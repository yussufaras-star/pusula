"""Görüşme Analizleri: haftanın telefon görüşmeleri ve toplantıları.

Akış (scripts/conversation_analysis.py, GitHub Actions):
1. topla      CRM çağrıları (events, kanal call) → Verimor call_uuid; Zoho
              Meeting kayıtları → meeting_sessions ile temsilci. conv_items.
2. yazıya dök kaydı indir, konuşma-tanımadan geçir (pusula/transcribe.py).
3. analiz     her görüşme için Claude: özet, itirazlar, beş başlıkta puan,
              sonraki adım, kanıt alıntısıyla güçlü/gelişim maddeleri.
4. haftalık   sayılar koddan (aggregate); Claude yalnız temsilci başına tek
              odak maddesini ve ekip özetini cümleye döker.

Ilkeler (ROADMAP): kanıtsız iddia yok, her tespit ≤15 kelimelik alıntıyla;
karşı-olgusal yüzde yok; CRM'e yazılmaz, sonuç yalnız panelde görünür.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pusula import llm, recordings, transcribe
from pusula.config import get_org_id
from pusula.metrics import TZ
from pusula.occupancy import MATCH_MIN
from pusula.panel_data import connect

logger = logging.getLogger(__name__)

# Bu süreden kısa çağrı analiz edilmez (ulaşılamadı, sesli mesaj, "müsait değilim").
MIN_CALL_SEC = int(os.environ.get("PUSULA_CONV_MIN_CALL_SEC") or 60)
# Müşteri toplantıda bu kadar kalmadıysa kayıt analiz edilmez.
MIN_MEETING_CUSTOMER_SEC = 120
# Bu kadar kelimeden kısa transkript konuşma sayılmaz.
MIN_WORDS = 25
MAX_ATTEMPTS = 3
TEAM = "team"

SCORE_KEYS = ("acilis", "ihtiyac_analizi", "deger_sunumu", "itiraz_karsilama", "kapanis")
SCORE_LABELS = {
    "acilis": "Açılış",
    "ihtiyac_analizi": "İhtiyaç analizi",
    "deger_sunumu": "Değer sunumu",
    "itiraz_karsilama": "İtiraz karşılama",
    "kapanis": "Kapanış / sonraki adım",
}
TYPES = ("kesif", "sunum", "takip", "kapanis", "itiraz", "destek", "diger")
OBJECTIONS = ("fiyat", "zaman", "guven", "ihtiyac_yok", "rakip", "karar_verici", "odeme", "diger")
RISKS = ("yanlis_bilgi", "baski", "olmayan_soz", "uslup", "diger")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conv_items (
    id              bigserial PRIMARY KEY,
    org_id          text NOT NULL DEFAULT 'rexven',
    kind            text NOT NULL CHECK (kind IN ('call', 'meeting')),
    source_ref      text NOT NULL,
    event_id        bigint,
    rep_id          text,
    thread_id       text,
    lead_id         text,
    customer        text,
    direction       text,
    occurred_at     timestamptz NOT NULL,
    duration_sec    int,
    crm_outcome     text,
    meta            jsonb,
    status          text NOT NULL DEFAULT 'bekliyor'
        CHECK (status IN ('bekliyor', 'yazildi', 'analiz_edildi', 'hata', 'atlandi')),
    attempts        int NOT NULL DEFAULT 0,
    error           text,
    transcript      text,
    stt_provider    text,
    stt_model       text,
    speakers        boolean,
    transcribed_at  timestamptz,
    analysis        jsonb,
    ai_model        text,
    analyzed_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, kind, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_conv_items_time ON conv_items (org_id, occurred_at);
CREATE TABLE IF NOT EXISTS conv_weekly (
    org_id      text NOT NULL DEFAULT 'rexven',
    week_start  date NOT NULL,
    scope       text NOT NULL,
    stats       jsonb NOT NULL,
    narrative   jsonb,
    ai_model    text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, week_start, scope)
);
CREATE TABLE IF NOT EXISTS conv_runs (
    id           bigserial PRIMARY KEY,
    org_id       text NOT NULL DEFAULT 'rexven',
    week_start   date NOT NULL,
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz,
    report       jsonb
);
"""


def ensure_schema(conn: Any) -> None:
    conn.execute(SCHEMA_SQL)
    conn.commit()


# --- hafta --------------------------------------------------------------------


def week_start_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_bounds(week_start: date) -> tuple[datetime, datetime]:
    start = datetime.combine(week_start, datetime.min.time(), TZ)
    return start, start + timedelta(days=7)


# --- 1. topla -----------------------------------------------------------------


