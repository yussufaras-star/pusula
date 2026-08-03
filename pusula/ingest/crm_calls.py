"""Zoho CRM Calls → events ingester.

CRM'deki çağrı metadata'sını (temsilci notu, süre, sonuç, kimlik)
çeker; transkript üretmez. body = Description (temsilci notu),
body_quality = high.

FIELD_MAP canlı Calls inspect ile doğrulandı. Call_Type ve sonuç
alanları Türkçe değer taşır; eşleme sözlükleri TR+EN kapsar.
Voice_Recording__s phonebridge URL'sidir — mevcut OAuth ile
indirilemez; meta'da referans olarak tutulur.

Delta: COQL where Modified_Time > since. $se_module COQL'de yok;
What_Id doğrudan zoho_lead_id sayılır (çağrılar lead'e bağlı).
Telefon kimliği: Phone/Phone_2/Mobile alanları, yoksa Subject'ten
(+90…) ayıklama; Caller_ID/Dialled_Number santral-dahili — sadece meta.
Scheduled_In_CRM='True' → channel=call + meta.scheduled + commitments.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_phone
from pusula.db.models import Direction, Event
from pusula.ingest.base import IngestResult, Ingester, RawRecord, to_istanbul
from pusula.ingest.lead_identity import sync_lead_identities
from pusula.ingest.registry import register
from pusula.zoho.crm import coql

logger = logging.getLogger(__name__)

# Zoho Calls API alan adları. Mapping düzeltmeleri sadece buradan.
# $se_module COQL kolonu değildir — select listesine konmaz.
FIELD_MAP: dict[str, str] = {
    "id": "id",
    "modified_time": "Modified_Time",
    "call_start_time": "Call_Start_Time",
    "call_type": "Call_Type",
    "description": "Description",
    "owner": "Owner",
    "duration_sec": "Call_Duration_in_seconds",  # integer; snapshot doğrulandı
    # Saniye alanı boşsa metin süre (MM:SS / HH:MM:SS).
    "duration_text": "Call_Duration",  # text; snapshot doğrulandı
    "call_result": "Call_Result",
    # Gelen aramalarda Call_Result yerine dolu olabilen özel alan.
    "inbound_call_result": "Gelen_Arama_Sonucu",
    "subject": "Subject",
    "call_purpose": "Call_Purpose",
    "who_id": "Who_Id",
    "what_id": "What_Id",
    "outgoing_call_status": "Outgoing_Call_Status",
    "voice_recording": "Voice_Recording__s",
    # Planlanmış randevu (string "True"/"False", boolean değil).
    "scheduled_in_crm": "Scheduled_In_CRM",
    "telephony_external_id": "Telephony_External_ID__s",
    "record_status": "Record_Status__s",
    # Santral/dahili — kimlik çözümlemede KULLANILMAZ, sadece meta.
    "extension": "Caller_ID",
    "dialled": "Dialled_Number",
}

# Gerçek telefon alanları (Calls snapshot'ta yok; varsa payload'dan okunur).
# COQL select'e eklenmez — olmayan kolon tüm fetch'i düşürür.
_PHONE_API_NAMES: tuple[str, ...] = ("Phone", "Phone_2", "Mobile")
# Subject: "İsim (+905xxxxxxxxx)'e giden arama" / "'den gelen arama"
_SUBJECT_PHONE_RE = re.compile(r"\+90[0-9]{10}")

# ---------------------------------------------------------------------------
# Picklist eşlemeleri (modül sabitleri). Anahtarlar casefold.
# Call_Type → direction koda gömülü (az ve sabit).
# Outcome → call_outcomes (Call_Result → Gelen_Arama_Sonucu).
# Call status → call_statuses (Outgoing_Call_Status; teknik, satış sonucu değil).
# Bilinmeyen değer: WARNING (bir kez) + varsayılana DÜŞME; kayıt ATLANMAZ.
# ---------------------------------------------------------------------------

DIRECTION_BY_CALL_TYPE: dict[str, Direction] = {
    "giden": "outbound",
    "outbound": "outbound",
    "gelen": "inbound",
    "inbound": "inbound",
    "cevapsız": "inbound",
    "cevapsiz": "inbound",
    "missed": "inbound",
}

# Satış sonucu: Outgoing_Call_Status dahil DEĞİL.
OUTCOME_FIELD_KEYS: tuple[str, ...] = (
    "call_result",
    "inbound_call_result",
)


@register
class CrmCallsIngester(Ingester):
    """Zoho CRM Calls modülünden satış çağrılarını events'e yazar."""

    source_name = "zoho_crm_calls"
    channel = "call"

    def __init__(self) -> None:
        # rep_id -> (category, active); fetch başında bir kez dolar.
        self._reps: dict[str, tuple[str, bool]] | None = None
        # raw_value.casefold() -> {outcome_key, category, is_progress}
        self._outcomes: dict[str, dict[str, Any]] | None = None
        # raw_value.casefold() -> {status_key}
        self._statuses: dict[str, dict[str, Any]] | None = None
        # Bilinmeyen picklist: bir kez uyar, adet tut.
        self._warned_outcomes: set[str] = set()
        self._warned_statuses: set[str] = set()
        self.unknown_outcomes: dict[str, int] = {}
        self.unknown_statuses: dict[str, int] = {}
        # run() skip_reasons / sample_skipped için (base okur).
        self.last_skip_reason: str | None = None
        self.last_skip_sample: dict[str, Any] | None = None
        # run_ingest --limit ile set edilir; None = sınırsız.
        self.fetch_limit: int | None = None
        # run_ingest --debug-query: COQL'i ekrana bas.
        self.debug_query: bool = False
        self.last_coql_query: str | None = None
        # Bu turda görülen lead id'ler; run sonunda batch zenginleştirilir.
        self._seen_lead_ids: set[str] = set()
        self.lead_identity_stats: dict[str, int] | None = None

    def run(self, since: datetime | None = None, dry_run: bool = False) -> IngestResult:
        """Şablon run + tur sonu lead kimlik batch zenginleştirmesi."""
        self._seen_lead_ids = set()
        self.lead_identity_stats = None
        self._warned_outcomes = set()
        self._warned_statuses = set()
        self.unknown_outcomes = {}
        self.unknown_statuses = {}
        result = super().run(since=since, dry_run=dry_run)
        if self.unknown_outcomes:
            logger.info(
                "bilinmeyen outcome: %s",
                {k: v for k, v in sorted(self.unknown_outcomes.items())},
            )
        if self.unknown_statuses:
            logger.info(
                "bilinmeyen call_status: %s",
                {k: v for k, v in sorted(self.unknown_statuses.items())},
            )
        if dry_run:
            self.lead_identity_stats = {
                "processed": 0,
                "phones_added": 0,
                "emails_added": 0,
                "leads_written": 0,
                "errors": 0,
                "leads_seen": len(self._seen_lead_ids),
            }
            return result
        if self._seen_lead_ids:
            stats = sync_lead_identities(self._seen_lead_ids)
            stats["leads_seen"] = len(self._seen_lead_ids)
            self.lead_identity_stats = stats
        else:
            self.lead_identity_stats = {
                "processed": 0,
                "phones_added": 0,
                "emails_added": 0,
                "leads_written": 0,
                "errors": 0,
                "leads_seen": 0,
            }
        return result

    def fetch(self, since: datetime | None) -> Iterator[RawRecord]:
        """Calls delta: COQL Modified_Time > since; Modified_Time artan.

        Watermark RawRecord.occurred_at = Modified_Time (filtreyle aynı eksen).
        Event.occurred_at to_event'te Call_Start_Time kalır.
        """
        self._reps = _load_reps()
        self._outcomes = _load_call_outcomes()
        self._statuses = _load_call_statuses()

        fields = list(dict.fromkeys(FIELD_MAP.values()))
        query = f"select {', '.join(fields)} from Calls"
        if since is not None:
            since_str = _format_coql_datetime(since)
            query += f" where {FIELD_MAP['modified_time']} > '{since_str}'"
        query += f" order by {FIELD_MAP['modified_time']} asc"

        self.last_coql_query = query
        logger.debug("zoho_crm_calls COQL: %s", query)
        if self.debug_query:
            print(f"COQL: {query}")

        yielded = 0
        for record in coql(query):
            call_id = record.get(FIELD_MAP["id"])
            if not call_id:
                logger.debug("Calls kaydı id eksik, atlandı")
                continue
            # Watermark / sıralama ekseni: Modified_Time (since filtresiyle aynı).
            modified_raw = record.get(FIELD_MAP["modified_time"])
            occurred_at = _parse_zoho_datetime(modified_raw)
            if occurred_at is None:
                occurred_at = _parse_zoho_datetime(
                    record.get(FIELD_MAP["call_start_time"])
                )
            if occurred_at is None:
                logger.debug("Calls %s: zaman alanı yok, atlandı", call_id)
                continue
            yield RawRecord(
                source_ref=str(call_id),
                occurred_at=occurred_at,
                payload=record,
            )
            yielded += 1
            if self.fetch_limit is not None and yielded >= self.fetch_limit:
                return

    def to_event(self, raw: RawRecord) -> Event | None:
        """Ham Calls kaydını Event'e çevirir; atlanacaksa None (+ skip reason)."""
        self.last_skip_reason = None
        self.last_skip_sample = None
        payload = raw.payload
        f = FIELD_MAP

        start_raw = payload.get(f["call_start_time"])
        occurred_at = _parse_zoho_datetime(start_raw)
        if occurred_at is None:
            return self._skip("no_start_time", payload)

        owner = payload.get(f["owner"])
        if not isinstance(owner, dict) or not owner.get("id"):
            return self._skip("no_rep_id", payload)
        rep_id = str(owner["id"])

        reps = self._reps if self._reps is not None else _load_reps()
        self._reps = reps
        rep_info = reps.get(rep_id)
        if rep_info is None:
            return self._skip("rep_not_found", payload)
        category, active = rep_info
        if category != "sales" or not active:
            return self._skip("rep_not_sales", payload)

        duration_sec, duration_source = _resolve_duration(payload)
        call_result = payload.get(f["call_result"])
        inbound_result = payload.get(f["inbound_call_result"])
        outgoing_status = payload.get(f["outgoing_call_status"])
        outcome_key, raw_outcome = self._resolve_outcome(
            *(payload.get(f[key]) for key in OUTCOME_FIELD_KEYS)
        )
        status_key, raw_status = self._resolve_call_status(outgoing_status)

        direction, raw_call_type = _map_direction(payload.get(f["call_type"]))
        who = payload.get(f["who_id"])
        what = payload.get(f["what_id"])

        zoho_contact_id = _lookup_id(who)
        # $se_module COQL'de yok; çağrılar lead'e bağlı — What_Id = lead.
        zoho_lead_id = _lookup_id(what)
        if zoho_lead_id is not None:
            self._seen_lead_ids.add(zoho_lead_id)

        # Caller_ID / Dialled_Number santral-dahili; kimlikte kullanılmaz.
        extension = payload.get(f["extension"])
        dialled = payload.get(f["dialled"])

        phone, phone_source = _extract_call_phone(payload)
        scheduled = _is_scheduled(payload.get(f["scheduled_in_crm"]))
        subject = payload.get(f["subject"])

        meta: dict[str, Any] = {
            "call_duration_sec": duration_sec,
            # Eski anahtar; okuyucular geçene kadar aynı değer.
            "duration_sec": duration_sec,
            "duration_source": duration_source,
            "outcome_key": outcome_key,
            "call_status": status_key if status_key is not None else raw_status,
            # Sonuç alanları ayrı tutulur (gelen çağrıda Call_Result boş olabilir).
            "call_result": call_result,
            "gelen_arama_sonucu": inbound_result,
            "subject": subject,
            "call_purpose": payload.get(f["call_purpose"]),
            "who_id": who,
            "what_id": what,
            "owner_name": owner.get("name"),
            "voice_recording_url": payload.get(f["voice_recording"]),
            "telephony_external_id": payload.get(f["telephony_external_id"]),
            "record_status": payload.get(f["record_status"]),
            "extension": extension,
            "dialled": dialled,
        }
        if scheduled:
            meta["scheduled"] = True
        if phone_source is not None:
            meta["phone_source"] = phone_source
        if outcome_key is None and raw_outcome is not None:
            meta["raw_outcome"] = raw_outcome
        if status_key is None and raw_status is not None:
            meta["raw_call_status"] = raw_status
        if raw_call_type is not None:
            meta["raw_call_type"] = raw_call_type

        return Event(
            # Planlanmış arama da call kanalında; meeting Bookings'e ayrılır.
            channel="call",
            direction=direction,
            rep_id=rep_id,
            occurred_at=occurred_at,
            source_ref=raw.source_ref,
            body=payload.get(f["description"]),
            body_quality="high",
            meta=meta,
            phone=phone,
            zoho_lead_id=zoho_lead_id,
            zoho_contact_id=zoho_contact_id,
        )

    def on_event_upserted(
        self,
        conn: Any,
        event: Event,
        event_id: int,
        created: bool,
    ) -> None:
        """Planlanmış arama → commitments (Subject, Call_Start_Time, open)."""
        meta = event.meta or {}
        if not meta.get("scheduled"):
            return
        subject = meta.get("subject")
        text = str(subject).strip() if subject is not None else None
        if text == "":
            text = None
        client.upsert_open_commitment(
            conn,
            thread_id=event.thread_id,
            source_event_id=event_id,
            text=text,
            due_at=event.occurred_at,
        )

    def _resolve_outcome(self, *candidates: Any) -> tuple[str | None, str | None]:
        """Call_Result → Gelen_Arama_Sonucu → (outcome_key, ham).

        Outgoing_Call_Status burada yok (teknik durum). Bilinmeyende
        WARNING (bir kez), outcome_key=None; kayıt atlanmaz.
        """
        raw = _first_nonempty(*candidates)
        if raw is None:
            return None, None
        text = str(raw).strip()
        outcomes = self._outcomes if self._outcomes is not None else _load_call_outcomes()
        self._outcomes = outcomes
        row = outcomes.get(text.casefold())
        if row is not None:
            return str(row["outcome_key"]), text
        self.unknown_outcomes[text] = self.unknown_outcomes.get(text, 0) + 1
        if text not in self._warned_outcomes:
            self._warned_outcomes.add(text)
            logger.warning("bilinmeyen outcome picklist değeri: %r", text)
        return None, text

    def _resolve_call_status(self, raw: Any) -> tuple[str | None, str | None]:
        """Outgoing_Call_Status → (status_key, ham); satış sonucu değil."""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None, None
        text = str(raw).strip()
        statuses = self._statuses if self._statuses is not None else _load_call_statuses()
        self._statuses = statuses
        row = statuses.get(text.casefold())
        if row is not None:
            return str(row["status_key"]), text
        self.unknown_statuses[text] = self.unknown_statuses.get(text, 0) + 1
        if text not in self._warned_statuses:
            self._warned_statuses.add(text)
            logger.warning("bilinmeyen call_status picklist değeri: %r", text)
        return None, text

    def _skip(self, reason: str, payload: dict[str, Any]) -> Event | None:
        """Atlamanın sebebini ve teşhis örneğini base.run için işaretler."""
        self.last_skip_reason = reason
        self.last_skip_sample = _skip_sample(payload)
        return None


