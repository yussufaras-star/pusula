"""Günlük dürtü üretir ve Cliq'e gönderir.

BUGUN (en fazla 3): pencere_aciliyor, planlanmis_arama, kayip_randevu,
gecikmis_taahhut. karar_bekleyen bu kotaya girmez.

KARAR BEKLEYEN (en fazla 2 + havuz): status='Düşünmek İstiyor' ve
status_changed_at 48 saatten eski.

Temas: scheduled değil, category <> 'not_reached' (süre yok).
Süre eşiği yalnız DÜN 10 sn+ görüşme satırında.

PUSULA_SHADOW_EMAIL dolu: tüm Cliq mesajları o adrese gider.
Boş veya tanımsız: gerçek temsilci emaillerine gider (hata değil).

Kullanım:
    python scripts/send_nudges.py
    python scripts/send_nudges.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from psycopg.types.json import Json
from dotenv import load_dotenv

from pusula.config import get_org_id
from pusula.freshness import print_call_stale_warning
from pusula.ingest.lead_identity import ensure_status_changed_columns
from pusula.lead_reach import is_dokunulmamis_sql
from pusula.sifir_satis import (
    ILK_SATIN_ALAN,
    classified_deals_cte,
    won_stage_sql,
)
from pusula.temas import (
    TEMAS_MIN_SEC,
    duration_sec,
    is_attempt_sql,
    is_temas_sql,
    outcome_join,
)

_TZ = ZoneInfo("Europe/Istanbul")
_MAX_PER_REP = 3
_MAX_LINE = 60
_KARAR_MAX = 2
_KARAR_HOURS = 48
_KARAR_STATUS = "Düşünmek İstiyor"
_AVG_MIN_TALKS = 5  # 10 sn+ ortalama basmak için min görüşme
_PENCERE_TYPE = "pencere_aciliyor"
_KARAR_TYPE = "karar_bekleyen"
_TYPE_ORDER = (
    "pencere_aciliyor",
    "planlanmis_arama",
    "kayip_randevu",
    "gecikmis_taahhut",
)
_MONTHS_SHORT = (
    "Oca", "Şub", "Mar", "Nis", "May", "Haz",
    "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara",
)
# Bu hafta snapshot satırları (bekleyen_lead yok: stok ayrı tek metrik).
_SNAPSHOT_LABELS = (
    ("kayip_randevu", "Kayıp randevu"),
    ("acik_taahhut", "Açık taahhüt"),
    ("tutulan_hafta", "Tutulan"),
    ("bozulan_hafta", "Bozulan"),
)

# Tek kaynak: pusula.temas
_DURATION_SEC = duration_sec("e")
_ATTEMPT_E = is_attempt_sql("e")
_TEMAS_E = is_temas_sql("e")
_TEMAS_E3 = is_temas_sql("e3")
_OUTCOME_JOIN = outcome_join  # callable(alias) -> SQL; geriye dönük .format yok

# Kanıt: yalnızca hesaplanmış sayı cümlesi. Sabit/genel ifade yok.
_EVIDENCE_TYPE_PRIORITY = (
    "gecikmis_taahhut",
    "kayip_randevu",
    "planlanmis_arama",
    "pencere_aciliyor",
)

_PENCERE_SQL = """
    SELECT
        l.owner_rep_id AS rep_id,
        l.thread_id,
        l.lead_id,
        NULL::bigint AS commitment_id,
        'pencere_aciliyor'::text AS nudge_type,
        public.pazarsiz_saat(
            coalesce(l.assigned_at, l.created_at), now()
        ) AS sort_key,
        (
            SELECT count(*)::int FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.occurred_at <= now()
              AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
              AND """ + _ATTEMPT_E + """
        ) AS outbound_calls,
        (
            SELECT count(*)::int
            FROM events e
""" + outcome_join("e") + """
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND e.occurred_at <= now()
              AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
              AND """ + _TEMAS_E + """
        ) AS temas_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = l.org_id
              AND i.thread_id = l.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        l.full_name AS contact_name,
        (""" + is_dokunulmamis_sql("l.status") + """) AS is_dokunulmamis,
        (
            SELECT max(e.occurred_at) FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.occurred_at <= now()
              AND """ + _ATTEMPT_E + """
        ) AS last_attempt_at,
        coalesce(l.assigned_at, l.created_at) AS assigned_at
    FROM leads l
    WHERE l.org_id = %s
      AND l.owner_rep_id IS NOT NULL
      AND l.thread_id IS NOT NULL
      AND (
            l.pusula_state = 'active'
            OR (""" + is_dokunulmamis_sql("l.status") + """)
      )
      AND (
            SELECT count(*)::int FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.occurred_at <= now()
              AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
              AND """ + _ATTEMPT_E + """
      ) < 3
    ORDER BY is_dokunulmamis DESC, sort_key DESC NULLS LAST
"""

_PLANLANMIS_SQL = """
    WITH overdue AS (
        SELECT
            e.org_id,
            e.thread_id,
            e.rep_id,
            e.occurred_at AS planned_at,
            nullif(e.meta->'what_id'->>'id', '') AS what_lead_id,
            row_number() OVER (
                PARTITION BY e.org_id, e.thread_id
                ORDER BY e.occurred_at ASC
            ) AS rn
        FROM events e
        WHERE e.org_id = %s
          AND e.channel = 'call'
          AND e.meta->>'call_status' = 'overdue'
          AND e.occurred_at < now()
          AND e.thread_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM events e2
              WHERE e2.org_id = e.org_id
                AND e2.thread_id = e.thread_id
                AND e2.channel = 'call'
                AND e2.occurred_at > e.occurred_at
                AND coalesce(e2.meta->>'scheduled', 'false') <> 'true'
                AND e2.meta->>'call_status' = 'connected'
          )
    )
    SELECT
        coalesce(o.rep_id, t.owner_rep_id) AS rep_id,
        o.thread_id,
        coalesce(
            (
                SELECT l.lead_id FROM leads l
                WHERE l.org_id = o.org_id AND l.lead_id = o.what_lead_id
                LIMIT 1
            ),
            (
                SELECT l.lead_id FROM leads l
                WHERE l.org_id = o.org_id AND l.thread_id = o.thread_id
                ORDER BY l.assigned_at DESC NULLS LAST
                LIMIT 1
            )
        ) AS lead_id,
        NULL::bigint AS commitment_id,
        'planlanmis_arama'::text AS nudge_type,
        extract(epoch FROM o.planned_at) AS sort_key,
        NULL::int AS outbound_calls,
        NULL::int AS temas_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = o.org_id
              AND i.thread_id = o.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        o.planned_at,
        coalesce(
            (
                SELECT l.full_name FROM leads l
                WHERE l.org_id = o.org_id
                  AND l.lead_id = o.what_lead_id
                  AND l.full_name IS NOT NULL
                LIMIT 1
            ),
            (
                SELECT l.full_name FROM leads l
                WHERE l.org_id = o.org_id
                  AND l.thread_id = o.thread_id
                  AND l.full_name IS NOT NULL
                ORDER BY l.assigned_at DESC NULLS LAST
                LIMIT 1
            )
        ) AS contact_name
    FROM overdue o
    JOIN threads t
      ON t.org_id = o.org_id AND t.thread_id = o.thread_id
    WHERE o.rn = 1
      AND coalesce(o.rep_id, t.owner_rep_id) IS NOT NULL
    ORDER BY o.planned_at ASC