def call_row(event: dict[str, Any]) -> dict[str, Any] | None:
    """events satırı → conv_items satırı; kayıt yoksa veya çok kısaysa None."""
    meta = event.get("meta") or {}
    uuid = recordings.call_uuid(meta.get("voice_recording_url"))
    if uuid is None:
        return None
    seconds = int(float(meta.get("duration_sec") or 0))
    if seconds < MIN_CALL_SEC:
        return None
    what = meta.get("what_id") if isinstance(meta.get("what_id"), dict) else {}
    return {
        "kind": "call",
        "source_ref": uuid,
        "event_id": event.get("id"),
        "rep_id": event.get("rep_id"),
        "thread_id": event.get("thread_id"),
        "lead_id": what.get("id"),
        "customer": what.get("name"),
        "direction": event.get("direction"),
        "occurred_at": event["occurred_at"],
        "duration_sec": seconds,
        "crm_outcome": meta.get("call_result") or meta.get("gelen_arama_sonucu"),
        "meta": {"call_purpose": meta.get("call_purpose"), "subject": meta.get("subject")},
    }


def _insert_items(conn: Any, rows: Iterable[dict[str, Any]]) -> int:
    added = 0
    for row in rows:
        cur = conn.execute(
            """
            INSERT INTO conv_items (org_id, kind, source_ref, event_id, rep_id, thread_id, lead_id,
                                    customer, direction, occurred_at, duration_sec, crm_outcome, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (org_id, kind, source_ref) DO UPDATE SET
                rep_id = EXCLUDED.rep_id,
                thread_id = coalesce(EXCLUDED.thread_id, conv_items.thread_id),
                lead_id = coalesce(EXCLUDED.lead_id, conv_items.lead_id),
                customer = coalesce(EXCLUDED.customer, conv_items.customer),
                crm_outcome = coalesce(EXCLUDED.crm_outcome, conv_items.crm_outcome)
            RETURNING (xmax = 0)
            """,
            (
                get_org_id(), row["kind"], row["source_ref"], row.get("event_id"), row.get("rep_id"),
                row.get("thread_id"), row.get("lead_id"), row.get("customer"), row.get("direction"),
                row["occurred_at"], row.get("duration_sec"), row.get("crm_outcome"),
                json.dumps(row.get("meta") or {}, ensure_ascii=False, default=str),
            ),
        )
        fetched = cur.fetchone()
        added += 1 if fetched and fetched[0] else 0
    conn.commit()
    return added


