"""Görüşme Analizleri: haftanın telefon görüşmeleri ve toplantıları.

Akış (scripts/conversation_analysis.py, GitHub Actions):
1. topla      CRM çağrıları (events, kanal call) → Verimor call_uuid; Zoho
              Meeting kayıtları → meeting_sessions ile temsilci. conv_items.
2. yazıya dök kaydı indir, konuşma-tanımadan geçir (pusula/transcribe.py).
3. analiz     her görüşme için Claude, SONAR-41 ile (pusula/sonar41.py): fırsatı
              doğan davranışlara 1-5 puan + alıntı, CRM notuna C1-C6, özet,
              itirazlar, sonraki adım. CRM notları (Zoho Notes) lead ve
              zamanla görüşmeye bağlanır.
4. haftalık   sayılar koddan (aggregate); Claude yalnız temsilci başına tek
              odak maddesini ve ekip özetini cümleye döker.

Ilkeler (ROADMAP): kanıtsız iddia yok, her tespit ≤15 kelimelik alıntıyla;
karşı-olgusal yüzde yok; CRM'e yazılmaz, sonuç yalnız panelde görünür.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any

from pusula import llm, recordings, sonar41, transcribe
from pusula.config import get_org_id
from pusula.metrics import TZ
from pusula.occupancy import MATCH_MIN
from pusula.panel_data import connect, load_reps
from pusula.zoho.crm import coql

logger = logging.getLogger(__name__)

# Bu süreden kısa çağrı analiz edilmez (ulaşılamadı, sesli mesaj, "müsait değilim").
MIN_CALL_SEC = int(os.environ.get("PUSULA_CONV_MIN_CALL_SEC") or 20)
# Haftalık kota (temsilci başına): 2 dk ve üstü görüşmelerden 14, 2 dk altından 1
# ("müsait değilim" anında müşteriyi tutuyor mu). Kota dışı kayıt yazıya dökülmez.
QUOTA_LONG = 14
QUOTA_SHORT = 1
LONG_MIN_SEC = 120
# Kota haftaya yayılır: n. iş gününün sonunda en fazla QUOTA_LONG * n / 6 uzun görüşme.
WORK_DAYS = 6
# Kotada önce alınanlar (toplantılar ayrıca öncelikli).
PRIORITY_OUTCOMES = frozenset({"demo_done", "meeting_booked", "meeting_scheduled"})
# Kısa çağrı havuzuna girmeyenler (açılmadı, numara yanlış).
SHORT_SKIP_OUTCOMES = frozenset({"no_answer", "invalid_number"})
# Müşteri toplantıda bu kadar kalmadıysa kayıt analiz edilmez.
MIN_MEETING_CUSTOMER_SEC = 120
# Bu kadar kelimeden kısa transkript konuşma sayılmaz.
MIN_WORDS = 25
MAX_ATTEMPTS = 3
TEAM = "team"
BATCH_POLL_SEC = 60

# Puan başlıkları SONAR-41 boyutlarıdır (K1-K5).
SCORE_KEYS = sonar41.DIM_KEYS
SCORE_LABELS = dict(sonar41.DIMS)
# Bu puan ve altı "bug"tır.
BUG_MAX = 2
# SONAR-41'de CRM notu davranışı.
NOTE_CODE = "B39"
# Bu puan ve üstü "güçlü an"dır.
STRONG_MIN = 4
# Örnek görüşme: en az bu kadar davranış puanlanmış ve en az bu kadar güçlü an.
EXAMPLE_MIN_SCORED = 8
EXAMPLE_MIN_STRONG = 3
EXAMPLE_LIMIT = 6
# Görüşmeden sonra bu süre içinde aynı temsilcinin lead'e yazdığı not o görüşmenindir.
NOTE_WINDOW = timedelta(hours=3)
# Önceki notlar için geriye bakış.
NOTE_LOOKBACK = timedelta(days=60)
TYPES = ("kesif", "sunum", "takip", "kapanis", "itiraz", "destek", "diger")
OBJECTIONS = ("fiyat", "zaman", "guven", "ihtiyac_yok", "rakip", "karar_verici", "odeme", "diger")
RISKS = ("yanlis_bilgi", "baski", "olmayan_soz", "uslup", "diger")
# Müşteri "müsait değilim" dediğinde temsilci: tuttu / kısmen / bıraktı; müsaitse uygulanmaz.
HOLD_VALUES = ("tuttu", "kismen", "birakti", "uygulanmaz")

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
ALTER TABLE conv_items ADD COLUMN IF NOT EXISTS usage jsonb;
ALTER TABLE conv_items ADD COLUMN IF NOT EXISTS cost_usd numeric;
CREATE TABLE IF NOT EXISTS conv_batches (
    batch_id    text PRIMARY KEY,
    org_id      text NOT NULL DEFAULT 'rexven',
    item_ids    bigint[] NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    done_at     timestamptz,
    report      jsonb
);
ALTER TABLE conv_batches ENABLE ROW LEVEL SECURITY;
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
ALTER TABLE conv_weekly ADD COLUMN IF NOT EXISTS sent_at timestamptz;
ALTER TABLE conv_weekly ADD COLUMN IF NOT EXISTS sent_by text;
ALTER TABLE conv_weekly ADD COLUMN IF NOT EXISTS cost_usd numeric;
CREATE TABLE IF NOT EXISTS conv_notes (
    org_id         text NOT NULL DEFAULT 'rexven',
    note_id        text NOT NULL,
    parent_module  text,
    parent_id      text,
    rep_id         text,
    author         text,
    created_at     timestamptz NOT NULL,
    content        text,
    PRIMARY KEY (org_id, note_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_notes_parent ON conv_notes (org_id, parent_id, created_at);
CREATE TABLE IF NOT EXISTS conv_requests (
    id            bigserial PRIMARY KEY,
    org_id        text NOT NULL DEFAULT 'rexven',
    phone         text NOT NULL,
    days          int NOT NULL,
    customer      text,
    requested_by  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    item_ids      bigint[] NOT NULL DEFAULT '{}'
);
ALTER TABLE conv_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE conv_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE conv_weekly ENABLE ROW LEVEL SECURITY;
ALTER TABLE conv_notes ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS conv_runs (
    id           bigserial PRIMARY KEY,
    org_id       text NOT NULL DEFAULT 'rexven',
    week_start   date NOT NULL,
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz,
    report       jsonb
);
ALTER TABLE conv_runs ENABLE ROW LEVEL SECURITY;
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
        "meta": {
            "call_purpose": meta.get("call_purpose"),
            "subject": meta.get("subject"),
            "outcome_key": meta.get("outcome_key"),
            "call_status": meta.get("call_status"),
        },
    }


def _insert_items(conn: Any, rows: Iterable[dict[str, Any]]) -> int:
    added = 0
    for row in rows:
        cur = conn.execute(
            """
            INSERT INTO conv_items (org_id, kind, source_ref, event_id, rep_id, thread_id, lead_id,
                                    customer, direction, occurred_at, duration_sec, crm_outcome, meta,
                                    status, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
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
                row.get("status") or "bekliyor", row.get("error"),
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
    rows = [_as_candidate(r) for r in (call_row(e) for e in events) if r is not None]
    return {"kayitli_cagri": len(events), "uygun": len(rows), "yeni": _insert_items(conn, rows)}


def _as_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Gece toplanan kayıt kota adayıdır; seçilene kadar yazıya dökülmez."""
    return {**row, "status": "atlandi", "error": QUOTA_WAIT, "meta": {**(row.get("meta") or {}), "kota": "aday"}}


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


def note_row(record: dict[str, Any]) -> dict[str, Any] | None:
    """Zoho Notes kaydı → conv_notes satırı (saf)."""
    created = record.get("Created_Time")
    if not record.get("id") or not created:
        return None
    parent = record.get("Parent_Id") or {}
    module = parent.get("module")
    module = module.get("api_name") if isinstance(module, dict) else module
    author = record.get("Created_By") or {}
    content = _clean_note(record.get("Note_Content"))
    title = (record.get("Note_Title") or "").strip()
    return {
        "note_id": str(record["id"]),
        "parent_module": module,
        "parent_id": str(parent.get("id")) if parent.get("id") else None,
        "rep_id": str(author.get("id")) if author.get("id") else None,
        "author": author.get("name"),
        "created_at": datetime.fromisoformat(str(created)),
        "content": f"{title}: {content}" if title and title not in content else content,
    }


def _collect_notes(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """Haftanın ve önceki 60 günün CRM notları (önceki notlar bağlam için)."""
    added = 0
    cursor = start - NOTE_LOOKBACK
    try:
        while cursor < end:
            stop = min(cursor + timedelta(days=7), end)
            query = (
                "select id, Note_Title, Note_Content, Parent_Id, Created_By, Created_Time from Notes "
                f"where Created_Time between '{cursor.isoformat(timespec='seconds')}' "
                f"and '{stop.isoformat(timespec='seconds')}'"
            )
            for record in coql(query):
                row = note_row(record)
                if row is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO conv_notes (org_id, note_id, parent_module, parent_id, rep_id, author,
                                            created_at, content)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (org_id, note_id) DO UPDATE SET content = EXCLUDED.content
                    """,
                    (get_org_id(), row["note_id"], row["parent_module"], row["parent_id"], row["rep_id"],
                     row["author"], row["created_at"], row["content"]),
                )
                added += 1
            conn.commit()
            cursor = stop
    except Exception as exc:  # noqa: BLE001 — not çekilemezse analiz notsuz sürer
        conn.rollback()
        logger.warning("CRM notları alınamadı: %s", exc)
        return {"hata": str(exc)[:200], "not": added}
    return {"not": added}