"""

_KAYIP_SQL = """
    WITH randevu AS (
        SELECT
            e.org_id,
            e.thread_id,
            e.rep_id,
            e.occurred_at AS randevu_at,
            row_number() OVER (
                PARTITION BY e.org_id, e.thread_id
                ORDER BY e.occurred_at DESC
            ) AS rn
        FROM events e
        WHERE e.org_id = %s
          AND (
              e.meta->>'outcome_key' = 'meeting_booked'
              OR e.meta->>'call_result' = 'Randevu Alındı'
          )
          AND e.occurred_at <= now()
    )
    SELECT
        coalesce(r.rep_id, t.owner_rep_id) AS rep_id,
        r.thread_id,
        (
            SELECT l.lead_id FROM leads l
            WHERE l.org_id = r.org_id AND l.thread_id = r.thread_id
            ORDER BY l.assigned_at DESC NULLS LAST
            LIMIT 1
        ) AS lead_id,
        NULL::bigint AS commitment_id,
        'kayip_randevu'::text AS nudge_type,
        extract(epoch FROM r.randevu_at) AS sort_key,
        NULL::int AS outbound_calls,
        NULL::int AS temas_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = r.org_id
              AND i.thread_id = r.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        r.randevu_at,
        (
            SELECT coalesce(
                (
                    SELECT l.full_name FROM leads l
                    WHERE l.org_id = r.org_id AND l.thread_id = r.thread_id
                      AND l.full_name IS NOT NULL
                    ORDER BY l.assigned_at DESC NULLS LAST
                    LIMIT 1
                ),
                (
                    SELECT ct.full_name FROM contacts ct
                    WHERE ct.org_id = r.org_id AND ct.thread_id = r.thread_id
                      AND ct.full_name IS NOT NULL
                    ORDER BY ct.created_at DESC NULLS LAST
                    LIMIT 1
                )
            )
        ) AS contact_name
    FROM randevu r
    JOIN threads t
      ON t.org_id = r.org_id AND t.thread_id = r.thread_id
    WHERE r.rn = 1
      AND coalesce(r.rep_id, t.owner_rep_id) IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM events e2
          WHERE e2.org_id = r.org_id
            AND e2.thread_id = r.thread_id
            AND (
                e2.meta->>'outcome_key' = 'demo_done'
                OR e2.meta->>'call_result' = 'Sunum Yapıldı'
            )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM events e3
""" + outcome_join("e3") + """
          WHERE e3.org_id = r.org_id
            AND e3.thread_id = r.thread_id
            AND e3.occurred_at > r.randevu_at
            AND e3.occurred_at <= now()
            AND coalesce(e3.meta->>'scheduled', 'false') <> 'true'
            AND (
                (e3.channel = 'call' AND e3.direction = 'inbound')
                OR e3.channel = 'meeting'
                OR (
                    e3.channel = 'call'
                    AND """ + _TEMAS_E3 + """
                )
            )
      )
    ORDER BY r.randevu_at DESC
"""

_TAAHHUT_SQL = """
    SELECT
        l.owner_rep_id AS rep_id,
        c.thread_id,
        l.lead_id,
        c.id AS commitment_id,
        'gecikmis_taahhut'::text AS nudge_type,
        extract(epoch FROM c.due_at) AS sort_key,
        NULL::int AS outbound_calls,
        NULL::int AS temas_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = c.org_id
              AND i.thread_id = c.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        c.due_at,
        c.text AS commitment_text,
        l.full_name AS contact_name
    FROM commitments c
    JOIN LATERAL (
        SELECT lead_id, owner_rep_id, full_name
        FROM leads
        WHERE org_id = c.org_id AND thread_id = c.thread_id
        ORDER BY assigned_at DESC NULLS LAST
        LIMIT 1
    ) l ON true
    WHERE c.org_id = %s
      AND c.status = 'broken'
      AND c.due_at IS NOT NULL
      AND c.due_at >= now() - interval '14 days'
      AND c.due_at < now()
      AND c.thread_id IS NOT NULL
      AND l.owner_rep_id IS NOT NULL
    ORDER BY c.due_at DESC
"""

_KARAR_SQL = """
    SELECT
        l.owner_rep_id AS rep_id,
        l.thread_id,
        l.lead_id,
        NULL::bigint AS commitment_id,
        'karar_bekleyen'::text AS nudge_type,
        extract(epoch FROM l.status_changed_at) AS sort_key,
        NULL::int AS outbound_calls,
        NULL::int AS temas_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = l.org_id
              AND i.thread_id = l.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        l.full_name AS contact_name,
        l.status_changed_at,
        (
            SELECT max(e.occurred_at)
            FROM events e
""" + outcome_join("e") + """
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.occurred_at <= now()
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND (
                    (e.channel = 'call' AND """ + _TEMAS_E + """)
                    OR e.channel = 'meeting'
              )
        ) AS last_temas_at
    FROM leads l
    WHERE l.org_id = %s
      AND l.owner_rep_id IS NOT NULL
      AND l.thread_id IS NOT NULL
      AND l.status = %s
      AND l.status_changed_at IS NOT NULL
      AND l.status_changed_at < now() - make_interval(hours => %s)
    ORDER BY l.status_changed_at ASC
"""

_DEDUP_SQL = """
    SELECT 1
    FROM nudges n
    WHERE n.org_id = %s
      AND n.rep_id = %s
      AND n.nudge_type = %s
      AND n.thread_id = %s
      AND n.sent_at >= now() - interval '3 days'
    LIMIT 1
"""

_WEEK_SNAPSHOT_SQL = """
    SELECT
        snapshot_date,
        bekleyen_lead,
        kayip_randevu,
        acik_taahhut,
        tutulan_hafta,
        bozulan_hafta
    FROM rep_snapshots
    WHERE rep_id = %s
    ORDER BY snapshot_date DESC
"""

# Dün / süre: yalnız duration >= TEMAS_MIN_SEC (bağlanmadı paydada yok).
_DUNDEN_SQL = """
    SELECT
        count(*)::int AS talks,
        count(*) FILTER (
            WHERE coalesce(nullif(trim(e.meta->>'call_result'), ''), '') = ''
        )::int AS empty_result,
        coalesce(sum(""" + _DURATION_SEC + """), 0)::float AS total_sec,
        coalesce(avg(""" + _DURATION_SEC + """), 0)::float AS avg_sec
    FROM events e
    WHERE e.org_id = %s
      AND e.rep_id = %s
      AND e.channel = 'call'
      AND e.direction = 'outbound'
      AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
      AND """ + _DURATION_SEC + """ >= """ + str(TEMAS_MIN_SEC) + """
      AND e.occurred_at <= now()
      AND e.occurred_at >= %s
      AND e.occurred_at < %s