def _collect_calls(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    cur = conn.execute(
        """
        SELECT e.id, e.rep_id, e.thread_id, e.direction, e.occurred_at, e.meta
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s AND e.channel = 'call'
          AND r.category = 'sales' AND r.active
          AND e.occurred_at >= %s AND e.occurred_at < %s
          AND e.meta->>'voice_recording_url' IS NOT NULL
        """,
        (get_org_id(), start, end),
    )
    cols = [c.name for c in cur.description]
    events = [dict(zip(cols, r)) for r in cur.fetchall()]
    rows = [r for r in (call_row(e) for e in events) if r is not None]
    return {"kayitli_cagri": len(events), "uygun": len(rows), "yeni": _insert_items(conn, rows)}


def _collect_meetings(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    if not recordings.meeting_enabled():
        return {"atlandi": "ZOHO_MEETING_REFRESH_TOKEN yok"}
    try:
        recs = recordings.list_meeting_recordings(start, end)
    except recordings.RecordingError as exc:
        return {"hata": str(exc)}
    rows: list[dict[str, Any]] = []
    skipped = Counter[str]()
    for rec in recs:
        key = str(rec.get("meetingKey") or "")
        found = conn.execute(
            f"""
            SELECT ms.rep_id, ms.topic, ms.planned_start, ms.customer_sec,
                   b.thread_id,
                   (SELECT l.full_name FROM leads l
                      WHERE l.org_id = b.org_id AND l.thread_id = b.thread_id
                      ORDER BY l.assigned_at DESC NULLS LAST LIMIT 1)
            FROM meeting_sessions ms
            JOIN reps r ON r.org_id = ms.org_id AND r.rep_id = ms.rep_id
            LEFT JOIN LATERAL (
                SELECT e.org_id, e.thread_id FROM events e
                WHERE e.org_id = ms.org_id AND e.channel = 'meeting' AND e.rep_id = ms.rep_id
                  AND e.occurred_at BETWEEN ms.planned_start - interval '{MATCH_MIN} minutes'
                                        AND ms.planned_start + interval '{MATCH_MIN} minutes'
                ORDER BY abs(extract(epoch FROM e.occurred_at - ms.planned_start))
                LIMIT 1
            ) b ON true
            WHERE ms.org_id = %s AND ms.meeting_key = %s AND r.category = 'sales' AND r.active
            """,
            (get_org_id(), key),
        ).fetchone()
        if not found:
            skipped["satis_disi_veya_eslesmedi"] += 1
            continue
        rep_id, topic, planned, customer_sec, thread_id, lead_name = found
        if float(customer_sec or 0) < MIN_MEETING_CUSTOMER_SEC:
            skipped["musteri_gelmedi"] += 1
            continue
        minutes = rec.get("durationInMins")
        seconds = int(float(minutes) * 60) if minutes not in (None, "") else int(float(customer_sec or 0))
        rows.append(
            {
                "kind": "meeting",
                "source_ref": str(rec.get("recordingId") or rec.get("id")),
                "rep_id": rep_id,
                "thread_id": thread_id,
                "customer": lead_name or _customer_from_topic(topic) or topic,
                "direction": "outbound",
                "occurred_at": recordings.recording_start(rec) or planned,
                "duration_sec": seconds,
                "meta": {
                    "meeting_key": key,
                    "topic": topic,
                    "download_url": rec.get("downloadUrl"),
                    "transcript_url": rec.get("transcriptionDownloadUrl"),
                },
            }
        )
    return {"kayit": len(recs), "uygun": len(rows), "atlanan": dict(skipped), "yeni": _insert_items(conn, rows)}


def _customer_from_topic(topic: str | None) -> str | None:
    if not topic or " with " not in topic:
        return None
    return topic.split(" with ", 1)[1].strip() or None


def collect(conn: Any, week_start: date) -> dict[str, Any]:
    start, end = week_bounds(week_start)
    return {"telefon": _collect_calls(conn, start, end), "toplanti": _collect_meetings(conn, start, end)}


# --- 2. yazıya dök --------------------------------------------------------------


def _pending(conn: Any, week_start: date, status: str, limit: int | None) -> list[dict[str, Any]]:
    start, end = week_bounds(week_start)
    cur = conn.execute(
        f"""
        SELECT c.*, r.full_name AS rep_name FROM conv_items c
        LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
        WHERE c.org_id = %s AND c.occurred_at >= %s AND c.occurred_at < %s
          AND (c.status = %s OR (c.status = 'hata' AND c.attempts < %s
               AND (%s = 'bekliyor') = (c.transcript IS NULL)))
        ORDER BY c.occurred_at
        {"LIMIT %s" if limit else ""}
        """,
        (get_org_id(), start, end, status, MAX_ATTEMPTS, status, *([limit] if limit else [])),
    )
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fail(conn: Any, item_id: int, error: str) -> None:
    conn.execute(
        "UPDATE conv_items SET status = 'hata', attempts = attempts + 1, error = %s WHERE id = %s",
        (error[:500], item_id),
    )
    conn.commit()


def word_count(text: str) -> int:
    return sum(
        len(line.split(":", 1)[1].split()) if line.startswith("Konuşmacı ") and ":" in line else len(line.split())
        for line in text.splitlines()
    )


def transcribe_pending(
    conn: Any, week_start: date, *, budget_sec: float | None = None, limit: int | None = None
) -> dict[str, Any]:
    if not transcribe.enabled():
        return {"atlandi": "konuşma-tanıma anahtarı yok (OPENAI/ELEVENLABS/DEEPGRAM)"}
    started = time.monotonic()
    report: dict[str, Any] = {"yazildi": 0, "atlandi": 0, "hata": 0}
    for item in _pending(conn, week_start, "bekliyor", limit):
        if budget_sec is not None and time.monotonic() - started > budget_sec:
            report["durdu"] = "sure_doldu"
            break
        try:
            with tempfile.TemporaryDirectory(prefix="pusula-kayit-") as tmp:
                if item["kind"] == "call":
                    path = recordings.download_call(item["source_ref"], Path(tmp) / "kayit.mp3")
                    result = transcribe.transcribe_file(path)
                else:
                    meta = item.get("meta") or {}
                    rec = {"downloadUrl": meta.get("download_url")}
                    path = recordings.download_meeting(rec, Path(tmp) / "kayit.mp4")
                    result = transcribe.transcribe_file(path, video=True)
        except (recordings.RecordingError, transcribe.TranscribeError, OSError) as exc:
            logger.warning("Kayıt %s yazıya dökülemedi: %s", item["id"], exc)
            _fail(conn, item["id"], str(exc))
            report["hata"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 — ffmpeg vb.; tek kayıt tüm turu düşürmesin
            logger.exception("Kayıt %s beklenmeyen hata", item["id"])
            _fail(conn, item["id"], f"{type(exc).__name__}: {exc}")
            report["hata"] += 1
            continue
        short = word_count(result.text) < MIN_WORDS
        conn.execute(
            """
            UPDATE conv_items SET transcript = %s, stt_provider = %s, stt_model = %s, speakers = %s,
                   transcribed_at = now(), status = %s, error = %s
            WHERE id = %s
            """,
            (
                result.text, result.provider, result.model, result.speakers,
                "atlandi" if short else "yazildi",
                "konuşma çok kısa" if short else None,
                item["id"],
            ),
        )
        conn.commit()
        report["atlandi" if short else "yazildi"] += 1
    return report


# --- 3. analiz ------------------------------------------------------------------

_QUOTE = {"type": "string", "description": "Transkriptten birebir, en fazla 15 kelime"}

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analiz_edilebilir": {"type": "boolean"},
        "neden": {"type": "string"},
        "ozet": {"type": "string"},
        "tur": {"type": "string", "enum": list(TYPES)},
        "temsilci_konusmaci": {"type": "string"},
        "musteri": {
            "type": "object",
            "properties": {
                "ihtiyaclar": {"type": "array", "items": {"type": "string"}},
                "ilgi": {"type": "integer"},
                "duygu": {"type": "string", "enum": ["olumlu", "notr", "olumsuz"]},
            },
            "required": ["ihtiyaclar", "ilgi", "duygu"],
            "additionalProperties": False,
        },
        "itirazlar": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tip": {"type": "string", "enum": list(OBJECTIONS)},
                    "alinti": _QUOTE,
                    "karsilandi": {"type": "string", "enum": ["evet", "kismen", "hayir"]},
                },
                "required": ["tip", "alinti", "karsilandi"],
                "additionalProperties": False,
            },
        },
        "puanlar": {
            "type": "object",
            "properties": {k: {"type": "integer"} for k in SCORE_KEYS},
            "required": list(SCORE_KEYS),
            "additionalProperties": False,
        },
        "guclu": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"madde": {"type": "string"}, "alinti": _QUOTE},
                "required": ["madde", "alinti"],
                "additionalProperties": False,
            },
        },
        "gelisim": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"madde": {"type": "string"}, "alinti": _QUOTE},
                "required": ["madde", "alinti"],
                "additionalProperties": False,
            },
        },
        "sonraki_adim": {
            "type": "object",
            "properties": {
                "var": {"type": "boolean"},
                "tarih_net": {"type": "boolean"},
                "aciklama": {"type": "string"},
            },
            "required": ["var", "tarih_net", "aciklama"],
            "additionalProperties": False,
        },
        "riskler": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tip": {"type": "string", "enum": list(RISKS)},
                    "aciklama": {"type": "string"},
                    "alinti": _QUOTE,
                },
                "required": ["tip", "aciklama", "alinti"],
                "additionalProperties": False,
            },
        },
        "sozler": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "analiz_edilebilir", "neden", "ozet", "tur", "temsilci_konusmaci", "musteri", "itirazlar",
        "puanlar", "guclu", "gelisim", "sonraki_adim", "riskler", "sozler",
    ],
    "additionalProperties": False,
}

