"""Ingester arayüzü: tüm veri kaynaklarının uyacağı ortak iskelet.

Somut ingester'lar (Verimor çağrı, CRM Calls, CRM Events, Zoho Mail,
WhatsApp webhook) Ingester'dan türer ve sadece fetch ile to_event
yazar. Akış (watermark okuma, kimlik çözümleme, idempotent yazma,
thread dokunuşu, watermark güncelleme) run() şablon metodundadır ve
alt sınıflar tarafından override edilmez.

Watermark sözleşmesi: fetch kayıtları occurred_at artan sırada
vermelidir; run başarıyla biterse watermark en son işlenen kaydın
occurred_at değerine çekilir (şu anki zamana değil). org_id her yerde
config üzerinden gelir (client ve resolve_thread içinde okunur).
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

from pusula.db import client
from pusula.db.identity import resolve_thread_detailed
from pusula.db.models import Event, SyncState

logger = logging.getLogger(__name__)

ISTANBUL = ZoneInfo("Europe/Istanbul")

# Bu kadar ardışık to_event hatası kaynağın kendisinde sorun demektir;
# tek tek kayıt atlamak yerine run durdurulur.
_MAX_CONSECUTIVE_FAILURES = 20
# IngestResult.errors'a yazılacak en fazla hata mesajı sayısı.
_MAX_ERRORS = 10
# dry_run'da döndürülecek örnek Event sayısı.
_MAX_SAMPLE_EVENTS = 5
# dry_run'da atlanan kayıtlardan tutulacak örnek sayısı.
_MAX_SAMPLE_SKIPPED = 3


class IngestError(RuntimeError):
    """Run yarıda kesildiğinde fırlatılır; watermark güncellenmemiştir."""


def to_istanbul(value: datetime) -> datetime:
    """Zaman damgasını Europe/Istanbul'a çevirir; naive değer Istanbul sayılır."""
    if value.tzinfo is None:
        return value.replace(tzinfo=ISTANBUL)
    return value.astimezone(ISTANBUL)


class RawRecord(BaseModel):
    """Kaynaktan gelen ham kayıt; to_event'in girdisi."""

    source_ref: str
    occurred_at: datetime
    payload: dict[str, Any]  # kaynağın ham verisi

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_istanbul(cls, value: datetime) -> datetime:
        # Tüm zaman damgaları Europe/Istanbul taşınır (DB'ye timestamptz).
        return to_istanbul(value)


class IngestResult(BaseModel):
    """Bir run'ın özeti.

    dry_run'da inserted "yazılacak olan" sayısıdır; DB'ye gidilmediği
    için duplicated hep 0 kalır ve sample_events ilk 5 Event'i taşır.
    skip_reasons: to_event'in last_skip_reason ile bildirdiği sebepler.
    sample_skipped: dry_run'da sample_events boşken teşhis için ilk
    atlanan kayıt özetleri (ingester last_skip_sample doldurur).
    """

    source_name: str
    channel: str
    fetched: int = 0
    inserted: int = 0
    duplicated: int = 0
    skipped: int = 0
    failed: int = 0
    watermark_before: datetime | None = None
    watermark_after: datetime | None = None
    errors: list[str] = Field(default_factory=list)  # ilk 10 hata mesajı
    sample_events: list[Event] = Field(default_factory=list)  # sadece dry_run
    skip_reasons: dict[str, int] = Field(default_factory=dict)
    sample_skipped: list[dict[str, Any]] = Field(default_factory=list)