def collect(conn: Any, week_start: date) -> dict[str, Any]:
    start, end = week_bounds(week_start)
    return {
        "telefon": _collect_calls(conn, start, end),
        "toplanti": _collect_meetings(conn, start, end),
        "crm_notlari": _collect_notes(conn, start, end),
    }


# --- 1b. kota ----------------------------------------------------------------------

QUOTA_WAIT = "kota: sırada"
QUOTA_OUT = "kota dışı"


def _stable(ref: str) -> int:
    # Sabit sıra: aynı kayıt her gece aynı yere düşer, temsilci seçimi tahmin edemez.
    return int(hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12], 16)


def plan_quota(rows: list[dict[str, Any]], elapsed_days: int, closed: bool) -> dict[str, list[int]]:
    """Kota planı (saf). rows: haftanın kayıtları (id, rep_id, kind, occurred_at, duration_sec,
    status, kota, talep, outcome, call_status, source_ref). Dönüş: secilen / sirada / disi id'leri."""
    by_rep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("rep_id") and not r.get("talep"):
            by_rep[str(r["rep_id"])].append(r)
    plan: dict[str, list[int]] = {"secilen": [], "sirada": [], "disi": []}
    cap_long = QUOTA_LONG if closed else -(-QUOTA_LONG * min(elapsed_days, WORK_DAYS) // WORK_DAYS)

    def short(r: dict[str, Any]) -> bool:
        return r["kind"] == "call" and (r.get("duration_sec") or 0) < LONG_MIN_SEC

    def day(r: dict[str, Any]) -> date:
        return r["occurred_at"].astimezone(TZ).date()

    for items in by_rep.values():
        chosen = [
            r for r in items
            if (r.get("kota") == "secildi" or (r.get("kota") is None and r["status"] == "analiz_edildi"))
            and not (r["status"] == "atlandi" and r.get("kota") != "aday")
        ]
        pool = [
            r for r in items
            if r.get("kota") == "aday" or (r.get("kota") is None and r["status"] in ("bekliyor", "yazildi"))
        ]
        need_long = max(cap_long - sum(1 for r in chosen if not short(r)), 0)
        need_short = max(QUOTA_SHORT - sum(1 for r in chosen if short(r)), 0) if elapsed_days >= 1 or closed else 0
        per_day = Counter(day(r) for r in chosen if not short(r))
        longs = [r for r in pool if not short(r)]
        picked: list[dict[str, Any]] = []
        while need_long and longs:
            best = min(
                longs,
                key=lambda r: (
                    0 if r["kind"] == "meeting" or r.get("outcome") in PRIORITY_OUTCOMES else 1,
                    per_day[day(r)],
                    _stable(r["source_ref"]),
                ),
            )
            longs.remove(best)
            picked.append(best)
            per_day[day(best)] += 1
            need_long -= 1
        shorts = sorted(
            (
                r for r in pool
                if short(r) and r.get("outcome") not in SHORT_SKIP_OUTCOMES
                and r.get("call_status") in (None, "connected")
            ),
            key=lambda r: (0 if r.get("outcome") == "callback" else 1, _stable(r["source_ref"])),
        )
        picked += shorts[:need_short]
        picked_ids = {r["id"] for r in picked}
        plan["secilen"] += sorted(picked_ids)
        plan["disi" if closed else "sirada"] += sorted(r["id"] for r in pool if r["id"] not in picked_ids)
    return plan


def select_quota(conn: Any, week_start: date, today: date) -> dict[str, Any]:
    """Haftanın kota seçimini uygular. Seçilen yazıya dökülür; kalanlar sırada bekler,
    hafta kapanınca kota dışı olur. Talep edilen ve temsilcisiz kayıtlar kotaya girmez."""
    start, end = week_bounds(week_start)
    cur = conn.execute(
        """
        SELECT c.id, c.rep_id, c.kind, c.occurred_at, c.duration_sec, c.status, c.source_ref,
               c.meta->>'kota' AS kota, coalesce((c.meta->>'talep')::boolean, false) AS talep,
               coalesce(c.meta->>'outcome_key', e.meta->>'outcome_key') AS outcome,
               coalesce(c.meta->>'call_status', e.meta->>'call_status') AS call_status
        FROM conv_items c LEFT JOIN events e ON e.id = c.event_id
        WHERE c.org_id = %s AND c.occurred_at >= %s AND c.occurred_at < %s
        """,
        (get_org_id(), start, end),
    )
    cols = [c.name for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    closed = today >= week_start + timedelta(days=7)
    plan = plan_quota(rows, (today - week_start).days, closed)
    conn.execute(
        """
        UPDATE conv_items SET
            status = CASE WHEN transcript IS NOT NULL THEN 'yazildi' ELSE 'bekliyor' END,
            error = NULL,
            meta = coalesce(meta, '{}'::jsonb) || '{"kota": "secildi"}'::jsonb
        WHERE id = ANY(%s) AND status IN ('bekliyor', 'yazildi', 'atlandi')
        """,
        (plan["secilen"],),
    )
    for key, error in (("sirada", QUOTA_WAIT), ("disi", QUOTA_OUT)):
        conn.execute(
            """
            UPDATE conv_items SET status = 'atlandi', error = %s,
                   meta = coalesce(meta, '{}'::jsonb) || jsonb_build_object('kota', %s::text)
            WHERE id = ANY(%s)
            """,
            (error, "aday" if key == "sirada" else "disi", plan[key]),
        )
    conn.commit()
    return {k: len(v) for k, v in plan.items()}


# --- 2. yazıya dök --------------------------------------------------------------


def _pending(conn: Any, week_start: date, status: str, limit: int | None) -> list[dict[str, Any]]:
    start, end = week_bounds(week_start)
    cur = conn.execute(
        f"""
        SELECT c.*, r.full_name AS rep_name, e.body AS event_body FROM conv_items c
        LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
        LEFT JOIN events e ON e.id = c.event_id
        WHERE c.org_id = %s
          AND ((c.occurred_at >= %s AND c.occurred_at < %s) OR c.meta ? 'talep')
          AND (c.status = %s OR (c.status = 'hata' AND c.attempts < %s
               AND (%s = 'bekliyor') = (c.transcript IS NULL)))
        ORDER BY (c.meta ? 'talep') DESC, c.occurred_at
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
        return {"atlandi": "konuşma-tanıma anahtarı yok (ASSEMBLYAI/OPENAI/ELEVENLABS/DEEPGRAM)"}
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
_CODE = {"type": "string", "enum": list(sonar41.CODES)}


def _quoted_items() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"kod": _CODE, "madde": {"type": "string"}, "alinti": _QUOTE},
            "required": ["kod", "madde", "alinti"],
            "additionalProperties": False,
        },
    }


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
        "sonar": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kod": _CODE,
                    "puan": {"type": "integer"},
                    "alinti": _QUOTE,
                    "dogrusu": {
                        "type": "string",
                        "description": "Puan 2 ve altıysa temsilcinin o anda söylemesi gereken tek cümle; değilse boş",
                    },
                },
                "required": ["kod", "puan", "alinti", "dogrusu"],
                "additionalProperties": False,
            },
        },
        "crm": {
            "type": "object",
            "properties": {
                "not_var": {"type": "boolean"},
                "puanlar": {
                    "type": "object",
                    "properties": {k: {"type": "integer"} for k in sonar41.CRM_KEYS},
                    "required": list(sonar41.CRM_KEYS),
                    "additionalProperties": False,
                },
                "yorum": {"type": "string"},
            },
            "required": ["not_var", "puanlar", "yorum"],
            "additionalProperties": False,
        },
        "guclu": _quoted_items(),
        "gelisim": _quoted_items(),
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
        "musaitlik": {
            "type": "object",
            "properties": {
                "temsilci_sordu": {"type": "boolean"},
                "musait_degil": {"type": "boolean"},
                "tutma": {"type": "string", "enum": list(HOLD_VALUES)},
                "geri_arama_net": {"type": "boolean"},
                "alinti": _QUOTE,
            },
            "required": ["temsilci_sordu", "musait_degil", "tutma", "geri_arama_net", "alinti"],
            "additionalProperties": False,
        },
    },
    "required": [
        "analiz_edilebilir", "neden", "ozet", "tur", "temsilci_konusmaci", "musteri", "itirazlar",
        "sonar", "crm", "guclu", "gelisim", "sonraki_adim", "riskler", "sozler", "musaitlik",
    ],
    "additionalProperties": False,
}

ANALYSIS_SYSTEM = f"""Rexven'in satış ekibinin müşteri görüşmelerini SONAR-41 çerçevesiyle inceleyen bir
satış koçusun. Rexven, e-ticaret (Amazon, Etsy, Shopify vb.) eğitim, mentorluk ve stoksuz üretim
(print on demand) paketleri satar (Plus, Premium, Pro); ekip telefonda ve Zoho Meeting'de görüşür.
Sana tek görüşmenin otomatik transkripti, temsilcinin bu görüşme için CRM'e yazdığı not ve aynı
müşteriye daha önce yazılmış notlar verilir. Transkript konuşma-tanımadan geldiği için yazım
hataları ve yanlış konuşmacı ayrımı olabilir ("Reksan", "Rexmen" = Rexven); anlamdan çıkar.

SONAR-41 davranışları (kod · ad: tanım):
{sonar41.rubric_text()}

Puanlama ("sonar"):
- Görüşmede fırsatı doğan her davranışı bir kez puanla. Fırsatı hiç doğmayan davranışı listeye
  koyma (ör. itiraz gelmediyse K3; görüşme ödeme sonrası destekse K1 teşhis davranışları).
- 1: fırsat vardı, yapılmadı ya da tersi yapıldı · 2: zayıf · 3: yeterli · 4: iyi · 5: örnek alınacak.
- Her puana transkriptten birebir en fazla 15 kelimelik alıntı ekle. Davranış yapılmadıysa
  fırsatın doğduğu cümleyi (çoğunlukla müşterinin) alıntıla.
- B35 için önceki notları kullan: takip, önceki görüşmeden somut bir referansla mı başladı.
- Puan 2 ve altıysa "dogrusu"na temsilcinin o anda müşteriye söylemesi gereken tek, doğal bir
  cümle yaz (bu görüşmenin bağlamına göre, Rexven diliyle). Puan 3 ve üstündeyse boş bırak.
  Transkriptte geçmeyen fiyat, tarih, isim ya da paket bilgisi yazma. B39'da "dogrusu" müşteriye
  söz değil, temsilcinin yazması gereken kısa CRM notudur ("İhtiyaç: … Engel: … Karar: …
  Sonraki adım: …"), yalnız transkriptteki olgularla.
- B39 CRM notudur: not yoksa (ve görüşme analiz edilebilirse) B39 = 1, alıntı olarak görüşmenin
  son cümlesini ver. Not varsa notun içeriğine göre puanla; alıntı transkriptten.

CRM notu ("crm"):
- Temsilcinin bu görüşme için notu yoksa not_var=false, C1-C6 hepsi 0, yorum kısa.
- Not varsa C1-C6'yı 1-5 puanla: notu transkriptle karşılaştır (C1 durum doğruluğu: notta yazan
  görüşmede gerçekten oldu mu). yorum: notta eksik olan en önemli şey, tek cümle.

Diğer kurallar:
- Kanıtsız iddia yazma; güçlü/gelişim/risk maddelerinin her birine alıntı ekle ve ilgili SONAR
  kodunu yaz. Alıntı bulamıyorsan maddeyi yazma.
- "Şunu yapsaydı satış %X artardı" gibi karşı-olgusal tahmin ve yüzde yazma.
- Transkript satış görüşmesi değilse (sesli mesaj, yanlış numara, yalnız selamlaşma, çok kısa)
  analiz_edilebilir=false yap, nedeni yaz; diğer listeleri boş, sayıları 0 bırak.
- musteri.ilgi 1-5: müşterinin satın alma ilgisi.
- temsilci_konusmaci: transkriptteki temsilci etiketi (ör. "Konuşmacı 1"); ayrım yoksa "belirsiz".
- sonraki_adim.var: görüşme net bir sonraki adımla bitti mi; tarih_net: gün/saat konuşuldu mu.
- sozler: temsilcinin müşteriye verdiği somut sözler.
- musaitlik.temsilci_sordu: temsilci "müsait misiniz / uygun musunuz / vaktiniz var mı" gibi
  bir soruyla müşteriye çıkış kapısı açtı mı. Açtıysa bu bir hatadır: B01'i en fazla 2 puanla,
  alıntı o soru olsun; DEBUG (dogrusu) aynı anda müşteriyi konuşturacak bir açılış sorusu olsun.
- musaitlik: müşteri o an müsait olmadığını söyledi mi (musait_degil). Söylediyse "tutma":
  tuttu = tek bir değer cümlesi ya da kısa bir soruyla ilgiyi canlı tuttu; müşteri bir süre
  konuştu ya da somut bir geri arama zamanına kendisi onay verdi. kismen = ilgi uyandırmaya
  çalıştı ama yarım kaldı. birakti = yalnız "ne zaman müsaitsiniz / sonra arayayım" deyip
  kapattı. Müşteri müsaitse "uygulanmaz". geri_arama_net: gün ve saat netleşti mi. alinti:
  müşterinin müsait olmadığını söylediği an ya da temsilcinin tepkisi. Müşteri müsait değilken
  sorup bıraktıysa B01'i, geri arama zamanı netleşmediyse B34'ü 2 ve altı puanla. Kısa görüşmede
  (2 dk altı) fırsatı doğmayan davranışları puanlama.
- riskler: kanıtsız kazanç/başarı iddiası, garanti, kesin vergi/hukuk yanıtı, baskıcı satış,
  verilemeyecek söz, kaba üslup. Doğruluğunu bilemediğin şirket bilgilerini (fiyat, paket içeriği)
  risk sayma.
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
    seen: dict[str, dict[str, Any]] = {}
    for row in raw.get("sonar") or []:
        code = row.get("kod")
        score = _clamp(row.get("puan"), 0, 5)
        if code in sonar41.BEHAVIORS and code not in seen and score > 0:
            seen[code] = {
                "kod": code,
                "puan": score,
                "alinti": str(row.get("alinti") or ""),
                "dogrusu": str(row.get("dogrusu") or "") if score <= BUG_MAX else "",
            }
    out["sonar"] = [seen[c] for c in sonar41.CODES if c in seen]
    crm = dict(raw.get("crm") or {})
    has_note = bool(crm.get("not_var"))
    scores = crm.get("puanlar") or {}
    crm["not_var"] = has_note
    crm["puanlar"] = {k: _clamp(scores.get(k), 0, 5) if has_note else 0 for k in sonar41.CRM_KEYS}
    crm["yorum"] = str(crm.get("yorum") or "")
    out["crm"] = crm
    hold = dict(raw.get("musaitlik") or {})
    hold["temsilci_sordu"] = bool(hold.get("temsilci_sordu"))
    hold["musait_degil"] = bool(hold.get("musait_degil"))
    if hold.get("tutma") not in HOLD_VALUES or not hold["musait_degil"]:
        hold["tutma"] = "uygulanmaz"
    hold["geri_arama_net"] = bool(hold.get("geri_arama_net"))
    hold["alinti"] = str(hold.get("alinti") or "")
    out["musaitlik"] = hold
    musteri = dict(raw.get("musteri") or {})
    musteri["ilgi"] = _clamp(musteri.get("ilgi"), 0, 5)
    out["musteri"] = musteri
    if out.get("tur") not in TYPES:
        out["tur"] = "diger"
    return out


def dim_scores(analysis: dict[str, Any]) -> dict[str, float]:
    """Tek görüşmenin boyut puanları: o boyutta puanlanan davranışların ortalaması."""
    rows: dict[str, list[float]] = defaultdict(list)
    for row in analysis.get("sonar") or []:
        b = sonar41.BEHAVIORS.get(row.get("kod") or "")
        if b and (row.get("puan") or 0) > 0:
            rows[b["dim"]].append(float(row["puan"]))
    return {k: round(sum(v) / len(v), 2) for k, v in rows.items()}


def score_spread(items: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Boyut başına davranış puanlarının 1-5 dağılımı: ortalama 3 tutarlı mı, dağınık mı."""
    out = {k: [0, 0, 0, 0, 0] for k in SCORE_KEYS}
    for item in items:
        a = item.get("analysis") or {}
        if not a.get("analiz_edilebilir"):
            continue
        for row in a.get("sonar") or []:
            b = sonar41.BEHAVIORS.get(row.get("kod") or "")
            score = row.get("puan") or 0
            if b and 1 <= score <= 5 and b["dim"] in out:
                out[b["dim"]][score - 1] += 1
    return out


def example_calls(items: list[dict[str, Any]], limit: int | None = EXAMPLE_LIMIT) -> list[dict[str, Any]]:
    """Örnek görüşmeler: yeterince davranış puanlanmış, güçlü anları çok ve ortalaması yüksek
    görüşmeler; güçlü anlar (4-5) görüşme akışı sırasıyla (K1→K5) alıntılarıyla."""
    out: list[dict[str, Any]] = []
    for item in items:
        a = item.get("analysis") or {}
        if not a.get("analiz_edilebilir"):
            continue
        rows = [r for r in a.get("sonar") or [] if r.get("kod") in sonar41.BEHAVIORS and (r.get("puan") or 0) > 0]
        strong = [r for r in rows if r["puan"] >= STRONG_MIN]
        if len(rows) < EXAMPLE_MIN_SCORED or len(strong) < EXAMPLE_MIN_STRONG:
            continue
        out.append({
            "item_id": item.get("id"),
            "rep_id": item.get("rep_id"),
            "rep_name": item.get("rep_name"),
            "kind": item.get("kind"),
            "occurred_at": item.get("occurred_at"),
            "duration_sec": item.get("duration_sec"),
            "crm_outcome": item.get("crm_outcome"),
            "tur": a.get("tur"),
            "ozet": a.get("ozet") or "",
            "puan": round(sum(r["puan"] for r in rows) / len(rows), 2),
            "puanlar": dim_scores(a),
            "sonraki_adim": (a.get("sonraki_adim") or {}).get("aciklama") or "",
            "anlar": [
                {"kod": r["kod"], "puan": r["puan"], "alinti": r.get("alinti") or ""}
                for r in sorted(strong, key=lambda r: sonar41.CODES.index(r["kod"]))
            ],
        })
    out.sort(key=lambda e: (-e["puan"], -len(e["anlar"])))
    return out if limit is None else out[:limit]


def examples_by_rep(items: list[dict[str, Any]], reps: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Her temsilcinin haftadaki en iyi örnek görüşmesi; ölçütü karşılayan yoksa None."""
    best: dict[str, dict[str, Any]] = {}
    for e in example_calls(items, limit=None):
        best.setdefault(str(e["rep_id"]), e)
    return [{"rep_id": rep_id, "rep_name": name, "ornek": best.get(rep_id)} for rep_id, name in reps]


def bug_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Haftanın bug'ları (puan 2 ve altı): ihlal anı alıntısı ve doğrusu cümlesiyle."""
    out: list[dict[str, Any]] = []
    for item in items:
        a = item.get("analysis") or {}
        if not a.get("analiz_edilebilir"):
            continue
        for row in a.get("sonar") or []:
            score = row.get("puan") or 0
            if row.get("kod") in sonar41.BEHAVIORS and 1 <= score <= BUG_MAX:
                crm = a.get("crm") or {}
                # B39'un alıntısı görüşmenin son cümlesidir; ihlali not durumu anlatır.
                if row["kod"] == NOTE_CODE:
                    ihlal = (
                        f"Not yazıldı ama eksik: {crm.get('yorum')}" if crm.get("not_var") and crm.get("yorum")
                        else "Not yazıldı ama eksik." if crm.get("not_var")
                        else "Görüşmeden sonra CRM notu yazılmadı."
                    )
                else:
                    ihlal = ""
                out.append({
                    "kod": row["kod"],
                    "puan": score,
                    "alinti": "" if ihlal else row.get("alinti") or "",
                    "ihlal": ihlal,
                    "dogrusu": row.get("dogrusu") or "",
                    "rep_id": item.get("rep_id"),
                    "item_id": item.get("id"),
                })
    return out


def _clean_note(html: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</div>", " / ", html or "", flags=re.IGNORECASE)
    text = unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"(\s*/\s*)+", " / ", re.sub(r"[ \t]+", " ", text)).strip(" /")


def call_notes(conn: Any, item: dict[str, Any]) -> dict[str, list[str]]:
    """Görüşmenin CRM notları: bu görüşmeye yazılan (temsilci, 3 saat içinde) ve öncekiler."""
    out: dict[str, list[str]] = {"bu": [], "onceki": []}
    body = (item.get("event_body") or "").strip()
    if len(body) > 3:
        out["bu"].append(f"(çağrı kaydı açıklaması) {body}")
    if not item.get("lead_id"):
        return out
    start: datetime = item["occurred_at"]
    end = start + timedelta(seconds=int(item.get("duration_sec") or 0)) + NOTE_WINDOW
    rows = conn.execute(
        """
        SELECT created_at, author, rep_id, content FROM conv_notes
        WHERE org_id = %s AND parent_id = %s AND created_at < %s
        ORDER BY created_at
        """,
        (get_org_id(), str(item["lead_id"]), end),
    ).fetchall()
    for created_at, author, rep_id, content in rows:
        line = f"{created_at.astimezone(TZ):%d.%m %H:%M} {author or ''}: {content}"
        if created_at >= start - timedelta(minutes=5) and (rep_id == item.get("rep_id") or not rep_id):
            out["bu"].append(line)
        elif created_at < start - timedelta(minutes=5):
            out["onceki"].append(line)
    out["onceki"] = out["onceki"][-5:]
    return out


def _prompt(item: dict[str, Any], notes: dict[str, list[str]] | None = None) -> str:
    notes = notes or {"bu": [], "onceki": []}
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
        "Temsilcinin bu görüşme için CRM notu:",
        *(notes["bu"] or ["(not yok)"]),
        "",
        "Aynı müşteriye önceki CRM notları (eskiden yeniye):",
        *(notes["onceki"] or ["(önceki not yok)"]),
        "",
        "<transkript>",
        item.get("transcript") or "",
        "</transkript>",
    ]
    return "\n".join(lines)


def reset_old_analyses(conn: Any, week_start: date) -> int:
    """SONAR-41 öncesi biçimde analiz edilmiş görüşmeleri yeniden analize alır."""
    start, end = week_bounds(week_start)
    cur = conn.execute(
        """
        UPDATE conv_items SET status = 'yazildi'
        WHERE org_id = %s AND occurred_at >= %s AND occurred_at < %s
          AND status = 'analiz_edildi' AND NOT (analysis ? 'sonar')
        """,
        (get_org_id(), start, end),
    )
    conn.commit()
    return cur.rowcount or 0


def _save_analysis(conn: Any, item_id: int, raw: dict[str, Any] | None, used: dict[str, Any] | None,
                   error: str | None) -> bool:
    usage_json = json.dumps(used, ensure_ascii=False) if used else None
    cost = (used or {}).get("usd")
    if raw is None:
        conn.execute(
            """
            UPDATE conv_items SET status = 'hata', attempts = attempts + 1, error = %s,
                   usage = coalesce(%s::jsonb, usage), cost_usd = coalesce(cost_usd, 0) + coalesce(%s, 0)
            WHERE id = %s
            """,
            ((error or "AI analizi alınamadı")[:500], usage_json, cost, item_id),
        )
        conn.commit()
        return False
    conn.execute(
        """
        UPDATE conv_items SET analysis = %s::jsonb, ai_model = %s, analyzed_at = now(),
               status = 'analiz_edildi', error = NULL,
               usage = %s::jsonb, cost_usd = coalesce(cost_usd, 0) + coalesce(%s, 0)
        WHERE id = %s
        """,
        (json.dumps(normalize_analysis(raw), ensure_ascii=False), (used or {}).get("model") or llm.model_name(),
         usage_json, cost, item_id),
    )
    conn.commit()
    return True


def batch_enabled() -> bool:
    return (os.environ.get("PUSULA_AI_BATCH") or "1") != "0"


def _collect_batches(conn: Any, report: dict[str, Any]) -> None:
    """Açık toplu işlerin bitenlerini yazar."""
    for batch_id, item_ids in conn.execute(
        "SELECT batch_id, item_ids FROM conv_batches WHERE org_id = %s AND done_at IS NULL ORDER BY created_at",
        (get_org_id(),),
    ).fetchall():
        results = llm.batch_results(batch_id)
        if results is None:
            report["bekleyen_toplu"] = report.get("bekleyen_toplu", 0) + 1
            continue
        done = {"analiz": 0, "hata": 0}
        for item_id in item_ids:
            raw, used, error = results.get(str(item_id), (None, None, "toplu işte sonuç yok"))
            done["analiz" if _save_analysis(conn, item_id, raw, used, error) else "hata"] += 1
        conn.execute(
            "UPDATE conv_batches SET done_at = now(), report = %s::jsonb WHERE batch_id = %s",
            (json.dumps(done), batch_id),
        )
        conn.commit()
        report["analiz"] += done["analiz"]
        report["hata"] += done["hata"]


def analyze_pending(
    conn: Any, week_start: date, *, budget_sec: float | None = None, limit: int | None = None
) -> dict[str, Any]:
    """Yazılmış kayıtları analiz eder. Toplu modda (varsayılan, %50 ucuz) iş gönderilir ve
    süre içinde bitmezse sonuçları sonraki çalışma alır."""
    if not llm.enabled():
        return {"atlandi": "ANTHROPIC_API_KEY yok"}
    started = time.monotonic()
    report: dict[str, Any] = {"analiz": 0, "hata": 0, "yeniden": reset_old_analyses(conn, week_start)}
    if not batch_enabled():
        for item in _pending(conn, week_start, "yazildi", limit):
            if budget_sec is not None and time.monotonic() - started > budget_sec:
                report["durdu"] = "sure_doldu"
                break
            used: list[dict[str, Any]] = []
            raw = llm.complete_json(ANALYSIS_SYSTEM, _prompt(item, call_notes(conn, item)), ANALYSIS_SCHEMA, usage=used)
            ok = _save_analysis(conn, item["id"], raw, used[0] if used else None, None)
            report["analiz" if ok else "hata"] += 1
        return report

    _collect_batches(conn, report)
    open_ids = {
        i for (ids,) in conn.execute(
            "SELECT item_ids FROM conv_batches WHERE org_id = %s AND done_at IS NULL", (get_org_id(),)
        ).fetchall() for i in ids
    }
    items = [i for i in _pending(conn, week_start, "yazildi", limit) if i["id"] not in open_ids]
    if items:
        batch_id = llm.batch_submit(
            [(str(i["id"]), ANALYSIS_SYSTEM, _prompt(i, call_notes(conn, i)), ANALYSIS_SCHEMA) for i in items]
        )
        if batch_id is None:
            report["hata_gonderim"] = len(items)
        else:
            conn.execute(
                "INSERT INTO conv_batches (batch_id, org_id, item_ids) VALUES (%s, %s, %s)",
                (batch_id, get_org_id(), [i["id"] for i in items]),
            )
            conn.commit()
            report["gonderildi"] = len(items)
    # Süre yettiğince bekle; çoğu toplu iş bir saat içinde biter.
    while report.get("gonderildi") or report.get("bekleyen_toplu"):
        if budget_sec is not None and time.monotonic() - started > budget_sec - BATCH_POLL_SEC:
            report["durdu"] = "toplu_is_suruyor"
            break
        time.sleep(BATCH_POLL_SEC)
        report.pop("bekleyen_toplu", None)
        report.pop("gonderildi", None)
        _collect_batches(conn, report)
    return report


# --- 4. haftalık ------------------------------------------------------------------


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Analiz edilmiş görüşmelerden haftalık sayılar (saf)."""
    usable = [i for i in items if (i.get("analysis") or {}).get("analiz_edilebilir")]
    dims: dict[str, list[float]] = {k: [] for k in SCORE_KEYS}
    behaviors: dict[str, list[float]] = defaultdict(list)
    crm_scores: dict[str, list[float]] = {k: [] for k in sonar41.CRM_KEYS}
    objections: dict[str, dict[str, int]] = defaultdict(lambda: {"adet": 0, "karsilandi": 0, "kismen": 0})
    types = Counter[str]()
    risks = Counter[str]()
    interest: list[float] = []
    next_step = dated = noted = 0
    hold: Counter[str] = Counter()
    for item in usable:
        a = item["analysis"]
        m = a.get("musaitlik") or {}
        hold["temsilci_sordu"] += 1 if m.get("temsilci_sordu") else 0
        if m.get("musait_degil"):
            hold["adet"] += 1
            hold[m.get("tutma") or "uygulanmaz"] += 1
            hold["net_geri_arama"] += 1 if m.get("geri_arama_net") else 0
        for key, value in dim_scores(a).items():
            dims[key].append(value)
        for row in a.get("sonar") or []:
            if (row.get("puan") or 0) > 0:
                behaviors[row["kod"]].append(float(row["puan"]))
        crm = a.get("crm") or {}
        if crm.get("not_var"):
            noted += 1
            for key in sonar41.CRM_KEYS:
                value = (crm.get("puanlar") or {}).get(key) or 0
                if value > 0:
                    crm_scores[key].append(float(value))
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
    dim_avgs = {k: _avg(v) for k, v in dims.items()}
    present = [v for v in dim_avgs.values() if v is not None]
    n = len(usable)
    return {
        "gorusme": len(items),
        "analiz": n,
        "telefon": sum(1 for i in usable if i.get("kind") == "call"),
        "toplanti": sum(1 for i in usable if i.get("kind") == "meeting"),
        "dakika": round(sum((i.get("duration_sec") or 0) for i in usable) / 60, 1),
        "puanlar": dim_avgs,
        "puan_ort": _avg(present),
        "davranislar": {
            code: {"ort": _avg(behaviors[code]), "adet": len(behaviors[code]),
                   "zayif": sum(1 for v in behaviors[code] if v <= 2)}
            for code in sonar41.CODES
            if behaviors.get(code)
        },
        "crm": {
            "notlu": noted,
            "oran": round(100.0 * noted / n, 1) if n else None,
            "puanlar": {k: _avg(v) for k, v in crm_scores.items()},
        },
        "ilgi_ort": _avg(interest),
        "musaitlik": {
            k: hold.get(k, 0) for k in ("adet", "tuttu", "kismen", "birakti", "net_geri_arama", "temsilci_sordu")
        },
        "sonraki_adim_orani": round(100.0 * next_step / n, 1) if n else None,
        "tarihli_adim_orani": round(100.0 * dated / n, 1) if n else None,
        "itirazlar": {
            k: {**v, "oran": round(100.0 * (v["karsilandi"] + 0.5 * v["kismen"]) / v["adet"], 1)}
            for k, v in sorted(objections.items(), key=lambda kv: -kv[1]["adet"])
        },
        "turler": dict(types.most_common()),
        "riskler": dict(risks.most_common()),
    }


def weakest(stats: dict[str, Any], limit: int = 5, min_count: int = 2) -> list[dict[str, Any]]:
    """En düşük ortalamalı davranışlar ("bug"): en az min_count gözlem."""
    rows = [
        {"kod": code, "ad": sonar41.BEHAVIORS[code]["name"], **row}
        for code, row in (stats.get("davranislar") or {}).items()
        if row.get("adet", 0) >= min_count and row.get("ort") is not None
    ]
    rows.sort(key=lambda r: (r["ort"], -r["adet"]))
    return rows[:limit]


REP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ozet": {"type": "string"},
        "odak": {
            "type": "object",
            "properties": {
                "kod": _CODE,
                "baslik": {"type": "string"},
                "neden": {"type": "string"},
                "alinti": _QUOTE,
                "nasil": {"type": "string"},
            },
            "required": ["kod", "baslik", "neden", "alinti", "nasil"],
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
- Puanlar SONAR-41'dendir: K1-K5 boyut, B01-B41 davranış, C1-C6 CRM notu ölçütü; 1-5 ölçek,
  2 ve altı "bug". "davranislar" her davranışın ortalamasını ve gözlem sayısını verir.
- Temsilci için tek odak maddesi seç: en çok gözlenen ve düşük puanlı, satışa en yakın SONAR
  davranışı; kodu "kod"a yaz. "nasil": bir sonraki görüşmede söylenebilecek somut bir cümle.
- CRM notu oranı düşükse ya da C puanları zayıfsa bunu dikkat maddelerinden birine yaz.
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
        for row in a.get("sonar") or []:
            if (row.get("puan") or 0) <= 2:
                name = sonar41.BEHAVIORS.get(row.get("kod") or "", {}).get("name", "")
                parts.append(f"  bug {row.get('kod')} {name} ({row.get('puan')}): \"{row.get('alinti')}\"")
        crm = a.get("crm") or {}
        parts.append(f"  CRM notu: {'var · ' + crm.get('yorum', '') if crm.get('not_var') else 'yok'}")
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
        used: list[dict[str, Any]] = []
        if narrate and llm.enabled() and stats["analiz"] > 0:
            stats_json = json.dumps(stats, ensure_ascii=False)
            if scope == TEAM:
                prompt = (
                    f"Ekip haftası ({week_start:%d.%m.%Y} haftası).\nSayılar:\n{stats_json}\n\n"
                    f"Görüşme notları:\n{_notes(rows, cap=120)}"
                )
                narrative = llm.complete_json(WEEKLY_SYSTEM, prompt, TEAM_SCHEMA, usage=used)
            else:
                name = rows[0].get("rep_name") or scope
                prompt = (
                    f"Temsilci: {name} ({week_start:%d.%m.%Y} haftası).\nSayılar:\n{stats_json}\n\n"
                    f"Görüşme notları:\n{_notes(rows)}"
                )
                narrative = llm.complete_json(WEEKLY_SYSTEM, prompt, REP_SCHEMA, usage=used)
        conn.execute(
            """
            INSERT INTO conv_weekly (org_id, week_start, scope, stats, narrative, ai_model, created_at, cost_usd)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, now(), %s)
            ON CONFLICT (org_id, week_start, scope) DO UPDATE SET
                stats = EXCLUDED.stats,
                narrative = coalesce(EXCLUDED.narrative, conv_weekly.narrative),
                ai_model = coalesce(EXCLUDED.ai_model, conv_weekly.ai_model),
                cost_usd = coalesce(conv_weekly.cost_usd, 0) + coalesce(EXCLUDED.cost_usd, 0),
                created_at = now()
            """,
            (
                get_org_id(), week_start, scope, json.dumps(stats, ensure_ascii=False),
                json.dumps(narrative, ensure_ascii=False) if narrative else None,
                llm.model_name() if narrative else None,
                sum((u.get("usd") or 0) for u in used) or None,
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
                report["kota"] = select_quota(conn, week_start, datetime.now(TZ).date())
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


# --- özel analiz talebi --------------------------------------------------------------


def request_calls(conn: Any, phone: str, days: int) -> list[dict[str, Any]]:
    """Numaranın son `days` gündeki kayıtlı çağrıları (temsilci kategorisinden bağımsız)."""
    cur = conn.execute(
        """
        SELECT e.id, e.rep_id, e.thread_id, e.direction, e.occurred_at, e.meta
        FROM events e
        WHERE e.org_id = %s AND e.channel = 'call'
          AND e.occurred_at >= now() - make_interval(days => %s)
          AND e.meta->>'voice_recording_url' IS NOT NULL
          AND (e.thread_id IN (SELECT thread_id FROM identities
                               WHERE org_id = %s AND id_type = 'phone' AND id_value = %s)
               OR e.meta->>'subject' LIKE %s)
        ORDER BY e.occurred_at
        """,
        (get_org_id(), days, get_org_id(), phone, f"%{phone}%"),
    )
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def create_request(phone: str, days: int, by: str) -> dict[str, Any]:
    """Numaranın kayıtlı çağrılarını analiz kuyruğuna alır. Zaten analiz edilmiş
    görüşmeye dokunmaz; talep onları da listeler."""
    with connect() as conn:
        _ready(conn)
        events = request_calls(conn, phone, days)
        rows = [r for r in (call_row(e) for e in events) if r is not None]
        ids: list[int] = []
        customer = next((r["customer"] for r in rows if r.get("customer")), None)
        for row in rows:
            _insert_items(conn, [row])
            item = conn.execute(
                """
                UPDATE conv_items SET meta = coalesce(meta, '{}'::jsonb) || jsonb_build_object('talep', true),
                       status = CASE WHEN status = 'atlandi' AND error IN (%s, %s)
                                     THEN CASE WHEN transcript IS NOT NULL THEN 'yazildi' ELSE 'bekliyor' END
                                     ELSE status END,
                       error = CASE WHEN error IN (%s, %s) THEN NULL ELSE error END
                WHERE org_id = %s AND kind = 'call' AND source_ref = %s RETURNING id
                """,
                (QUOTA_WAIT, QUOTA_OUT, QUOTA_WAIT, QUOTA_OUT, get_org_id(), row["source_ref"]),
            ).fetchone()
            ids.append(int(item[0]))
        req_id = conn.execute(
            """
            INSERT INTO conv_requests (org_id, phone, days, customer, requested_by, item_ids)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (get_org_id(), phone, days, customer, by, ids),
        ).fetchone()[0]
        conn.commit()
    return {"id": req_id, "kayitli": len(events), "kisa": len(events) - len(rows), "gorusme": len(ids)}


def load_requests(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        _ready(conn)
        reqs = conn.execute(
            """
            SELECT id, phone, days, customer, requested_by, created_at, item_ids
            FROM conv_requests WHERE org_id = %s ORDER BY created_at DESC LIMIT %s
            """,
            (get_org_id(), limit),
        ).fetchall()
        all_ids = sorted({i for r in reqs for i in r[6]})
        items: dict[int, dict[str, Any]] = {}
        if all_ids:
            cur = conn.execute(
                """
                SELECT c.id, c.kind, c.rep_id, r.full_name AS rep_name, c.customer, c.occurred_at,
                       c.duration_sec, c.crm_outcome, c.status, c.error, c.analysis
                FROM conv_items c LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
                WHERE c.org_id = %s AND c.id = ANY(%s)
                """,
                (get_org_id(), all_ids),
            )
            cols = [c.name for c in cur.description]
            items = {row[0]: _item_summary(dict(zip(cols, row))) for row in cur.fetchall()}
    out = []
    for rid, phone, days, customer, by, created, ids in reqs:
        rows = sorted((items[i] for i in ids if i in items), key=lambda x: x["occurred_at"])
        out.append({
            "id": rid, "phone": phone, "days": days, "customer": customer, "requested_by": by,
            "created_at": created, "items": rows,
            "durum": dict(Counter(x["status"] for x in rows)),
        })
    return out


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
    scores = [r["puan"] for r in a.get("sonar") or [] if isinstance(r.get("puan"), int) and r["puan"] > 0]
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
        "crm_not": (a.get("crm") or {}).get("not_var"),
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
        cost = conn.execute(
            """
            SELECT coalesce((SELECT sum(cost_usd) FROM conv_items
                             WHERE org_id = %s AND occurred_at >= %s AND occurred_at < %s), 0),
                   (SELECT count(*) FROM conv_items
                    WHERE org_id = %s AND occurred_at >= %s AND occurred_at < %s AND cost_usd IS NOT NULL),
                   coalesce((SELECT sum(cost_usd) FROM conv_weekly WHERE org_id = %s AND week_start = %s), 0)
            """,
            (get_org_id(), start, end, get_org_id(), start, end, get_org_id(), week_start),
        ).fetchone()
        weekly = conn.execute(
            """
            SELECT w.scope, r.full_name, w.stats, w.narrative, w.created_at, w.sent_at
            FROM conv_weekly w LEFT JOIN reps r ON r.org_id = w.org_id AND r.rep_id = w.scope
            WHERE w.org_id = %s AND w.week_start = %s
            """,
            (get_org_id(), week_start),
        ).fetchall()
    # Kota bekleyen/dışı kayıtlar listede yer almaz, yalnız sayılır.
    quota = [r for r in rows if r.get("error") in (QUOTA_WAIT, QUOTA_OUT)]
    rows = [r for r in rows if r.get("error") not in (QUOTA_WAIT, QUOTA_OUT)]
    status = Counter(r["status"] for r in rows)
    analysed = [r for r in rows if r["status"] == "analiz_edildi"]
    by_rep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in analysed:
        if r.get("rep_id"):
            by_rep[str(r["rep_id"])].append(r)
    team = next(
        ({"stats": s, "narrative": n, "created_at": c} for sc, _, s, n, c, _ in weekly if sc == TEAM), None
    )
    reps = [
        {"rep_id": sc, "name": name or sc, "stats": s, "narrative": n, "sent_at": sent,
         "dagilim": score_spread(by_rep.get(sc, []))}
        for sc, name, s, n, _, sent in weekly
        if sc != TEAM and (rep_id is None or sc == rep_id)
    ]
    reps.sort(key=lambda r: (-(r["stats"] or {}).get("analiz", 0), r["name"]))
    if rep_id is not None and team is not None:
        # Temsilci ekip sayılarını görür; başkalarına ait notları içeren ekip yorumunu görmez.
        team = {**team, "narrative": None}
    return {
        "week_start": week_start,
        "week_end": week_start + timedelta(days=6),
        "durum": {
            **{k: status.get(k, 0) for k in ("bekliyor", "yazildi", "analiz_edildi", "hata", "atlandi")},
            "kota_disi": len(quota),
        },
        "team": team,
        "reps": reps,
        "items": [_item_summary(r) for r in rows],
        "dagilim": score_spread(analysed),
        "maliyet": {"analiz_usd": float(cost[0]), "analiz_adet": int(cost[1]), "haftalik_usd": float(cost[2])},
        "bugs": bug_rows(analysed),
        "ornekler": example_calls(analysed),
        "ornek_temsilci": examples_by_rep(
            analysed,
            [(r.rep_id, r.full_name) for r in load_reps() if rep_id is None or r.rep_id == rep_id],
        ),
        "score_labels": SCORE_LABELS,
        "sonar": {
            "behaviors": {
                k: {"dim": b["dim"], "name": b["name"], "def": b["def"]} for k, b in sonar41.BEHAVIORS.items()
            },
            "crm": {k: c["name"] for k, c in sonar41.CRM_CRITERIA.items()},
        },
    }


def load_item(item_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        _ready(conn)
        cur = conn.execute(
            """
            SELECT c.id, c.kind, c.rep_id, r.full_name AS rep_name, c.customer, c.occurred_at,
                   c.duration_sec, c.crm_outcome, c.status, c.error, c.analysis, c.transcript,
                   c.speakers, c.stt_provider, c.ai_model, c.lead_id, e.body AS event_body
            FROM conv_items c LEFT JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
            LEFT JOIN events e ON e.id = c.event_id
            WHERE c.org_id = %s AND c.id = %s
            """,
            (get_org_id(), item_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [c.name for c in cur.description]
        out = dict(zip(cols, row))
        out["notes"] = call_notes(conn, out)
    out.pop("event_body", None)
    return out


# --- Cliq'e gönderim -------------------------------------------------------------


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}".replace(".", ",")


def share_text(name: str, week_start: date, stats: dict[str, Any], narrative: dict[str, Any]) -> str:
    """Temsilciye gidecek haftalık değerlendirme (Cliq biçimi, saf)."""
    end = week_start + timedelta(days=6)
    first = name.split()[0] if name else ""
    odak = narrative.get("odak") or {}
    scores = stats.get("puanlar") or {}
    lines = [
        f"*Görüşme değerlendirmen · {week_start:%d.%m} – {end:%d.%m.%Y}*",
        f"Merhaba {first}, bu hafta analiz edilen {stats.get('analiz', 0)} görüşmenin özeti:",
        "",
        narrative.get("ozet") or "",
    ]
    if odak.get("baslik"):
        code = f"{odak['kod']} · " if odak.get("kod") else ""
        lines += ["", f"*Bu haftanın odağı: {code}{odak['baslik']}*", odak.get("neden") or ""]
        if odak.get("alinti"):
            lines.append(f"> “{odak['alinti']}”")
        if odak.get("nasil"):
            lines.append(f"*Bir sonraki görüşmede:* {odak['nasil']}")
    for title, key in (("Güçlü yönlerin", "guclu"), ("Dikkat", "dikkat")):
        rows = [r for r in narrative.get(key) or [] if r]
        if rows:
            lines += ["", f"*{title}*", *[f"• {r}" for r in rows]]
    score_line = " · ".join(
        f"{SCORE_LABELS[k]} {_num(scores.get(k))}" for k in SCORE_KEYS if scores.get(k) is not None
    )
    lines += [
        "",
        f"SONAR-41 puanların (1-5): {score_line}" if score_line else "",
        f"CRM notu yazılan görüşme: %{_num((stats.get('crm') or {}).get('oran'))}",
        f"Net sonraki adım: %{_num(stats.get('sonraki_adim_orani'))} · tarihli: %{_num(stats.get('tarihli_adim_orani'))}",
        "Görüşme ayrıntıları ve alıntılar: Pusula → Görüşme Analizleri",
    ]
    return "\n".join(line for line in lines if line is not None).replace("\n\n\n", "\n\n").strip()


def load_share(week_start: date, rep_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        _ready(conn)
        row = conn.execute(
            """
            SELECT r.full_name, r.email, w.stats, w.narrative, w.sent_at
            FROM conv_weekly w JOIN reps r ON r.org_id = w.org_id AND r.rep_id = w.scope
            WHERE w.org_id = %s AND w.week_start = %s AND w.scope = %s
            """,
            (get_org_id(), week_start, rep_id),
        ).fetchone()
    if not row:
        return None
    name, email, stats, narrative, sent_at = row
    return {"name": name, "email": email, "stats": stats, "narrative": narrative, "sent_at": sent_at}


def mark_sent(week_start: date, rep_id: str, by: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE conv_weekly SET sent_at = now(), sent_by = %s
            WHERE org_id = %s AND week_start = %s AND scope = %s
            """,
            (by, get_org_id(), week_start, rep_id),
        )
        conn.commit()