ANALYSIS_SYSTEM = """Rexven'in satış ekibinin müşteri görüşmelerini inceleyen bir satış koçusun.
Rexven, e-ticaret (Amazon, Etsy, Shopify vb.) eğitim ve danışmanlık paketleri satar; ekip telefonda
ve Zoho Meeting'de görüşür. Sana tek görüşmenin otomatik transkripti verilir. Transkript
konuşma-tanımadan geldiği için yazım hataları ve yanlış konuşmacı ayrımı olabilir; anlamdan çıkar.

Kurallar:
- Kanıtsız iddia yazma. Her itiraz, güçlü yön, gelişim maddesi ve riskin yanında transkriptten
  birebir en fazla 15 kelimelik alıntı olsun. Alıntı bulamıyorsan maddeyi yazma.
- "Şunu yapsaydı satış %X artardı" gibi karşı-olgusal tahmin ve yüzde yazma.
- Transkript satış görüşmesi değilse (sesli mesaj, yanlış numara, yalnız selamlaşma, teknik
  destek dışı kısa konuşma) analiz_edilebilir=false yap, nedeni yaz, diğer alanları boş/0 bırak.
- Puanlar 1-5 tam sayı: 1 zayıf, 3 yeterli, 5 örnek alınacak. Görüşmede o aşama hiç yoksa
  (ör. itiraz gelmedi) 0 yaz; 0 ortalamaya girmez.
- musteri.ilgi 1-5: müşterinin satın alma ilgisi.
- temsilci_konusmaci: transkriptteki temsilci etiketi (ör. "Konuşmacı 1"); ayrım yoksa "belirsiz".
- sonraki_adim.var: görüşme net bir sonraki adımla bitti mi (randevu, ödeme, geri arama saati).
  tarih_net: gün/saat konuşuldu mu.
- sozler: temsilcinin müşteriye verdiği somut sözler (geri arama, belge gönderme vb.).
- riskler: yanlış/abartılı bilgi, baskıcı satış, verilemeyecek söz, kaba üslup. Yoksa boş.
- Türkçe, kısa ve somut yaz. özet en fazla 3 cümle; madde başına bir cümle."""


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(low, min(high, number))