def _skip_sample(payload: dict[str, Any]) -> dict[str, Any]:
    """Kişisel veri olmadan atlanan kayıt teşhis alanları."""
    owner = payload.get(FIELD_MAP["owner"])
    owner_id = owner.get("id") if isinstance(owner, dict) else None
    owner_name = owner.get("name") if isinstance(owner, dict) else None
    start = payload.get(FIELD_MAP["call_start_time"])
    return {
        "id": payload.get(FIELD_MAP["id"]),
        "Call_Start_Time_present": bool(start),
        "Owner.id": owner_id,
        "Owner.name": owner_name,
        "Call_Duration_in_seconds": payload.get(FIELD_MAP["duration_sec"]),
        "Call_Duration": payload.get(FIELD_MAP["duration_text"]),
        "Call_Result": payload.get(FIELD_MAP["call_result"]),
        "Outgoing_Call_Status": payload.get(FIELD_MAP["outgoing_call_status"]),
        "Call_Type": payload.get(FIELD_MAP["call_type"]),
    }


def _extract_call_phone(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Telefonu alanlardan, yoksa Subject'ten çıkarır.

    Dönüş: (normalize edilmiş numara, phone_source).
    phone_source: "field" | "subject" | None.
    Subject'te birden fazla +90... varsa belirsiz — hiçbiri alınmaz.
    Subject numarası blocked_identifiers'daysa kimlik sayılmaz.
    """
    for api_name in _PHONE_API_NAMES:
        raw = payload.get(api_name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        normalized = normalize_phone(str(raw))
        if normalized is None:
            continue
        if _phone_blocked(normalized):
            logger.info(
                "Calls telefon alanı blocked_identifiers'da, yok sayıldı (%s)",
                api_name,
            )
            continue
        return normalized, "field"

    subject = payload.get(FIELD_MAP["subject"])
    if not isinstance(subject, str) or not subject.strip():
        return None, None
    matches = _SUBJECT_PHONE_RE.findall(subject)
    if len(matches) > 1:
        logger.info(
            "Subject'te birden fazla telefon, belirsiz — alınmadı: %s",
            matches,
        )
        return None, None
    if len(matches) != 1:
        return None, None
    normalized = normalize_phone(matches[0])
    if normalized is None:
        return None, None
    if _phone_blocked(normalized):
        logger.info(
            "Subject telefonu blocked_identifiers'da, kimlik sayılmadı: %s",
            normalized,
        )
        return None, None
    return normalized, "subject"


def _phone_blocked(normalized: str) -> bool:
    """Normalize edilmiş telefon blocked_identifiers'da mı (cache)."""
    return client.is_identifier_blocked("phone", normalized)


def _is_scheduled(raw: Any) -> bool:
    """Scheduled_In_CRM string 'True' mu (büyük/küçük harf duyarsız)."""
    if raw is None:
        return False
    return str(raw).strip().casefold() == "true"


def _load_reps() -> dict[str, tuple[str, bool]]:
    """Tüm rep_id → (category, active) eşlemesini tek sorguda çeker."""
    query = """
        SELECT rep_id, category, active FROM reps
        WHERE org_id = %s
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(),)).fetchall()
    return {str(row[0]): (str(row[1]), bool(row[2])) for row in rows}


def _load_call_outcomes() -> dict[str, dict[str, Any]]:
    """call_outcomes tablosunu raw_value.casefold() anahtarıyla cache'ler."""
    query = """
        SELECT raw_value, outcome_key, category, is_progress
        FROM call_outcomes
        WHERE org_id = %s
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(),)).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for raw_value, outcome_key, category, is_progress in rows:
        result[str(raw_value).casefold()] = {
            "outcome_key": str(outcome_key),
            "category": str(category),
            "is_progress": bool(is_progress),
            "raw_value": str(raw_value),
        }
    return result


def _load_call_statuses() -> dict[str, dict[str, Any]]:
    """call_statuses tablosunu raw_value.casefold() anahtarıyla cache'ler."""
    query = """
        SELECT raw_value, status_key
        FROM call_statuses
        WHERE org_id = %s
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(),)).fetchall()
    return {
        str(raw_value).casefold(): {
            "status_key": str(status_key),
            "raw_value": str(raw_value),
        }
        for raw_value, status_key in rows
    }


def _parse_zoho_datetime(value: Any) -> datetime | None:
    """Zoho ISO-8601 zamanını Europe/Istanbul datetime'a çevirir."""
    if isinstance(value, datetime):
        return to_istanbul(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return to_istanbul(datetime.fromisoformat(text))
    except ValueError:
        return None


def _format_coql_datetime(value: datetime) -> str:
    """Zoho COQL tarih biçimi: yyyy-MM-ddTHH:mm:ss+03:00."""
    value = to_istanbul(value)
    offset = value.strftime("%z")  # +0300
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return value.strftime("%Y-%m-%dT%H:%M:%S") + offset


def _resolve_duration(payload: dict[str, Any]) -> tuple[int | None, str]:
    """Süreyi saniye + kaynağa çevirir.

    Önce Call_Duration_in_seconds; yoksa Call_Duration metnini
    (MM:SS / HH:MM:SS) parse eder. İkisi de yoksa (None, "unknown").
    """
    raw_sec = payload.get(FIELD_MAP["duration_sec"])
    if raw_sec is not None and raw_sec != "":
        try:
            return int(float(raw_sec)), "seconds_field"
        except (TypeError, ValueError):
            pass

    parsed = _parse_duration_text(payload.get(FIELD_MAP["duration_text"]))
    if parsed is not None:
        return parsed, "text_parsed"
    return None, "unknown"


def _parse_duration_text(value: Any) -> int | None:
    """'MM:SS' veya 'HH:MM:SS' metnini saniyeye çevirir."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 2:
        minutes, seconds = nums
        return minutes * 60 + seconds
    hours, minutes, seconds = nums
    return hours * 3600 + minutes * 60 + seconds


def _first_nonempty(*values: Any) -> Any | None:
    """İlk dolu (None/boş string olmayan) değeri döner."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _map_direction(call_type: Any) -> tuple[Direction | None, str | None]:
    """Call_Type → direction.

    Bilinmeyen picklist: WARNING, direction=None, raw_call_type=ham değer.
    Sessizce outbound varsayılmaz.
    """
    if not isinstance(call_type, str) or not call_type.strip():
        return None, None
    raw = call_type.strip()
    mapped = DIRECTION_BY_CALL_TYPE.get(raw.casefold())
    if mapped is not None:
        return mapped, None
    logger.warning("bilinmeyen Call_Type picklist değeri: %r", raw)
    return None, raw


def _lookup_id(lookup: Any) -> str | None:
    """Zoho lookup nesnesinden id çıkarır."""
    if isinstance(lookup, dict):
        value = lookup.get("id")
        return str(value) if value else None
    return None