"""

# Ekip ortalaması: yalnız sales, mesaj sahibi hariç.
_DUNDEN_TEAM_SQL = """
    SELECT
        count(*)::int AS talks,
        coalesce(avg(""" + _DURATION_SEC + """), 0)::float AS avg_sec
    FROM events e
    JOIN reps r
      ON r.org_id = e.org_id AND r.rep_id = e.rep_id
    WHERE e.org_id = %s
      AND e.rep_id IS DISTINCT FROM %s
      AND r.category = 'sales'
      AND r.active = true
      AND e.channel = 'call'
      AND e.direction = 'outbound'
      AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
      AND """ + _DURATION_SEC + """ >= """ + str(TEMAS_MIN_SEC) + """
      AND e.occurred_at <= now()
      AND e.occurred_at >= %s
      AND e.occurred_at < %s
"""

# Dün tüm arama (süresiz): pencere + bağlanmadı.
_DUN_CALLS_SQL = """
    SELECT
        count(*)::int AS total_calls,
        count(*) FILTER (
            WHERE co.category = 'not_reached'
        )::int AS not_reached,
        min(e.occurred_at) AS first_at,
        max(e.occurred_at) AS last_at
    FROM events e
""" + outcome_join("e") + """
    WHERE e.org_id = %s
      AND e.rep_id = %s
      AND e.channel = 'call'
      AND e.direction = 'outbound'
      AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
      AND e.occurred_at <= now()
      AND e.occurred_at >= %s
      AND e.occurred_at < %s