def normalize_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    """Model çıktısını sınırlar içine alır (şema aralık kısıtı taşımaz)."""
    out = dict(raw)
    out["puanlar"] = {k: _clamp((raw.get("puanlar") or {}).get(k), 0, 5) for k in SCORE_KEYS}
    musteri = dict(raw.get("musteri") or {})
    musteri["ilgi"] = _clamp(musteri.get("ilgi"), 0, 5)
    out["musteri"] = musteri
    if out.get("tur") not in TYPES:
        out["tur"] = "diger"
    return out


def _prompt(item: dict[str, Any]) -> str:
    kind = "Telefon görüşmesi" if item["kind"] == "call" else "Zoho Meeting toplantısı"
    direction = {"outbound": "giden", "inbound": "gelen"}.get(item.get("direction") or "", "")
    when = item["occurred_at"].astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    lines = [
        f"Tür: {kind}{f' ({direction})' if direction and item['kind'] == 'call' else ''}",
        f"Temsilci: {item.get('rep_name') or 'bilinmiyor'}",
        f"Müşteri: {item.get('customer') or 'bilinmiyor'}",
        f"Tarih: {when}",
        f"Süre: {round((item.get('duration_sec') or 0) / 60, 1)} dk",
        f"Temsilcinin CRM'e girdiği sonuç: {item.get('crm_outcome') or 'girilmemiş'}",
        "Konuşmacı ayrımı: " + ("var" if item.get("speakers") else "yok, temsilciyi bağlamdan çıkar"),
        "",
        "<transkript>",
        item.get("transcript") or "",
        "</transkript>",
    ]
    return "\n".join(lines)


def analyze_pending(
    conn: Any, week_start: date, *, budget_sec: float | None = None, limit: int | None = None
) -> dict[str, Any]:
    if not llm.enabled():
        return {"atlandi": "ANTHROPIC_API_KEY yok"}
    started = time.monotonic()
    report: dict[str, Any] = {"analiz": 0, "hata": 0}
    for item in _pending(conn, week_start, "yazildi", limit):
        if budget_sec is not None and time.monotonic() - started > budget_sec:
            report["durdu"] = "sure_doldu"
            break
        raw = llm.complete_json(ANALYSIS_SYSTEM, _prompt(item), ANALYSIS_SCHEMA)
        if raw is None:
            _fail(conn, item["id"], "AI analizi alınamadı")
            report["hata"] += 1
            continue
        conn.execute(
            """
            UPDATE conv_items SET analysis = %s::jsonb, ai_model = %s, analyzed_at = now(),
                   status = 'analiz_edildi', error = NULL
            WHERE id = %s
            """,
            (json.dumps(normalize_analysis(raw), ensure_ascii=False), llm.model_name(), item["id"]),
        )
        conn.commit()
        report["analiz"] += 1
    return report