class Ingester(ABC):
    """Tüm ingester'ların temel sınıfı.

    Alt sınıf sadece source_name, channel, fetch ve to_event tanımlar.
    """

    source_name: str  # sync_state'te kullanılacak anahtar
    channel: str  # call | email | whatsapp | meeting | note | task

    @abstractmethod
    def fetch(self, since: datetime | None) -> Iterator[RawRecord]:
        """Kaynaktan ham kayıtları çeker. Sayfalama ingester'ın işi.

        Kayıtlar occurred_at artan sırada verilmelidir; watermark
        en son işlenen kaydın occurred_at değerine çekilir.
        """

    @abstractmethod
    def to_event(self, raw: RawRecord) -> Event | None:
        """Ham kaydı Event'e çevirir. Kayıt atlanacaksa None döner.

        resolve_thread için gereken kimlik alanlarını (phone, email,
        zoho_lead_id, zoho_contact_id) ham kayıttan çıkarıp Event'in
        geçici alanlarına yazar; bu alanlar DB'ye yazılmaz.
        """

    def run(self, since: datetime | None = None, dry_run: bool = False) -> IngestResult:
        """Şablon metot; alt sınıflar override etmez.

        Akış: watermark oku -> fetch -> to_event -> resolve_thread ->
        insert_event -> thread dokunuşu -> watermark güncelle.

        - resolve_thread tanımlayıcı bulamazsa None döner; yeni thread
          açılmaz. Event thread_id=None ile yazılır (şema nullable).
        - Thread yaratma ile event yazma aynı transaction'dadır; insert
          ON CONFLICT ile hiçbir şey yazmazsa yeni thread geri alınır.
        - Thread dokunuşunda owner_rep_id = event.rep_id yazılır.
        - Tek kayıt hatası run'ı düşürmez: hata loglanır, kayıt failed
          sayılır, devam edilir. _MAX_CONSECUTIVE_FAILURES aşılırsa
          IngestError yükselir.
        - Watermark sadece run'ın TAMAMI başarılıysa (failed == 0)
          güncellenir; yarıda hata watermark'ı ilerletmez, bir sonraki
          run aynı aralığı yeniden çeker (insert idempotent).
        - dry_run=True: DB'ye hiçbir şey yazılmaz; sayılar ve ilk 5
          Event döner.
        """
        # a) since verilmemişse sync_state'ten watermark oku.
        watermark_before = since
        if watermark_before is None:
            state = client.get_sync_state(self.source_name)
            watermark_before = state.last_synced_at if state is not None else None

        result = IngestResult(
            source_name=self.source_name,
            channel=self.channel,
            watermark_before=watermark_before,
            watermark_after=watermark_before,
        )
        consecutive_failures = 0
        last_processed_at: datetime | None = None

        # b) fetch(since) ile kayıtları al.
        for raw in self.fetch(watermark_before):
            result.fetched += 1

            # c) to_event; tek kayıt hatası run'ı düşürmesin.
            try:
                event = self.to_event(raw)
            except Exception as exc:
                result.failed += 1
                consecutive_failures += 1
                if len(result.errors) < _MAX_ERRORS:
                    result.errors.append(f"{raw.source_ref}: {exc}")
                logger.exception(
                    "%s: kayıt dönüştürülemedi (source_ref=%s)",
                    self.source_name,
                    raw.source_ref,
                )
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    raise IngestError(
                        f"{self.source_name}: {consecutive_failures} ardışık hata, "
                        f"run durduruldu (fetched={result.fetched})"
                    ) from exc
                continue
            consecutive_failures = 0

            if event is None:
                result.skipped += 1
                reason = getattr(self, "last_skip_reason", None)
                if isinstance(reason, str) and reason:
                    result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1
                if dry_run and len(result.sample_skipped) < _MAX_SAMPLE_SKIPPED:
                    sample = getattr(self, "last_skip_sample", None)
                    if isinstance(sample, dict):
                        result.sample_skipped.append(sample)
                self.last_skip_reason = None
                self.last_skip_sample = None
                last_processed_at = raw.occurred_at
                continue

            if dry_run:
                result.inserted += 1
                if len(result.sample_events) < _MAX_SAMPLE_EVENTS:
                    result.sample_events.append(event)
                last_processed_at = raw.occurred_at
                continue

            # d–f) resolve + insert + touch aynı transaction'da.
            # ON CONFLICT'te yeni açılan thread geri alınır (orphan sızıntısı yok).
            # Tanımlayıcı yoksa thread_id None; şema nullable — event yine yazılır.
            try:
                with client.transaction() as conn:
                    thread_id, created_new = resolve_thread_detailed(
                        phone=event.phone,
                        email=event.email,
                        zoho_lead_id=event.zoho_lead_id,
                        zoho_contact_id=event.zoho_contact_id,
                        conn=conn,
                    )
                    event.thread_id = thread_id
                    if thread_id is None:
                        logger.info(
                            "%s: geçerli kimlik yok, thread açılmadı (source_ref=%s)",
                            self.source_name,
                            raw.source_ref,
                        )
                    event_id = client.insert_event(event, conn=conn)
                    if event_id is None:
                        result.duplicated += 1
                        if created_new and thread_id is not None:
                            client.clear_thread_identities(conn, thread_id)
                            client.delete_thread(conn, thread_id)
                    else:
                        result.inserted += 1
                        if thread_id is not None:
                            client.touch_thread(
                                thread_id,
                                self.channel,
                                event.occurred_at,
                                owner_rep_id=event.rep_id,
                                conn=conn,
                            )
            except Exception as exc:
                result.failed += 1
                consecutive_failures += 1
                if len(result.errors) < _MAX_ERRORS:
                    result.errors.append(f"{raw.source_ref}: {exc}")
                logger.exception(
                    "%s: yazma başarısız (source_ref=%s)",
                    self.source_name,
                    raw.source_ref,
                )
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    raise IngestError(
                        f"{self.source_name}: {consecutive_failures} ardışık hata, "
                        f"run durduruldu (fetched={result.fetched})"
                    ) from exc
                continue
            consecutive_failures = 0
            last_processed_at = raw.occurred_at

        # g) Watermark yalnızca tamamı başarılıysa en son işlenen kaydın
        # occurred_at değerine çekilir. h) Hata yükselirse buraya
        # gelinmez, watermark olduğu gibi kalır.
        if not dry_run and result.failed == 0 and last_processed_at is not None:
            client.set_sync_state(
                SyncState(source_name=self.source_name, last_synced_at=last_processed_at)
            )
            result.watermark_after = last_processed_at
        return result