"""


@dataclass
class NudgeCandidate:
    rep_id: str
    thread_id: str
    lead_id: str | None
    commitment_id: int | None
    nudge_type: str
    sort_key: float
    phone: str | None
    contact_name: str | None = None
    outbound_calls: int | None = None
    temas_calls: int | None = None
    randevu_at: datetime | None = None
    due_at: datetime | None = None
    planned_at: datetime | None = None
    commitment_text: str | None = None
    is_dokunulmamis: bool = False
    last_attempt_at: datetime | None = None
    assigned_at: datetime | None = None
    status_changed_at: datetime | None = None
    last_temas_at: datetime | None = None


@dataclass(frozen=True)
class WeekSnapshotPair:
    this_date: date
    last_date: date
    this_vals: dict[str, int]
    last_vals: dict[str, int]


@dataclass(frozen=True)
class DundenStats:
    talks: int
    empty_result: int
    total_sec: float | None = None
    avg_sec: float | None = None
    week4_avg_sec: float | None = None
    total_calls: int = 0
    not_reached: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None


@dataclass(frozen=True)
class NetFlow:
    """Bu hafta stok hareketi (olaylardan; snapshot farkı değil)."""

    closed: int
    added: int
    reason: str | None = None  # sifirsa neden (sessiz 0 yok)


def _fmt_dt(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "?"
    local = value.astimezone(_TZ) if value.tzinfo else value.replace(tzinfo=_TZ)
    ref = now or datetime.now(_TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_TZ)
    else:
        ref = ref.astimezone(_TZ)
    months = _MONTHS_SHORT
    age_days = (ref.date() - local.date()).days
    base = f"{local.day} {months[local.month - 1]}"
    if age_days > 90:
        base = f"{base} {local.year}"
    if age_days > 180:
        month_delta = (ref.year - local.year) * 12 + (ref.month - local.month)
        if ref.day < local.day:
            month_delta -= 1
        month_delta = max(1, month_delta)
        base = f"{base} ({month_delta} ay önce)"
    return base


def _fmt_snapshot_date(d: date) -> str:
    return f"{d.day} {_MONTHS_SHORT[d.month - 1]} {d.year}"


def _fmt_gun(value: datetime | None) -> str | None:
    """Gün + ay, sayı yoksa None."""
    if value is None:
        return None
    local = value.astimezone(_TZ) if value.tzinfo else value.replace(tzinfo=_TZ)
    return f"{local.day} {_MONTHS_SHORT[local.month - 1]}"


def _fmt_saat(value: datetime | None) -> str | None:
    if value is None:
        return None
    local = value.astimezone(_TZ) if value.tzinfo else value.replace(tzinfo=_TZ)
    return f"{local.hour:02d}:{local.minute:02d}"


def _clip(text: str, n: int = _MAX_LINE) -> str:
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = max(0, int(round(float(seconds))))
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        if secs:
            return f"{hours} sa {minutes} dk {secs} sn"
        if minutes:
            return f"{hours} sa {minutes} dk"
        return f"{hours} sa"
    if secs:
        return f"{minutes} dk {secs} sn"
    return f"{minutes} dk"


def _fmt_total_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    mins = max(0, int(round(float(seconds) / 60.0)))
    return f"{mins} dk"


def _fmt_hours(value: Any) -> str:
    if value is None:
        return "?"
    hours = float(value)
    if hours >= 48:
        days = hours / 24.0
        return f"{days:.0f} gün"
    return f"{hours:.0f} saat"


def _identity_label(n: NudgeCandidate) -> str:
    phone = n.phone or "telefon yok"
    name = (n.contact_name or "").strip()
    if name:
        return _clip(f"{name} - {phone}")
    return _clip(phone)


def _days_since(value: datetime | None, *, now: datetime | None = None) -> int | None:
    if value is None:
        return None
    local = value.astimezone(_TZ) if value.tzinfo else value.replace(tzinfo=_TZ)
    ref = now or datetime.now(_TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_TZ)
    else:
        ref = ref.astimezone(_TZ)
    delta = ref - local
    days = int(delta.total_seconds() // 86400)
    return max(0, days)


def _reason_for(n: NudgeCandidate, *, now: datetime | None = None) -> str | None:
    """Sinyal şablonu; tarih/sayı yoksa None (satır basılmaz)."""
    if n.nudge_type == "kayip_randevu":
        gun = _fmt_gun(n.randevu_at)
        if gun is None:
            return None
        return _clip(f"{gun} randevu alındı, sonrasında temas yok")
    if n.nudge_type == "planlanmis_arama":
        gun = _fmt_gun(n.planned_at)
        if gun is None:
            return None
        return _clip(f"{gun}'ya planlanan arama yapılmadı")
    if n.nudge_type == "gecikmis_taahhut":
        gun = _fmt_gun(n.due_at)
        if gun is None:
            return None
        return _clip(f"{gun} vadeli söz, arama yapılmadı")
    if n.nudge_type == "karar_bekleyen":
        days = _days_since(n.status_changed_at, now=now)
        gun = _fmt_gun(n.last_temas_at)
        if days is None or gun is None:
            return None
        return _clip(f"{days} gündür karar verilmedi, son temas {gun}")
    if n.nudge_type == "pencere_aciliyor":
        calls = n.outbound_calls if n.outbound_calls is not None else None
        gun = _fmt_gun(n.last_attempt_at) or _fmt_gun(n.assigned_at)
        if calls is None or gun is None:
            return None
        if n.last_attempt_at is not None:
            return _clip(f"{calls} deneme, son {gun}")
        return _clip(f"{gun}'dan beri {calls} deneme")
    return None


def _line_for(n: NudgeCandidate) -> str:
    who = _identity_label(n)
    reason = _reason_for(n)
    if reason:
        return f"{who} - {reason}"
    return who


def _is_first_business_day(now: datetime | None = None) -> bool:
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    return local.weekday() == 0


def _evidence_section(
    suitable: dict[str, int],
    *,
    now: datetime | None = None,
) -> tuple[str, str] | None:
    """İlk iş gününde, yalnızca hesaplanmış sayı içeren cümle.

    Sayı üretilemiyorsa None (sabit/genel ifade basılmaz).
    """
    if not _is_first_business_day(now):
        return None
    nudge_type = None
    for t in _EVIDENCE_TYPE_PRIORITY:
        if suitable.get(t, 0) > 0:
            nudge_type = t
            break
    if nudge_type is None:
        return None
    count = suitable[nudge_type]
    if count <= 0:
        return None
    if nudge_type == "gecikmis_taahhut":
        line = f"Son 14 günde {count} gecikmiş taahhüt."
    elif nudge_type == "kayip_randevu":
        line = f"Şu an {count} kayıp randevu adayı."
    elif nudge_type == "planlanmis_arama":
        line = f"Şu an {count} planlanmış arama gecikmiş."
    else:
        line = f"Penceresi açık {count} lead."
    return nudge_type, line


def _first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else full_name


def _row_to_snapshot_vals(row: tuple[Any, ...]) -> dict[str, int]:
    # row: date, bekleyen_lead, kayip, acik, tutulan, bozulan
    return {
        "kayip_randevu": int(row[2] or 0),
        "acik_taahhut": int(row[3] or 0),
        "tutulan_hafta": int(row[4] or 0),
        "bozulan_hafta": int(row[5] or 0),
    }


def _istanbul_week_bounds(
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    monday = local.date() - timedelta(days=local.weekday())
    start = datetime(monday.year, monday.month, monday.day, tzinfo=_TZ)
    return start, start + timedelta(days=7)


def _load_week_compare(
    conn: psycopg.Connection,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> WeekSnapshotPair | None:
    """En son snapshot ile en az 7 gün önceki bir önceki; yoksa None."""
    del now
    rows = conn.execute(_WEEK_SNAPSHOT_SQL, (rep_id,)).fetchall()
    if len(rows) < 2:
        return None
    this_row = rows[0]
    this_date = this_row[0]
    for prev in rows[1:]:
        prev_date = prev[0]
        if (this_date - prev_date).days >= 7:
            return WeekSnapshotPair(
                this_date=this_date,
                last_date=prev_date,
                this_vals=_row_to_snapshot_vals(this_row),
                last_vals=_row_to_snapshot_vals(prev),
            )
    return None


def _load_net_flow(
    conn: psycopg.Connection,
    org_id: str,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> NetFlow:
    """Bu hafta kapanan / yeni eklenen — olaylardan (snapshot farkı değil).

    Kapanan: temas outbound + overdue sonrası connected + fulfilled commitment.
    Yeni: assigned lead + yeni overdue + meeting_booked + broken due_at.
    """
    week_start, week_end = _istanbul_week_bounds(now)
    closed_temas = conn.execute(
        """
        SELECT count(DISTINCT e.thread_id)::int
        FROM events e
        """
        + outcome_join("e")
        + """
        WHERE e.org_id = %s
          AND e.rep_id = %s
          AND e.channel = 'call'
          AND e.direction = 'outbound'
          AND """
        + _TEMAS_E
        + """
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s
          AND e.occurred_at < %s
        """,
        (org_id, rep_id, week_start, week_end),
    ).fetchone()
    closed_overdue = conn.execute(
        """
        SELECT count(DISTINCT e.thread_id)::int
        FROM events e
        WHERE e.org_id = %s
          AND e.rep_id = %s
          AND e.channel = 'call'
          AND e.meta->>'call_status' = 'connected'
          AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s
          AND e.occurred_at < %s
          AND EXISTS (
              SELECT 1 FROM events o
              WHERE o.org_id = e.org_id
                AND o.thread_id = e.thread_id
                AND o.channel = 'call'
                AND o.meta->>'call_status' = 'overdue'
                AND o.occurred_at < e.occurred_at
                AND o.occurred_at <= now()
          )
        """,
        (org_id, rep_id, week_start, week_end),
    ).fetchone()
    closed_commit = conn.execute(
        """
        SELECT count(*)::int
        FROM commitments c
        JOIN events fe ON fe.id = c.fulfilled_event_id
        JOIN LATERAL (
            SELECT owner_rep_id
            FROM leads
            WHERE org_id = c.org_id AND thread_id = c.thread_id
            ORDER BY assigned_at DESC NULLS LAST
            LIMIT 1
        ) l ON true
        WHERE c.org_id = %s
          AND c.status = 'fulfilled'
          AND l.owner_rep_id = %s
          AND fe.occurred_at <= now()
          AND fe.occurred_at >= %s
          AND fe.occurred_at < %s
        """,
        (org_id, rep_id, week_start, week_end),
    ).fetchone()

    added_leads = conn.execute(
        """
        SELECT count(*)::int
        FROM leads
        WHERE org_id = %s
          AND owner_rep_id = %s
          AND assigned_at >= %s
          AND assigned_at < %s
        """,
        (org_id, rep_id, week_start, week_end),
    ).fetchone()
    added_overdue = conn.execute(
        """
        SELECT count(DISTINCT e.thread_id)::int
        FROM events e
        WHERE e.org_id = %s
          AND e.rep_id = %s
          AND e.channel = 'call'
          AND e.meta->>'call_status' = 'overdue'
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s
          AND e.occurred_at < %s
        """,
        (org_id, rep_id, week_start, week_end),
    ).fetchone()
    added_meeting = conn.execute(
        """
        SELECT count(DISTINCT e.thread_id)::int
        FROM events e
        WHERE e.org_id = %s
          AND e.rep_id = %s
          AND e.channel = 'call'
          AND (
              e.meta->>'outcome_key' = 'meeting_booked'
              OR e.meta->>'call_result' = 'Randevu Alındı'
          )
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s
          AND e.occurred_at < %s
        """,
        (org_id, rep_id, week_start, week_end),
    ).fetchone()
    added_broken = conn.execute(
        """
        SELECT count(*)::int
        FROM commitments c
        JOIN LATERAL (
            SELECT owner_rep_id
            FROM leads
            WHERE org_id = c.org_id AND thread_id = c.thread_id
            ORDER BY assigned_at DESC NULLS LAST
            LIMIT 1
        ) l ON true
        WHERE c.org_id = %s
          AND c.status = 'broken'
          AND l.owner_rep_id = %s
          AND c.due_at >= %s
          AND c.due_at < %s
        """,
        (org_id, rep_id, week_start, week_end),
    ).fetchone()

    closed = (
        int(closed_temas[0] or 0)
        + int(closed_overdue[0] or 0)
        + int(closed_commit[0] or 0)
    )
    added = (
        int(added_leads[0] or 0)
        + int(added_overdue[0] or 0)
        + int(added_meeting[0] or 0)
        + int(added_broken[0] or 0)
    )
    reason: str | None = None
    if closed == 0 and added == 0:
        reason = (
            f"bu hafta ({week_start.date()}..{week_end.date()}) "
            f"kapanış/ekleme olayı yok "
            f"[kapanan: temas={int(closed_temas[0] or 0)} "
            f"overdue→connected={int(closed_overdue[0] or 0)} "
            f"fulfilled={int(closed_commit[0] or 0)}; "
            f"yeni: lead={int(added_leads[0] or 0)} "
            f"overdue={int(added_overdue[0] or 0)} "
            f"randevu={int(added_meeting[0] or 0)} "
            f"broken={int(added_broken[0] or 0)}]"
        )
    return NetFlow(closed=closed, added=added, reason=reason)


def _parse_extra_recipients() -> list[str]:
    raw = os.environ.get("PUSULA_EXTRA_RECIPIENTS") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _shadow_email() -> str | None:
    """Boş / tanımsız = canlı gönderim; dolu = tüm mesajlar bu adrese."""
    raw = os.environ.get("PUSULA_SHADOW_EMAIL")
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _print_delivery_mode(shadow: str | None) -> None:
    if shadow:
        print(f"GOLGE MOD: tum mesajlar {shadow} adresine gidiyor")
    else:
        print("CANLI: mesajlar gercek temsilcilere gidecek")


def _load_recipients(
    conn: psycopg.Connection, org_id: str
) -> dict[str, tuple[str, str | None, str]]:
    """sales + PUSULA_EXTRA_RECIPIENTS → rep_id → (name, email, category)."""
    sales = conn.execute(
        """
        SELECT rep_id, full_name, email, category
        FROM reps
        WHERE org_id = %s
          AND active = true
          AND category = 'sales'
        ORDER BY full_name
        """,
        (org_id,),
    ).fetchall()
    out: dict[str, tuple[str, str | None, str]] = {
        str(r[0]): (str(r[1]), str(r[2]) if r[2] else None, str(r[3]))
        for r in sales
    }
    extras = _parse_extra_recipients()
    if extras:
        rows = conn.execute(
            """
            SELECT rep_id, full_name, email, category
            FROM reps
            WHERE org_id = %s AND rep_id = ANY(%s)
            """,
            (org_id, extras),
        ).fetchall()
        found = {
            str(r[0]): (str(r[1]), str(r[2]) if r[2] else None, str(r[3]))
            for r in rows
        }
        for rid in extras:
            if rid in found:
                out[rid] = found[rid]
            else:
                print(f"uyarı: PUSULA_EXTRA_RECIPIENTS bilinmeyen rep_id={rid}")
    elif not out:
        print("uyarı: sales yok ve PUSULA_EXTRA_RECIPIENTS boş")
    return out


def _count_talks(
    conn: psycopg.Connection,
    org_id: str,
    day: date,
    *,
    category: str | None = "sales",
) -> int:
    """Belirli günde 10 sn+ outbound çağrı sayısı (scheduled hariç)."""
    y0, y1 = _day_bounds(day)
    if category:
        row = conn.execute(
            """
            SELECT count(*)::int
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND r.category = %s
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND """
            + _DURATION_SEC
            + """ >= """ + str(TEMAS_MIN_SEC) + """
              AND e.occurred_at <= now()
              AND e.occurred_at >= %s
              AND e.occurred_at < %s
            """,
            (org_id, category, y0, y1),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT count(*)::int
            FROM events e
            WHERE e.org_id = %s
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND """
            + _DURATION_SEC
            + """ >= """ + str(TEMAS_MIN_SEC) + """
              AND e.occurred_at <= now()
              AND e.occurred_at >= %s
              AND e.occurred_at < %s
            """,
            (org_id, y0, y1),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    return start, start + timedelta(days=1)