# --- 4. haftalık ------------------------------------------------------------------


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Analiz edilmiş görüşmelerden haftalık sayılar (saf)."""
    usable = [i for i in items if (i.get("analysis") or {}).get("analiz_edilebilir")]
    scores: dict[str, list[float]] = {k: [] for k in SCORE_KEYS}
    objections: dict[str, dict[str, int]] = defaultdict(lambda: {"adet": 0, "karsilandi": 0, "kismen": 0})
    types = Counter[str]()
    risks = Counter[str]()
    interest: list[float] = []
    next_step = dated = 0
    for item in usable:
        a = item["analysis"]
        for key in SCORE_KEYS:
            value = (a.get("puanlar") or {}).get(key) or 0
            if value > 0:
                scores[key].append(float(value))
        for obj in a.get("itirazlar") or []:
            row = objections[obj.get("tip") or "diger"]
            row["adet"] += 1
            if obj.get("karsilandi") == "evet":
                row["karsilandi"] += 1
            elif obj.get("karsilandi") == "kismen":
                row["kismen"] += 1
        for risk in a.get("riskler") or []:
            risks[risk.get("tip") or "diger"] += 1
        types[a.get("tur") or "diger"] += 1
        ilgi = (a.get("musteri") or {}).get("ilgi") or 0
        if ilgi > 0:
            interest.append(float(ilgi))
        step = a.get("sonraki_adim") or {}
        if step.get("var"):
            next_step += 1
            if step.get("tarih_net"):
                dated += 1
    score_avgs = {k: _avg(v) for k, v in scores.items()}
    present = [v for v in score_avgs.values() if v is not None]
    n = len(usable)
    return {
        "gorusme": len(items),
        "analiz": n,
        "telefon": sum(1 for i in usable if i.get("kind") == "call"),
        "toplanti": sum(1 for i in usable if i.get("kind") == "meeting"),
        "dakika": round(sum((i.get("duration_sec") or 0) for i in usable) / 60, 1),
        "puanlar": score_avgs,
        "puan_ort": _avg(present),
        "ilgi_ort": _avg(interest),
        "sonraki_adim_orani": round(100.0 * next_step / n, 1) if n else None,
        "tarihli_adim_orani": round(100.0 * dated / n, 1) if n else None,
        "itirazlar": {
            k: {**v, "oran": round(100.0 * (v["karsilandi"] + 0.5 * v["kismen"]) / v["adet"], 1)}
            for k, v in sorted(objections.items(), key=lambda kv: -kv[1]["adet"])
        },
        "turler": dict(types.most_common()),
        "riskler": dict(risks.most_common()),
    }


REP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ozet": {"type": "string"},
        "odak": {
            "type": "object",
            "properties": {
                "baslik": {"type": "string"},
                "neden": {"type": "string"},
                "alinti": _QUOTE,
                "nasil": {"type": "string"},
            },
            "required": ["baslik", "neden", "alinti", "nasil"],
            "additionalProperties": False,
        },
        "guclu": {"type": "array", "items": {"type": "string"}},
        "dikkat": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ozet", "odak", "guclu", "dikkat"],
    "additionalProperties": False,
}

TEAM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ozet": {"type": "string"},
        "musteri_sesi": {"type": "array", "items": {"type": "string"}},
        "itirazlar": {"type": "string"},
        "iyi_ornekler": {"type": "array", "items": {"type": "string"}},
        "aksiyonlar": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ozet", "musteri_sesi", "itirazlar", "iyi_ornekler", "aksiyonlar"],
    "additionalProperties": False,
}

WEEKLY_SYSTEM = """Rexven satış ekibinin haftalık görüşme koçusun. Sayıları kod hesapladı; sen
yalnız bunları ve görüşme notlarını okunur, eyleme dönük Türkçe cümlelere dökersin.

Kurallar:
- Sayıları değiştirme, yeni sayı uydurma. Karşı-olgusal tahmin ve yüzde yazma.
- Her iddia görüşme notlarındaki bir maddeye dayansın; alıntıları notlardan birebir al.
- Temsilci için tek odak maddesi seç: haftada en sık tekrar eden ve satışa en yakın gelişim alanı.
  "nasil": bir sonraki görüşmede söylenebilecek somut bir cümle ya da davranış.
- Kısa yaz: özet en fazla 3 cümle, liste maddeleri en fazla 4, her biri tek cümle.
- Ton: yargılamayan, saygılı, yapıcı. Temsilciyi başkasıyla kıyaslama."""


def _notes(items: list[dict[str, Any]], cap: int = 60) -> str:
    out: list[str] = []
    usable = [i for i in items if (i.get("analysis") or {}).get("analiz_edilebilir")]
    for item in usable[:cap]:
        a = item["analysis"]
        parts = [
            f"- [{'tel' if item['kind'] == 'call' else 'toplantı'} · {a.get('tur')} · "
            f"{round((item.get('duration_sec') or 0) / 60)} dk · {item.get('rep_name') or ''}] {a.get('ozet')}"
        ]
        for obj in a.get("itirazlar") or []:
            parts.append(f"  itiraz {obj.get('tip')} ({obj.get('karsilandi')}): \"{obj.get('alinti')}\"")
        for g in a.get("gelisim") or []:
            parts.append(f"  gelişim: {g.get('madde')} — \"{g.get('alinti')}\"")
        for g in a.get("guclu") or []:
            parts.append(f"  güçlü: {g.get('madde')} — \"{g.get('alinti')}\"")
        for need in (a.get("musteri") or {}).get("ihtiyaclar") or []:
            parts.append(f"  müşteri ihtiyacı: {need}")
        out.append("\n".join(parts))
    return "\n".join(out)


def _load_week_items(conn: Any, week_start: date) -> list[dict[str, Any]]:
    start, end = week_bounds(week_start)
    cur = conn.execute(
        """
        SELECT c.id, c.kind, c.rep_id, r.full_name AS rep_name, c.duration_sec, c.analysis, c.status
        FROM conv_items c LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
        WHERE c.org_id = %s AND c.occurred_at >= %s AND c.occurred_at < %s
          AND c.status = 'analiz_edildi'
        ORDER BY c.occurred_at
        """,
        (get_org_id(), start, end),
    )
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_weekly(conn: Any, week_start: date, *, narrate: bool = True) -> dict[str, Any]:
    items = _load_week_items(conn, week_start)
    by_rep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("rep_id"):
            by_rep[str(item["rep_id"])].append(item)
    scopes: list[tuple[str, list[dict[str, Any]]]] = [(TEAM, items), *sorted(by_rep.items())]
    written = 0
    for scope, rows in scopes:
        stats = aggregate(rows)
        narrative = None
        if narrate and llm.enabled() and stats["analiz"] > 0:
            stats_json = json.dumps(stats, ensure_ascii=False)
            if scope == TEAM:
                prompt = (
                    f"Ekip haftası ({week_start:%d.%m.%Y} haftası).\nSayılar:\n{stats_json}\n\n"
                    f"Görüşme notları:\n{_notes(rows, cap=120)}"
                )
                narrative = llm.complete_json(WEEKLY_SYSTEM, prompt, TEAM_SCHEMA)
            else:
                name = rows[0].get("rep_name") or scope
                prompt = (
                    f"Temsilci: {name} ({week_start:%d.%m.%Y} haftası).\nSayılar:\n{stats_json}\n\n"
                    f"Görüşme notları:\n{_notes(rows)}"
                )
                narrative = llm.complete_json(WEEKLY_SYSTEM, prompt, REP_SCHEMA)
        conn.execute(
            """
            INSERT INTO conv_weekly (org_id, week_start, scope, stats, narrative, ai_model, created_at)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, now())
            ON CONFLICT (org_id, week_start, scope) DO UPDATE SET
                stats = EXCLUDED.stats,
                narrative = coalesce(EXCLUDED.narrative, conv_weekly.narrative),
                ai_model = coalesce(EXCLUDED.ai_model, conv_weekly.ai_model),
                created_at = now()
            """,
            (
                get_org_id(), week_start, scope, json.dumps(stats, ensure_ascii=False),
                json.dumps(narrative, ensure_ascii=False) if narrative else None,
                llm.model_name() if narrative else None,
            ),
        )
        conn.commit()
        written += 1
    return {"kapsam": written, "gorusme": len(items)}


def run(
    week_start: date,
    stages: Iterable[str] = ("topla", "yaziya_dok", "analiz", "haftalik"),
    *,
    budget_sec: float | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    stages = list(stages)
    report: dict[str, Any] = {"hafta": week_start.isoformat()}

    def left() -> float | None:
        return None if budget_sec is None else max(budget_sec - (time.monotonic() - started), 0.0)

    with connect() as conn:
        ensure_schema(conn)
        run_id = conn.execute(
            "INSERT INTO conv_runs (org_id, week_start, started_at) VALUES (%s, %s, now()) RETURNING id",
            (get_org_id(), week_start),
        ).fetchone()[0]
        conn.commit()
        try:
            if "topla" in stages:
                report["topla"] = collect(conn, week_start)
            if "yaziya_dok" in stages:
                # Analize süre kalsın: yazıya dökme sürenin en fazla %70'ini kullanır.
                share = None if budget_sec is None else (left() or 0.0) * 0.7
                report["yaziya_dok"] = transcribe_pending(conn, week_start, budget_sec=share, limit=limit)
            if "analiz" in stages:
                report["analiz"] = analyze_pending(conn, week_start, budget_sec=left(), limit=limit)
            if "haftalik" in stages:
                report["haftalik"] = build_weekly(conn, week_start)
        finally:
            report["saniye"] = round(time.monotonic() - started, 1)
            conn.rollback()
            conn.execute(
                "UPDATE conv_runs SET finished_at = now(), report = %s::jsonb WHERE id = %s",
                (json.dumps(report, ensure_ascii=False, default=str), run_id),
            )
            conn.commit()
    return report


# --- panel okuma -------------------------------------------------------------------

_schema_ready = False


def _ready(conn: Any) -> None:
    global _schema_ready
    if not _schema_ready:
        ensure_schema(conn)
        _schema_ready = True


def setup_status() -> dict[str, Any]:
    """Panelde kurulum uyarısı için; anahtarlar Actions'ta olduğundan son çalışmaya bakılır."""
    with connect() as conn:
        _ready(conn)
        row = conn.execute(
            """
            SELECT started_at, finished_at, report FROM conv_runs
            WHERE org_id = %s ORDER BY started_at DESC LIMIT 1
            """,
            (get_org_id(),),
        ).fetchone()
    if not row:
        return {"son_calisma": None}
    started_at, finished_at, report = row
    return {"son_calisma": finished_at or started_at, "rapor": report}