def _load_dunden(
    conn: psycopg.Connection,
    org_id: str,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> DundenStats:
    """Dün görüşme + arama penceresi; süre yalnız 10 sn+."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    yesterday = local.date() - timedelta(days=1)
    y0, y1 = _day_bounds(yesterday)
    row = conn.execute(_DUNDEN_SQL, (org_id, rep_id, y0, y1)).fetchone()
    talks = int(row[0] or 0) if row else 0
    empty = int(row[1] or 0) if row else 0
    total_sec = float(row[2] or 0) if row else 0.0
    avg_sec = float(row[3] or 0) if row else 0.0

    calls = conn.execute(_DUN_CALLS_SQL, (org_id, rep_id, y0, y1)).fetchone()
    total_calls = int(calls[0] or 0) if calls else 0
    not_reached = int(calls[1] or 0) if calls else 0
    first_at = calls[2] if calls else None
    last_at = calls[3] if calls else None

    w0 = y1 - timedelta(days=28)
    week4 = conn.execute(_DUNDEN_SQL, (org_id, rep_id, w0, y1)).fetchone()
    week4_talks = int(week4[0] or 0) if week4 else 0
    week4_avg: float | None = None
    if week4_talks > 0:
        week4_avg = float(week4[3] or 0)

    return DundenStats(
        talks=talks,
        empty_result=empty,
        total_sec=total_sec if talks > 0 else None,
        avg_sec=avg_sec if talks > 0 else None,
        week4_avg_sec=week4_avg,
        total_calls=total_calls,
        not_reached=not_reached,
        first_at=first_at,
        last_at=last_at,
    )


def _load_ilk_satin(
    conn: psycopg.Connection,
    org_id: str,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Bu hafta / önceki hafta ilk kez satın alan (kazanılan, lead owner)."""
    this_start, this_end = _istanbul_week_bounds(now)
    prev_start = this_start - timedelta(days=7)
    cte = classified_deals_cte()
    won = won_stage_sql("cl")
    sql = f"""
        WITH {cte}
        SELECT
            count(*) FILTER (
                WHERE {won}
                  AND cl.closed_at >= %s AND cl.closed_at < %s
            )::int,
            count(*) FILTER (
                WHERE {won}
                  AND cl.closed_at >= %s AND cl.closed_at < %s
            )::int
        FROM classified cl
        WHERE cl.lead_owner_rep_id = %s
          AND cl.kind = '{ILK_SATIN_ALAN}'
    """
    row = conn.execute(
        sql,
        (org_id, this_start, this_end, prev_start, this_start, rep_id),
    ).fetchone()
    return (int(row[0] or 0) if row else 0, int(row[1] or 0) if row else 0)


def _build_message(
    *,
    rep_name: str,
    rep_email: str | None,
    selected: list[NudgeCandidate],
    karar: list[NudgeCandidate],
    karar_pool: int,
    dunden: DundenStats | None = None,
    ilk_this: int = 0,
    ilk_prev: int = 0,
) -> str:
    who = rep_name
    if rep_email:
        who = f"{rep_name} <{rep_email}>"
    parts: list[str] = [
        f"[gölge] {who} için",
        "",
        "BUGUN",
    ]
    for n in selected:
        reason = _reason_for(n)
        if reason is None:
            continue
        parts.append(_identity_label(n))
        parts.append(reason)

    parts.append("")
    parts.append(_clip(f"KARAR BEKLEYEN — {karar_pool}"))
    for n in karar:
        reason = _reason_for(n)
        if reason is None:
            continue
        parts.append(_identity_label(n))
        parts.append(reason)
    if karar:
        parts.append(
            _clip("Zoho'da güncelle: randevu / kaybetti / tekrar ara")
        )

    parts.append("")
    parts.append("DUN")
    if dunden is None:
        parts.append("görüşme 0")
    else:
        parts.append(f"görüşme {dunden.talks}")
        if dunden.total_sec is not None:
            parts.append(f"toplam süre {_fmt_total_minutes(dunden.total_sec)}")
        if dunden.avg_sec is not None:
            parts.append(f"ortalama {_fmt_duration(dunden.avg_sec)}")
        if dunden.week4_avg_sec is not None:
            parts.append(
                f"4 hafta ortalama {_fmt_duration(dunden.week4_avg_sec)}"
            )
        first_s = _fmt_saat(dunden.first_at)
        last_s = _fmt_saat(dunden.last_at)
        if first_s and last_s:
            parts.append(f"pencere {first_s}-{last_s}")
        if dunden.total_calls > 0:
            parts.append(
                f"arama {dunden.total_calls}, bağlanmadı {dunden.not_reached}"
            )
        if dunden.talks > 0:
            parts.append(f"sonuç boş {dunden.empty_result}")

    parts.append("")
    parts.append("BU HAFTA")
    parts.append(f"ilk kez satın alan {ilk_this} / {ilk_prev}")
    parts.append(f"karar bekleyen {karar_pool}")
    return "\n".join(parts).rstrip() + "\n"


def _is_dup(conn: psycopg.Connection, org_id: str, n: NudgeCandidate) -> bool:
    row = conn.execute(
        _DEDUP_SQL, (org_id, n.rep_id, n.nudge_type, n.thread_id)
    ).fetchone()
    return row is not None


def _print_taahhut_funnel(conn: psycopg.Connection, org_id: str) -> None:
    """gecikmis_taahhut filtre adımlarını bas (temas bu sorguda yok)."""
    print("--- gecikmis_taahhut filtre ---")
    totals = conn.execute(
        """
        SELECT
          count(*) FILTER (WHERE status = 'broken') AS broken,
          count(*) FILTER (
            WHERE status = 'broken' AND due_at IS NOT NULL
          ) AS with_due,
          count(*) FILTER (
            WHERE status = 'broken' AND due_at IS NOT NULL AND due_at < now()
          ) AS past_due,
          count(*) FILTER (
            WHERE status = 'broken'
              AND due_at >= now() - interval '14 days'
              AND due_at < now()
          ) AS in_14d,
          count(*) FILTER (
            WHERE status = 'broken'
              AND due_at >= now() - interval '14 days'
              AND due_at < now()
              AND thread_id IS NOT NULL
          ) AS with_thread,
          count(*) FILTER (
            WHERE status = 'broken'
              AND due_at >= now() - interval '14 days'
              AND due_at < now()
              AND thread_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM leads l
                WHERE l.org_id = c.org_id AND l.thread_id = c.thread_id
                  AND l.owner_rep_id IS NOT NULL
              )
          ) AS with_owner
        FROM commitments c
        WHERE org_id = %s
        """,
        (org_id,),
    ).fetchone()
    print(
        f"  ham broken={totals[0]} due_dolu={totals[1]} "
        f"gecmis_due={totals[2]} son_14g={totals[3]} "
        f"threadli={totals[4]} owner_lead={totals[5]}"
    )
    if int(totals[2] or 0) > 0 and int(totals[3] or 0) == 0:
        print(
            "  not: son 14g=0 — due_at'ler 14 günden eski "
            "(temas filtresi bu sinyalde yok)"
        )
    elif int(totals[3] or 0) > int(totals[5] or 0):
        print(
            "  not: owner_lead < son_14g — thread'de owner'lı lead yok"
        )

    per_rep = conn.execute(
        """
        SELECT r.full_name,
          count(*) FILTER (
            WHERE c.status = 'broken' AND c.due_at < now()
          ) AS past_due,
          count(*) FILTER (
            WHERE c.status = 'broken'
              AND c.due_at >= now() - interval '14 days'
              AND c.due_at < now()
          ) AS in_14d
        FROM commitments c
        JOIN LATERAL (
          SELECT owner_rep_id FROM leads
          WHERE org_id = c.org_id AND thread_id = c.thread_id
          ORDER BY assigned_at DESC NULLS LAST
          LIMIT 1
        ) l ON true
        JOIN reps r ON r.org_id = c.org_id AND r.rep_id = l.owner_rep_id
        WHERE c.org_id = %s
          AND r.category = 'sales'
          AND r.active = true
        GROUP BY r.full_name
        HAVING count(*) FILTER (
            WHERE c.status = 'broken' AND c.due_at < now()
          ) > 0
        ORDER BY r.full_name
        """,
        (org_id,),
    ).fetchall()
    for name, past, in14 in per_rep:
        elenen = int(past or 0) - int(in14 or 0)
        print(
            f"  {name}: gecmis_due={past} -> son_14g={in14} "
            f"(14g dışı elenen={elenen})"
        )


def _load_candidates(conn: psycopg.Connection, org_id: str) -> list[NudgeCandidate]:
    out: list[NudgeCandidate] = []

    for row in conn.execute(_PENCERE_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, temas_calls, phone, contact_name,
            is_dokunulmamis, last_attempt_at, assigned_at,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                outbound_calls=int(outbound_calls or 0),
                temas_calls=int(temas_calls or 0),
                is_dokunulmamis=bool(is_dokunulmamis),
                last_attempt_at=last_attempt_at,
                assigned_at=assigned_at,
            )
        )

    for row in conn.execute(_PLANLANMIS_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, temas_calls, phone, planned_at,
            contact_name,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                planned_at=planned_at,
            )
        )

    for row in conn.execute(_KAYIP_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, temas_calls, phone, randevu_at,
            contact_name,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                randevu_at=randevu_at,
            )
        )

    for row in conn.execute(_TAAHHUT_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, temas_calls, phone, due_at,
            commitment_text, contact_name,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=int(commitment_id) if commitment_id is not None else None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                due_at=due_at,
                commitment_text=str(commitment_text) if commitment_text else None,
            )
        )

    return out