def load_weeks(limit: int = 12) -> list[dict[str, Any]]:
    with connect() as conn:
        _ready(conn)
        rows = conn.execute(
            """
            SELECT (date_trunc('week', occurred_at AT TIME ZONE 'Europe/Istanbul'))::date AS w,
                   count(*), count(*) FILTER (WHERE status = 'analiz_edildi')
            FROM conv_items WHERE org_id = %s
            GROUP BY 1 ORDER BY 1 DESC LIMIT %s
            """,
            (get_org_id(), limit),
        ).fetchall()
    return [{"week_start": w, "gorusme": int(n), "analiz": int(a)} for w, n, a in rows]


def _item_summary(row: dict[str, Any]) -> dict[str, Any]:
    a = row.get("analysis") or {}
    scores = [v for v in (a.get("puanlar") or {}).values() if isinstance(v, int) and v > 0]
    return {
        "id": row["id"],
        "kind": row["kind"],
        "rep_id": row.get("rep_id"),
        "rep_name": row.get("rep_name"),
        "customer": row.get("customer"),
        "occurred_at": row["occurred_at"],
        "duration_sec": row.get("duration_sec"),
        "crm_outcome": row.get("crm_outcome"),
        "status": row["status"],
        "error": row.get("error"),
        "analiz_edilebilir": a.get("analiz_edilebilir"),
        "tur": a.get("tur"),
        "ozet": a.get("ozet") or a.get("neden"),
        "puan": round(sum(scores) / len(scores), 1) if scores else None,
        "ilgi": (a.get("musteri") or {}).get("ilgi") or None,
        "sonraki_adim": (a.get("sonraki_adim") or {}).get("var"),
        "itirazlar": sorted({o.get("tip") for o in a.get("itirazlar") or [] if o.get("tip")}),
        "risk": len(a.get("riskler") or []),
    }


def load_week(week_start: date, rep_id: str | None) -> dict[str, Any]:
    """Haftanın panel verisi. rep_id verilirse yalnız o temsilcinin görüşmeleri."""
    start, end = week_bounds(week_start)
    with connect() as conn:
        _ready(conn)
        cur = conn.execute(
            f"""
            SELECT c.id, c.kind, c.rep_id, r.full_name AS rep_name, c.customer, c.occurred_at,
                   c.duration_sec, c.crm_outcome, c.status, c.error, c.analysis
            FROM conv_items c LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
            WHERE c.org_id = %s AND c.occurred_at >= %s AND c.occurred_at < %s
              {"AND c.rep_id = %s" if rep_id else ""}
            ORDER BY c.occurred_at DESC
            """,
            (get_org_id(), start, end, *([rep_id] if rep_id else [])),
        )
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        weekly = conn.execute(
            """
            SELECT w.scope, r.full_name, w.stats, w.narrative, w.created_at
            FROM conv_weekly w LEFT JOIN reps r ON r.org_id = w.org_id AND r.rep_id = w.scope
            WHERE w.org_id = %s AND w.week_start = %s
            """,
            (get_org_id(), week_start),
        ).fetchall()
    status = Counter(r["status"] for r in rows)
    team = next(({"stats": s, "narrative": n, "created_at": c} for sc, _, s, n, c in weekly if sc == TEAM), None)
    reps = [
        {"rep_id": sc, "name": name or sc, "stats": s, "narrative": n}
        for sc, name, s, n, _ in weekly
        if sc != TEAM and (rep_id is None or sc == rep_id)
    ]
    reps.sort(key=lambda r: (-(r["stats"] or {}).get("analiz", 0), r["name"]))
    if rep_id is not None and team is not None:
        # Temsilci ekip sayılarını görür; başkalarına ait notları içeren ekip yorumunu görmez.
        team = {**team, "narrative": None}
    return {
        "week_start": week_start,
        "week_end": week_start + timedelta(days=6),
        "durum": {k: status.get(k, 0) for k in ("bekliyor", "yazildi", "analiz_edildi", "hata", "atlandi")},
        "team": team,
        "reps": reps,
        "items": [_item_summary(r) for r in rows],
        "score_labels": SCORE_LABELS,
    }


def load_item(item_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        _ready(conn)
        cur = conn.execute(
            """
            SELECT c.id, c.kind, c.rep_id, r.full_name AS rep_name, c.customer, c.occurred_at,
                   c.duration_sec, c.crm_outcome, c.status, c.error, c.analysis, c.transcript,
                   c.speakers, c.stt_provider, c.ai_model, c.lead_id
            FROM conv_items c LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
            WHERE c.org_id = %s AND c.id = %s
            """,
            (get_org_id(), item_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [c.name for c in cur.description]
    return dict(zip(cols, row))