def _load_karar(
    conn: psycopg.Connection, org_id: str
) -> list[NudgeCandidate]:
    """Düşünmek İstiyor + 48 saatten eski; en eski önce."""
    out: list[NudgeCandidate] = []
    rows = conn.execute(
        _KARAR_SQL, (org_id, _KARAR_STATUS, _KARAR_HOURS)
    ).fetchall()
    for row in rows:
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, temas_calls, phone, contact_name,
            status_changed_at, last_temas_at,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                status_changed_at=status_changed_at,
                last_temas_at=last_temas_at,
            )
        )
    return out


def _allocate_slots(counts: dict[str, int], max_slots: int) -> dict[str, int]:
    """pencere en az 1 (uygunsa); kalan diğer sinyallere orantılı.

    Tek sinyalin 3 yeri birden alması engellenir (birden fazla tip varken
    tip başı en fazla 2).
    """
    slots = {t: 0 for t in _TYPE_ORDER}
    present = {t: counts.get(t, 0) for t in _TYPE_ORDER if counts.get(t, 0) > 0}
    if not present:
        return slots

    remaining = min(max_slots, sum(present.values()))
    types_with = list(present.keys())

    # 1) pencere_aciliyor: uygunsa 1 yer garanti.
    if present.get(_PENCERE_TYPE, 0) > 0 and remaining > 0:
        slots[_PENCERE_TYPE] = 1
        remaining -= 1

    # 2) Kalan yerler diğer sinyaller arasında (pencere hariç).
    others = [
        t for t in _TYPE_ORDER
        if t != _PENCERE_TYPE and present.get(t, 0) > 0
    ]
    if remaining > 0 and others:
        other_counts = {t: present[t] for t in others}
        per_cap = 2 if len(types_with) > 1 else remaining
        total_o = sum(other_counts.values())
        ideals = [
            (t, other_counts[t], remaining * other_counts[t] / total_o)
            for t in others
        ]
        for t, avail, ideal in ideals:
            take = min(avail, per_cap, int(ideal))
            slots[t] = take
        used = sum(slots[t] for t in others)
        leftover = remaining - used
        remainders = sorted(
            (
                (ideal - int(ideal), -_TYPE_ORDER.index(t), t)
                for t, avail, ideal in ideals
            ),
            reverse=True,
        )
        for _, _, t in remainders:
            if leftover <= 0:
                break
            cap = min(other_counts[t], per_cap)
            if slots[t] < cap:
                slots[t] += 1
                leftover -= 1
        remaining = leftover

    # 3) Yalnız pencere kaldıysa doldur; tek tipte en fazla 2.
    if remaining > 0 and present.get(_PENCERE_TYPE, 0) > slots[_PENCERE_TYPE]:
        if not others:
            cap = min(2, present[_PENCERE_TYPE])
            add = min(remaining, cap - slots[_PENCERE_TYPE])
            slots[_PENCERE_TYPE] += max(0, add)

    return slots


def _select_for_rep(
    items: list[NudgeCandidate],
) -> tuple[list[NudgeCandidate], dict[str, int], dict[str, int], int]:
    by_type: dict[str, list[NudgeCandidate]] = defaultdict(list)
    for n in items:
        by_type[n.nudge_type].append(n)
    for nudge_type in _TYPE_ORDER:
        # planlanmis: isimli + en eski önce; diğerleri en yüksek sort_key önce
        if nudge_type == "planlanmis_arama":
            by_type[nudge_type].sort(
                key=lambda x: (
                    0 if (x.contact_name or "").strip() else 1,
                    x.sort_key,
                )
            )
        elif nudge_type == "pencere_aciliyor":
            by_type[nudge_type].sort(
                key=lambda x: (0 if x.is_dokunulmamis else 1, -x.sort_key)
            )
        else:
            by_type[nudge_type].sort(key=lambda x: x.sort_key, reverse=True)

    suitable = {t: len(by_type[t]) for t in _TYPE_ORDER}
    slots = _allocate_slots(suitable, _MAX_PER_REP)

    selected: list[NudgeCandidate] = []
    shown = {t: 0 for t in _TYPE_ORDER}
    for nudge_type in _TYPE_ORDER:
        take = slots[nudge_type]
        for n in by_type[nudge_type][:take]:
            selected.append(n)
            shown[nudge_type] += 1

    stock = max(0, len(items) - len(selected))
    return selected, suitable, shown, stock


def _post_cliq(webhook_url: str, text: str, userids: str) -> None:
    body = json.dumps(
        {"type": "message", "text": text, "userids": userids},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _insert_nudge(
    conn: psycopg.Connection,
    *,
    org_id: str,
    n: NudgeCandidate,
    payload: dict[str, Any],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO nudges (
            org_id, rep_id, nudge_type, thread_id, lead_id,
            commitment_id, sent_at, channel, payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, now(), 'cliq', %s)
        """,
        (
            org_id,
            n.rep_id,
            n.nudge_type,
            n.thread_id,
            n.lead_id,
            n.commitment_id,
            Json(payload),
        ),
    )
    return int(cur.rowcount)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Günlük dürtü üretir; Cliq'e gönderir."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Cliq'e gönder ve nudges'a yaz (varsayılan: dry-run)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    load_dotenv()
    shadow_email = _shadow_email()
    _print_delivery_mode(shadow_email)
    database_url = os.environ.get("DATABASE_URL_POOLED")
    if not database_url:
        print("DATABASE_URL_POOLED ortam değişkeni tanımlı değil")
        print("üretilen=0, gönderilen=0, hata=1")
        return 1

    webhook_url = os.environ.get("CLIQ_WEBHOOK_URL")
    if not dry_run and not webhook_url:
        print("eksik ortam değişkeni: CLIQ_WEBHOOK_URL")
        print("üretilen=0, gönderilen=0, hata=1")
        return 1

    org_id = get_org_id()
    produced = 0
    sent = 0
    errors = 0

    try:
        with psycopg.connect(database_url, prepare_threshold=None) as conn:
            ensure_status_changed_columns(conn)
            conn.commit()
            print_call_stale_warning(conn, org_id)
            recipients = _load_recipients(conn, org_id)
            recipient_ids = set(recipients.keys())
            extras = _parse_extra_recipients()
            print(
                f"PUSULA_EXTRA_RECIPIENTS: {len(extras)} id"
                + (f" ({', '.join(extras)})" if extras else " (bos)")
            )
            listed = ", ".join(
                f"{recipients[rid][0]} ({recipients[rid][2]})"
                for rid in sorted(
                    recipients.keys(),
                    key=lambda x: (recipients[x][2], recipients[x][0]),
                )
            )
            print(f"alıcı={len(recipients)}: {listed}")

            # Dün 10 sn+ (eşik mi / veri eksik mi ayrımı).
            yesterday = datetime.now(_TZ).date() - timedelta(days=1)
            talks_sales = _count_talks(
                conn, org_id, yesterday, category="sales"
            )
            talks_all = _count_talks(
                conn, org_id, yesterday, category=None
            )
            print(
                f"dün ({yesterday}) 10sn+ çağrı: "
                f"sales={talks_sales}, tüm={talks_all}"
            )

            _print_taahhut_funnel(conn, org_id)

            raw_all = _load_candidates(conn, org_id)
            raw = [n for n in raw_all if n.rep_id in recipient_ids]
            named = sum(1 for n in raw if (n.contact_name or "").strip())
            if raw and named == 0:
                print(
                    "uyarı: leads/contacts.full_name boş — "
                    "satırlar sadece telefon; backfill_leads çalıştırın"
                )

            karar_all = _load_karar(conn, org_id)
            karar_raw = [n for n in karar_all if n.rep_id in recipient_ids]
            print(f"karar_bekleyen aday={len(karar_raw)}")

            suitable_raw: dict[str, dict[str, int]] = defaultdict(
                lambda: {t: 0 for t in _TYPE_ORDER}
            )
            for n in raw:
                suitable_raw[n.rep_id][n.nudge_type] += 1

            karar_pool_raw: dict[str, int] = defaultdict(int)
            for n in karar_raw:
                karar_pool_raw[n.rep_id] += 1

            planlanmis_total = sum(
                1 for n in raw if n.nudge_type == "planlanmis_arama"
            )
            if planlanmis_total == 0:
                overdue_n = conn.execute(
                    """
                    SELECT count(*) FROM events e
                    WHERE e.org_id = %s
                      AND e.channel = 'call'
                      AND e.meta->>'call_status' = 'overdue'
                    """,
                    (org_id,),
                ).fetchone()
                print(
                    "planlanmis_arama uygun=0 — "
                    f"overdue ham={(overdue_n[0] if overdue_n else 0)}; "
                    "hepsi sonrası connected ile kapanmış veya "
                    "rep/thread eşleşmesi yok olabilir"
                )

            filtered: list[NudgeCandidate] = []
            skipped_dup = 0
            for n in raw:
                if _is_dup(conn, org_id, n):
                    skipped_dup += 1
                    continue
                filtered.append(n)

            karar_filtered: list[NudgeCandidate] = []
            skipped_karar_dup = 0
            for n in karar_raw:
                if _is_dup(conn, org_id, n):
                    skipped_karar_dup += 1
                    continue
                karar_filtered.append(n)

            by_rep: dict[str, list[NudgeCandidate]] = defaultdict(list)
            for n in filtered:
                by_rep[n.rep_id].append(n)
            karar_by_rep: dict[str, list[NudgeCandidate]] = defaultdict(list)
            for n in karar_filtered:
                karar_by_rep[n.rep_id].append(n)

            plans: list[
                tuple[
                    str,
                    list[NudgeCandidate],
                    list[NudgeCandidate],
                    dict[str, int],
                    dict[str, int],
                    int,
                    str,
                ]
            ] = []
            for rep_id in sorted(
                recipient_ids,
                key=lambda x: (recipients[x][2], recipients[x][0]),
            ):
                suitable_pool = suitable_raw.get(
                    rep_id, {t: 0 for t in _TYPE_ORDER}
                )
                items = by_rep.get(rep_id) or []
                selected, suitable_elig, shown, _rest = _select_for_rep(items)
                selected = [n for n in selected if _reason_for(n) is not None]
                karar_items = karar_by_rep.get(rep_id) or []
                karar_shown: list[NudgeCandidate] = []
                for n in karar_items:
                    if _reason_for(n) is None:
                        continue
                    karar_shown.append(n)
                    if len(karar_shown) >= _KARAR_MAX:
                        break
                karar_pool = karar_pool_raw.get(rep_id, 0)
                name, email, category = recipients[rep_id]
                dunden = _load_dunden(conn, org_id, rep_id)
                ilk_this, ilk_prev = _load_ilk_satin(conn, org_id, rep_id)
                msg = _build_message(
                    rep_name=name,
                    rep_email=email,
                    selected=selected,
                    karar=karar_shown,
                    karar_pool=karar_pool,
                    dunden=dunden,
                    ilk_this=ilk_this,
                    ilk_prev=ilk_prev,
                )
                plans.append(
                    (
                        rep_id,
                        selected,
                        karar_shown,
                        suitable_elig,
                        shown,
                        karar_pool,
                        msg,
                    )
                )
                produced += len(selected) + len(karar_shown)

            print(
                f"aday: {len(raw)} (dedup atlanan={skipped_dup}), "
                f"karar={len(karar_raw)} (dedup atlanan={skipped_karar_dup}), "
                f"isimli={named}, planlanmis={planlanmis_total}, "
                f"mesaj={len(plans)}, dürtü={produced} (org={org_id})"
            )
            for (
                rep_id, selected, karar_shown, suitable, shown, pool, _msg
            ) in plans:
                name, _email, category = recipients[rep_id]
                detail = ", ".join(
                    f"{t} uygun={suitable[t]}/gösterilen={shown[t]}"
                    for t in _TYPE_ORDER
                    if suitable[t] or shown[t]
                )
                print(
                    f"  {name} [{category}]: bugun={len(selected)} "
                    f"({detail}), karar={len(karar_shown)}/{pool}"
                )

            if dry_run:
                print("dry-run: gönderilmedi. Yazmak için --apply kullan.")
                if plans:
                    sample = plans[0][6]
                    for p in plans:
                        if p[1] or p[2]:
                            sample = p[6]
                            break
                    print("--- örnek mesaj ---")
                    print(sample)
            else:
                assert webhook_url
                if shadow_email is None:
                    print(
                        f"gonderim oncesi: mesaj={len(plans)} "
                        "(gercek temsilciler)"
                    )
                    for (
                        rep_id, _sel, _kar, _su, _sh, _pool, _msg
                    ) in plans:
                        name, email, category = recipients[rep_id]
                        dest = email or "(email yok)"
                        print(f"  {name} [{category}] -> {dest}")
                for (
                    rep_id, selected, karar_shown, suitable, shown, pool, msg
                ) in plans:
                    name, email, category = recipients[rep_id]
                    dest = shadow_email or email
                    if not dest:
                        errors += 1
                        print(f"hata (cliq {name}): email yok")
                        continue
                    try:
                        _post_cliq(webhook_url, msg, dest)
                    except (
                        urllib.error.URLError,
                        urllib.error.HTTPError,
                        TimeoutError,
                    ) as exc:
                        errors += 1
                        print(f"hata (cliq {name}): {exc}")
                        continue

                    to_store = selected + karar_shown
                    if not to_store:
                        continue
                    for n in to_store:
                        try:
                            payload: dict[str, Any] = {
                                "shadow": shadow_email is not None,
                                "intended_rep_id": rep_id,
                                "intended_rep_name": name,
                                "intended_rep_email": email,
                                "intended_category": category,
                                "nudge_type": n.nudge_type,
                                "phone": n.phone,
                                "contact_name": n.contact_name,
                                "karar_pool": pool,
                                "suitable": suitable,
                                "shown": shown,
                            }
                            if shadow_email is not None:
                                payload["shadow_email"] = shadow_email
                            sent += _insert_nudge(
                                conn, org_id=org_id, n=n, payload=payload
                            )
                        except psycopg.Error as exc:
                            errors += 1
                            print(
                                f"hata (nudges {name}/{n.nudge_type}): {exc}"
                            )
    except psycopg.Error as exc:
        print(f"bağlantı/sorgu başarısız: {exc}")
        errors += 1
        print(f"üretilen={produced}, gönderilen={sent}, hata={errors}")
        return 1

    print(f"üretilen={produced}, gönderilen={sent}, hata={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
