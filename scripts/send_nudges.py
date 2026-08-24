"""Günlük dürtü üretir ve Cliq'e gönderir.

Koçluk formatı: kayıt disiplini, BUGUN (en fazla 8), KARAR BEKLEYEN
(günde 1), DUN paragrafı.

Zaman-hassas (tetikleyici son 14 gün, tazelik sırası): kayip_randevu,
planlanmis_arama, gecikmis_taahhut, ikinci_arama.
14 günden eski aynı tipler backlog: günde en fazla 2, en eski önce.
Havuz: karar_bekleyen 1/gün, sunumsuz_temas 2/gün.

Temas: pusula.temas.temas_mi — süre eşiği yok.
Çevirme: call_status=connected. overdue/scheduled faaliyet dışı.

PUSULA_SHADOW_EMAIL dolu: tüm Cliq mesajları o adrese gider.
Boş veya tanımsız: gerçek temsilci emaillerine gider (hata değil).

Kullanım:
    python scripts/send_nudges.py
    python scripts/send_nudges.py --as-of 2026-08-24
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
    WON_STAGE,
    classified_deals_cte,
    won_stage_sql,
)
from pusula.temas import (
    TEMAS_MIGRATION_SITES,
    duration_sec,
    is_attempt_sql,
    is_cevirme_sql,
    is_not_planned_sql,
    is_temas_sql,
    outcome_join,
)

_TZ = ZoneInfo("Europe/Istanbul")
_MAX_PER_REP = 3  # _allocate_slots geriye dönük; BUGUN kotası aşağıda
_MAX_LINE = 60
_BUGUN_CAP = 8
_FRESH_DAYS = 14
_BACKLOG_QUOTA = 2
_SUNUMSUZ_QUOTA = 2
_KARAR_MAX = 1
_KARAR_DAYS = 7
_KARAR_WINDOW_DAYS = 30
_KARAR_STATUS = "Düşünmek İstiyor"
_PENCERE_TYPE = "pencere_aciliyor"
_IKINCI_TYPE = "ikinci_arama"
_SUNUMSUZ_TYPE = "sunumsuz_temas"
_KARAR_TYPE = "karar_bekleyen"
_EXCLUDED_STATUSES = (
    "Aranmamalı",
    "İletişim Kurulamadı/Numara Hatalı",
    "Nitelikli Değil-İlgisiz",
)
_EXCL_IN = (
    "("
    + ", ".join("'" + s.replace("'", "''") + "'" for s in _EXCLUDED_STATUSES)
    + ")"
)
_STATUS_OK_L = f"coalesce(l.status, '') NOT IN {_EXCL_IN}"
_KARAR_CHOICES = ("randevu", "kaybetti", "tekrar_ara")
_MEVCUT_MUSTERI = "Mevcut Müşteri"
_IKINCI_ARAMA_DAYS = 60
_IKINCI_ARAMA_MIN_TWO = 10
_TIME_SENSITIVE = (
    "ikinci_arama",
    "planlanmis_arama",
    "gecikmis_taahhut",
    "kayip_randevu",
)
_TYPE_ORDER = (
    "ikinci_arama",
    "planlanmis_arama",
    "gecikmis_taahhut",
    "kayip_randevu",
    "sunumsuz_temas",
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
_CEVIRME_E = is_cevirme_sql("e")
_NOT_PLANNED_E = is_not_planned_sql("e")
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
            SELECT count(*)::int
            FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND e.occurred_at <= now()
              AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
              AND """ + _CEVIRME_E + """
        ) AS cevirme_calls,
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
      AND """ + _STATUS_OK_L + """
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
      AND coalesce((
            SELECT lx.status FROM leads lx
            WHERE lx.org_id = o.org_id AND lx.thread_id = o.thread_id
            ORDER BY lx.assigned_at DESC NULLS LAST
            LIMIT 1
          ), '') NOT IN """ + _EXCL_IN + """
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
      AND coalesce((
            SELECT lx.status FROM leads lx
            WHERE lx.org_id = r.org_id AND lx.thread_id = r.thread_id
            ORDER BY lx.assigned_at DESC NULLS LAST
            LIMIT 1
          ), '') NOT IN """ + _EXCL_IN + """
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
        SELECT lead_id, owner_rep_id, full_name, status
        FROM leads
        WHERE org_id = c.org_id AND thread_id = c.thread_id
        ORDER BY assigned_at DESC NULLS LAST
        LIMIT 1
    ) l ON true
    WHERE c.org_id = %s
      AND c.status = 'broken'
      AND c.due_at IS NOT NULL
      AND c.due_at < now()
      AND c.thread_id IS NOT NULL
      AND l.owner_rep_id IS NOT NULL
      AND coalesce(l.status, '') NOT IN """ + _EXCL_IN + """
    ORDER BY c.due_at ASC
"""

# Son 7 gün: 1 çevirme, temas yok, ikinci deneme yok.
_IKINCI_ARAMA_SQL = """
    WITH week AS (
        SELECT
            e.org_id,
            e.thread_id,
            count(*) FILTER (WHERE """ + _CEVIRME_E + """)::int AS cevirme,
            count(*) FILTER (WHERE """ + _TEMAS_E + """)::int AS temas,
            count(*) FILTER (
                WHERE e.channel = 'call'
                  AND e.direction = 'outbound'
                  AND """ + _ATTEMPT_E + """
            )::int AS attempts,
            min(e.occurred_at) FILTER (
                WHERE """ + _CEVIRME_E + """
            ) AS first_cevirme
        FROM events e
""" + outcome_join("e") + """
        WHERE e.org_id = %s
          AND e.thread_id IS NOT NULL
          AND e.occurred_at <= now()
          AND e.occurred_at >= now() - interval '7 days'
        GROUP BY e.org_id, e.thread_id
        HAVING count(*) FILTER (WHERE """ + _CEVIRME_E + """) = 1
           AND count(*) FILTER (WHERE """ + _TEMAS_E + """) = 0
           AND count(*) FILTER (
                WHERE e.channel = 'call'
                  AND e.direction = 'outbound'
                  AND """ + _ATTEMPT_E + """
           ) = 1
    )
    SELECT
        l.owner_rep_id AS rep_id,
        w.thread_id,
        l.lead_id,
        NULL::bigint AS commitment_id,
        'ikinci_arama'::text AS nudge_type,
        extract(epoch FROM w.first_cevirme) AS sort_key,
        1::int AS outbound_calls,
        0::int AS temas_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = w.org_id
              AND i.thread_id = w.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        l.full_name AS contact_name
    FROM week w
    JOIN LATERAL (
        SELECT lead_id, owner_rep_id, full_name, status
        FROM leads
        WHERE org_id = w.org_id AND thread_id = w.thread_id
        ORDER BY assigned_at DESC NULLS LAST
        LIMIT 1
    ) l ON true
    WHERE l.owner_rep_id IS NOT NULL
      AND coalesce(l.status, '') NOT IN """ + _EXCL_IN + """
    ORDER BY w.first_cevirme ASC NULLS LAST
"""

# Son 14 gün temas var, hiçbir çağrıda demo_done yok. En eski önce.
_SUNUMSUZ_SQL = """
    WITH touched AS (
        SELECT
            e.org_id,
            e.thread_id,
            min(e.occurred_at) AS first_temas
        FROM events e
""" + outcome_join("e") + """
        WHERE e.org_id = %s
          AND e.thread_id IS NOT NULL
          AND e.occurred_at <= now()
          AND e.occurred_at >= now() - interval '14 days'
          AND """ + _TEMAS_E + """
        GROUP BY e.org_id, e.thread_id
    )
    SELECT
        l.owner_rep_id AS rep_id,
        t.thread_id,
        l.lead_id,
        NULL::bigint AS commitment_id,
        'sunumsuz_temas'::text AS nudge_type,
        extract(epoch FROM t.first_temas) AS sort_key,
        NULL::int AS outbound_calls,
        NULL::int AS temas_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = t.org_id
              AND i.thread_id = t.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        l.full_name AS contact_name
    FROM touched t
    JOIN LATERAL (
        SELECT lead_id, owner_rep_id, full_name, status
        FROM leads
        WHERE org_id = t.org_id AND thread_id = t.thread_id
        ORDER BY assigned_at DESC NULLS LAST
        LIMIT 1
    ) l ON true
    WHERE l.owner_rep_id IS NOT NULL
      AND coalesce(l.status, '') NOT IN """ + _EXCL_IN + """
      AND NOT EXISTS (
            SELECT 1 FROM events e2
            WHERE e2.org_id = t.org_id
              AND e2.thread_id = t.thread_id
              AND (
                    e2.meta->>'outcome_key' = 'demo_done'
                    OR e2.meta->>'call_result' = 'Sunum Yapıldı'
              )
      )
    ORDER BY t.first_temas ASC NULLS LAST
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
      AND """ + _STATUS_OK_L + """
      AND l.status_changed_at IS NOT NULL
      AND l.status_changed_at < now() - interval '7 days'
      AND l.status_changed_at >= now() - interval '30 days'
      AND NOT EXISTS (
            SELECT 1 FROM nudges n
            WHERE n.org_id = l.org_id
              AND n.lead_id = l.lead_id
              AND n.nudge_type = 'karar_bekleyen'
              AND n.payload->>'karar' IS NOT NULL
      )
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

# Dün çevirme (connected); süre yalnız çevirme kaydı.
_DUNDEN_SQL = """
    SELECT
        count(*)::int AS cevirme,
        count(*) FILTER (WHERE """ + _TEMAS_E + """)::int AS temas,
        count(*) FILTER (
            WHERE """ + _TEMAS_E + """
              AND coalesce(nullif(trim(e.meta->>'call_result'), ''), '') = ''
        )::int AS empty_result,
        coalesce(sum(""" + _DURATION_SEC + """), 0)::float AS total_sec,
        coalesce(avg(""" + _DURATION_SEC + """), 0)::float AS avg_sec,
        count(*) FILTER (
            WHERE (e.occurred_at AT TIME ZONE 'Europe/Istanbul')::time
                  >= time '15:00'
        )::int AS afternoon_cevirme,
        count(*) FILTER (
            WHERE (e.occurred_at AT TIME ZONE 'Europe/Istanbul')::time
                  >= time '15:00'
              AND """ + _TEMAS_E + """
        )::int AS afternoon_temas,
        count(*) FILTER (
            WHERE (e.occurred_at AT TIME ZONE 'Europe/Istanbul')::time
                  < time '15:00'
        )::int AS morning_cevirme,
        count(*) FILTER (
            WHERE (e.occurred_at AT TIME ZONE 'Europe/Istanbul')::time
                  < time '15:00'
              AND """ + _TEMAS_E + """
        )::int AS morning_temas
    FROM events e
    WHERE e.org_id = %s
      AND e.rep_id = %s
      AND e.channel = 'call'
      AND e.direction = 'outbound'
      AND """ + _CEVIRME_E + """
      AND e.occurred_at <= now()
      AND e.occurred_at >= %s
      AND e.occurred_at < %s
"""

_DISCIPLINE_SQL = """
    SELECT
        l.full_name AS contact_name,
        i.id_value AS phone
    FROM events e
    LEFT JOIN LATERAL (
        SELECT full_name, lead_id
        FROM leads
        WHERE org_id = e.org_id AND thread_id = e.thread_id
          AND """ + _STATUS_OK_L.replace("l.status", "status") + """
        ORDER BY assigned_at DESC NULLS LAST
        LIMIT 1
    ) l ON true
    LEFT JOIN LATERAL (
        SELECT id_value FROM identities
        WHERE org_id = e.org_id AND thread_id = e.thread_id
          AND id_type = 'phone'
        ORDER BY id_value
        LIMIT 1
    ) i ON true
    WHERE e.org_id = %s
      AND e.rep_id = %s
      AND e.channel = 'call'
      AND e.direction = 'outbound'
      AND """ + _TEMAS_E + """
      AND coalesce(nullif(trim(e.meta->>'call_result'), ''), '') = ''
      AND e.occurred_at <= now()
      AND e.occurred_at >= %s
      AND e.occurred_at < %s
    ORDER BY e.occurred_at DESC
    LIMIT 10
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
    cevirme_calls: int | None = None
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
class IkinciAramaStats:
    """Son 60 gün: ikinci arama vs tek arama, satışa dönüş."""

    two_plus: int
    two_plus_won: int
    one_call: int
    one_call_won: int


@dataclass(frozen=True)
class DundenStats:
    cevirme: int
    temas: int
    empty_result: int
    total_sec: float | None = None
    avg_sec: float | None = None
    week4_avg_sec: float | None = None
    afternoon_line: str | None = None


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


def _fmt_sure(seconds: float | None) -> str:
    """DUN şablonu: dakika; '{sure}'ti ekiyle okunur."""
    if seconds is None:
        return "?"
    mins = max(0, int(round(float(seconds) / 60.0)))
    return f"{mins} dk"


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


def _looks_like_phone(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    digits = "".join(c for c in raw if c.isdigit())
    letters = sum(1 for c in raw if c.isalpha())
    return letters == 0 and len(digits) >= 10


def _title_name(name: str | None) -> str:
    """Türkçe başlık: mehmet tavali -> Mehmet Tavali."""
    raw = (name or "").strip()
    if not raw or _looks_like_phone(raw):
        return "İsimsiz"
    words: list[str] = []
    for part in raw.split():
        folded = part.replace("I", "ı").replace("İ", "i").lower()
        if not folded:
            continue
        first = "İ" if folded[0] == "i" else folded[0].upper()
        words.append(first + folded[1:])
    return " ".join(words) if words else "İsimsiz"


def _fmt_phone(raw: str | None) -> str:
    """+905353241988 -> 0535 324 1988."""
    if not raw or not str(raw).strip():
        return "telefon yok"
    digits = "".join(c for c in str(raw) if c.isdigit())
    if digits.startswith("90") and len(digits) >= 12:
        digits = "0" + digits[2:12]
    elif len(digits) == 10 and digits.startswith("5"):
        digits = "0" + digits
    elif len(digits) > 11 and digits.startswith("0"):
        digits = digits[:11]
    if len(digits) == 11 and digits.startswith("0"):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    return str(raw).strip()


def _person_line(n: NudgeCandidate) -> str:
    name = _title_name(n.contact_name)
    return f"{name} · {_fmt_phone(n.phone)}"


def _identity_label(n: NudgeCandidate) -> str:
    return _person_line(n)


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


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_TZ)
    return value.astimezone(_TZ)


def _trigger_at(n: NudgeCandidate) -> datetime | None:
    """Tetikleyici olay zamanı (SQL değişmez; mevcut alan / sort_key)."""
    for val in (n.randevu_at, n.planned_at, n.due_at):
        if val is not None:
            return _as_local(val)
    if n.sort_key:
        return datetime.fromtimestamp(
            float(n.sort_key), tz=ZoneInfo("UTC")
        ).astimezone(_TZ)
    return None


def _is_fresh(
    n: NudgeCandidate, *, now: datetime | None = None
) -> bool:
    trigger = _trigger_at(n)
    if trigger is None:
        return False
    ref = now or datetime.now(_TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_TZ)
    else:
        ref = ref.astimezone(_TZ)
    return trigger >= ref - timedelta(days=_FRESH_DAYS)


def _reason_for(n: NudgeCandidate, *, now: datetime | None = None) -> str | None:
    """Madde gerekçesi: olgu. İstatistik BUGUN altında ayrı."""
    if n.nudge_type == "kayip_randevu":
        days = _days_since(n.randevu_at, now=now)
        if days is None:
            return None
        return f"Randevudan {days} gün geçti, dönüş yapılmadı."
    if n.nudge_type == "ikinci_arama":
        return "1 kez arandı, açılmadı."
    if n.nudge_type == "sunumsuz_temas":
        return "Konuşuldu, sunum yapılmadı."
    if n.nudge_type == "planlanmis_arama":
        gun = _fmt_gun(n.planned_at)
        if gun is None:
            return None
        return f"{gun} için planladığın arama yapılmadı."
    if n.nudge_type == "gecikmis_taahhut":
        days = _days_since(n.due_at, now=now)
        if days is None:
            return None
        return f"{days} gün önce söz verdiğin dönüş yapılmadı."
    if n.nudge_type == "karar_bekleyen":
        days = _days_since(n.status_changed_at, now=now)
        if days is None:
            return None
        return (
            f"{days} gündür 'düşünüyor'. Bir arayıp netleştir, sonra işaretle:"
        )
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
    """Belirli günde çevirme (connected, planlanmış hariç)."""
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
              AND """
            + _CEVIRME_E
            + """
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
              AND """
            + _CEVIRME_E
            + """
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
    """Dün çevirme + süre; öğleden sonra temas oranı koşullu."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    yesterday = local.date() - timedelta(days=1)
    y0, y1 = _day_bounds(yesterday)
    row = conn.execute(_DUNDEN_SQL, (org_id, rep_id, y0, y1)).fetchone()
    cevirme = int(row[0] or 0) if row else 0
    temas = int(row[1] or 0) if row else 0
    empty = int(row[2] or 0) if row else 0
    total_sec = float(row[3] or 0) if row else 0.0
    avg_sec = float(row[4] or 0) if row else 0.0
    aft_c = int(row[5] or 0) if row else 0
    aft_t = int(row[6] or 0) if row else 0
    mor_c = int(row[7] or 0) if row else 0
    mor_t = int(row[8] or 0) if row else 0

    w0 = y1 - timedelta(days=28)
    week4 = conn.execute(_DUNDEN_SQL, (org_id, rep_id, w0, y1)).fetchone()
    week4_n = int(week4[0] or 0) if week4 else 0
    week4_avg: float | None = None
    if week4_n > 0:
        week4_avg = float(week4[4] or 0)

    afternoon_line: str | None = None
    if cevirme > 0 and (aft_c / cevirme) > 0.40 and mor_c > 0:
        aft_rate = aft_t / aft_c if aft_c else 0.0
        mor_rate = mor_t / mor_c if mor_c else 0.0
        if aft_rate < mor_rate:
            afternoon_line = _clip(
                f"15:00 sonrası temas {aft_t}/{aft_c}, "
                f"öğleden önce {mor_t}/{mor_c}"
            )

    return DundenStats(
        cevirme=cevirme,
        temas=temas,
        empty_result=empty,
        total_sec=total_sec if cevirme > 0 else None,
        avg_sec=avg_sec if cevirme > 0 else None,
        week4_avg_sec=week4_avg,
        afternoon_line=afternoon_line,
    )


def _load_discipline(
    conn: psycopg.Connection,
    org_id: str,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> tuple[int, list[tuple[str, str | None]]]:
    """Dün temas var, call_result boş. (toplam, en fazla 3 isim)."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    yesterday = local.date() - timedelta(days=1)
    y0, y1 = _day_bounds(yesterday)
    total_row = conn.execute(
        """
        SELECT count(*)::int
        FROM events e
        WHERE e.org_id = %s
          AND e.rep_id = %s
          AND e.channel = 'call'
          AND e.direction = 'outbound'
          AND """
        + _TEMAS_E
        + """
          AND coalesce(nullif(trim(e.meta->>'call_result'), ''), '') = ''
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s
          AND e.occurred_at < %s
        """,
        (org_id, rep_id, y0, y1),
    ).fetchone()
    total = int(total_row[0] or 0) if total_row else 0
    if total <= 0:
        return 0, []
    rows = conn.execute(_DISCIPLINE_SQL, (org_id, rep_id, y0, y1)).fetchall()
    names: list[tuple[str, str | None]] = []
    for r in rows:
        raw_name = str(r[0]).strip() if r[0] else ""
        if not raw_name or _looks_like_phone(raw_name):
            continue
        names.append((raw_name, str(r[1]) if r[1] else None))
        if len(names) >= 3:
            break
    return total, names


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


def _show_ilk_satin(now: datetime | None = None) -> bool:
    """Pazartesi ve salı satır yok."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    return local.weekday() not in (0, 1)


def _is_monday(now: datetime | None = None) -> bool:
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    return local.weekday() == 0


def _load_ikinci_arama_stats(
    conn: psycopg.Connection,
    org_id: str,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> IkinciAramaStats:
    """assigned_at son 60 gün; connected çağrı; dönüşüm deal created_at."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    start = local - timedelta(days=_IKINCI_ARAMA_DAYS)
    row = conn.execute(
        """
        WITH cohort AS (
            SELECT l.lead_id, l.thread_id, l.assigned_at, l.org_id
            FROM leads l
            WHERE l.org_id = %s
              AND l.owner_rep_id = %s
              AND l.assigned_at >= %s
              AND l.assigned_at <= %s
              AND coalesce(l.status, '') <> %s
        ),
        conv AS (
            SELECT d.lead_id, min(d.created_at) AS conv_at
            FROM deals d
            WHERE d.org_id = %s
              AND d.stage = %s
              AND d.lead_id IS NOT NULL
            GROUP BY d.lead_id
        ),
        bounded AS (
            SELECT
                c.lead_id,
                c.org_id,
                c.thread_id,
                CASE
                    WHEN v.conv_at IS NOT NULL AND v.conv_at > c.assigned_at
                    THEN v.conv_at
                END AS conv_at,
                coalesce(
                    CASE
                        WHEN v.conv_at IS NOT NULL AND v.conv_at > c.assigned_at
                        THEN v.conv_at
                    END,
                    %s
                ) AS until_at
            FROM cohort c
            LEFT JOIN conv v ON v.lead_id = c.lead_id
        ),
        scored AS (
            SELECT
                b.lead_id,
                (b.conv_at IS NOT NULL) AS converted,
                (
                    SELECT count(*)::int
                    FROM events e
                    WHERE e.org_id = b.org_id
                      AND e.thread_id = b.thread_id
                      AND e.channel = 'call'
                      AND e.direction = 'outbound'
                      AND e.meta->>'call_status' = 'connected'
                      AND e.occurred_at <= b.until_at
                ) AS n_call
            FROM bounded b
        )
        SELECT
          count(*) FILTER (WHERE n_call >= 2)::int,
          count(*) FILTER (WHERE n_call >= 2 AND converted)::int,
          count(*) FILTER (WHERE n_call = 1)::int,
          count(*) FILTER (WHERE n_call = 1 AND converted)::int
        FROM scored
        """,
        (
            org_id,
            rep_id,
            start,
            local,
            _MEVCUT_MUSTERI,
            org_id,
            WON_STAGE,
            local,
        ),
    ).fetchone()
    return IkinciAramaStats(
        two_plus=int(row[0] or 0) if row else 0,
        two_plus_won=int(row[1] or 0) if row else 0,
        one_call=int(row[2] or 0) if row else 0,
        one_call_won=int(row[3] or 0) if row else 0,
    )


def _ikinci_arama_lines(stats: IkinciAramaStats) -> list[str] | None:
    """Pazartesi koçluk satırları; eşik tutmazsa None."""
    if stats.two_plus < _IKINCI_ARAMA_MIN_TWO:
        return None
    if stats.two_plus_won == 0 and stats.one_call_won == 0:
        return None
    return [
        (
            f"Son iki ayda {stats.two_plus} lead'i ikinci kez aradın, "
            f"{stats.two_plus_won}'i satışa döndü."
        ),
        (
            f"Tek aramada bırakılan {stats.one_call} lead'den "
            f"{stats.one_call_won}'i satışa döndü."
        ),
    ]


def _build_message(
    *,
    rep_name: str,
    rep_email: str | None,
    selected: list[NudgeCandidate],
    karar: list[NudgeCandidate],
    dunden: DundenStats | None = None,
    discipline_total: int = 0,
    discipline_names: list[tuple[str, str | None]] | None = None,
    ikinci_lines: list[str] | None = None,
) -> str:
    who = rep_name
    if rep_email:
        who = f"{rep_name} <{rep_email}>"
    first = _first_name(_title_name(rep_name))
    parts: list[str] = [
        f"[gölge] {who} için",
        "",
        f"Günaydın {first}",
        "",
    ]
    if discipline_total > 0:
        shown_names = [
            _title_name(name) for name, _phone in (discipline_names or [])
        ]
        listed = ", ".join(shown_names)
        parts.append(
            f"Dün {discipline_total} görüşmenin sonucu boş kaldı: {listed}."
        )
        parts.append("Önce bunları doldur.")
        parts.append("")

    if selected:
        parts.append("BUGUN")
        for idx, n in enumerate(selected, start=1):
            parts.append(f"{idx}. {_person_line(n)}")
            reason = _reason_for(n)
            if reason:
                parts.append(f"   {reason}")
        if ikinci_lines:
            parts.append("")
            parts.extend(ikinci_lines)
        parts.append("")
    elif ikinci_lines:
        parts.extend(ikinci_lines)
        parts.append("")

    if karar:
        n = karar[0]
        parts.append("KARAR BEKLEYEN")
        parts.append(_person_line(n))
        reason = _reason_for(n)
        if reason:
            parts.append(reason)
        parts.append("buton: randevu / kaybetti / tekrar ara")
        parts.append("")

    cevirme = dunden.cevirme if dunden else 0
    temas = dunden.temas if dunden else 0
    sure = (
        _fmt_duration(dunden.avg_sec)
        if dunden is not None and dunden.avg_sec is not None
        else "?"
    )
    dun_line = (
        f"{cevirme} çevirme, {temas} kişi açtı. Ortalama görüşme {sure}"
    )
    avg = dunden.avg_sec if dunden is not None else None
    week4 = dunden.week4_avg_sec if dunden is not None else None
    if avg is not None and week4 is not None:
        if int(round(float(avg))) != int(round(float(week4))):
            sure4 = _fmt_duration(week4)
            dun_line += f" — son 4 haftanda {sure4}'ydi."
        else:
            dun_line += "."
    else:
        dun_line += "."
    parts.append("DUN")
    parts.append(dun_line)
    return "\n".join(parts).rstrip() + "\n"


def _karar_buttons(karar: list[NudgeCandidate]) -> list[dict[str, Any]]:
    """En eski kayda 3 Cliq butonu; tıklanınca pusula_karar fonksiyonu."""
    if not karar:
        return []
    n = karar[0]
    lead_id = n.lead_id or ""
    labels = (
        ("Randevu", "randevu"),
        ("Kaybetti", "kaybetti"),
        ("Tekrar ara", "tekrar_ara"),
    )
    buttons: list[dict[str, Any]] = []
    for label, choice in labels:
        buttons.append(
            {
                "label": label,
                "hint": (n.contact_name or lead_id)[:40],
                "type": "+",
                "key": f"{lead_id}|{n.thread_id}|{choice}",
                "action": {
                    "type": "invoke.function",
                    "data": {"name": "pusula_karar"},
                },
            }
        )
    return buttons


def _is_dup(conn: psycopg.Connection, org_id: str, n: NudgeCandidate) -> bool:
    if n.nudge_type == _KARAR_TYPE:
        return False
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
              AND due_at IS NOT NULL
              AND due_at < now()
              AND thread_id IS NOT NULL
          ) AS with_thread,
          count(*) FILTER (
            WHERE status = 'broken'
              AND due_at IS NOT NULL
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
    if int(totals[2] or 0) > int(totals[3] or 0):
        print(
            "  not: 14g dışı broken backlog kotasına girer "
            f"(günde en fazla {_BACKLOG_QUOTA}, en eski önce)"
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
        older = int(past or 0) - int(in14 or 0)
        print(
            f"  {name}: gecmis_due={past} taze_14g={in14} "
            f"backlog={older} (gösterilen en fazla {_BACKLOG_QUOTA})"
        )


def _load_candidates(conn: psycopg.Connection, org_id: str) -> list[NudgeCandidate]:
    out: list[NudgeCandidate] = []

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

    for row in conn.execute(_IKINCI_ARAMA_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, temas_calls, phone, contact_name,
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
            )
        )

    for row in conn.execute(_SUNUMSUZ_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, temas_calls, phone, contact_name,
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
            )
        )

    return out


def _load_karar(
    conn: psycopg.Connection, org_id: str
) -> list[NudgeCandidate]:
    """Düşünmek İstiyor + 48 saatten eski; en eski önce."""
    out: list[NudgeCandidate] = []
    rows = conn.execute(
        _KARAR_SQL, (org_id, _KARAR_STATUS)
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
    """Taze (14g) tazelik sırası; gecikmis_taahhut backlog en fazla 2
    (en eski önce, kotası taze ikinci_arama ile ezilmez); BUGUN en fazla 8."""
    by_type: dict[str, list[NudgeCandidate]] = defaultdict(list)
    for n in items:
        by_type[n.nudge_type].append(n)

    suitable = {t: len(by_type[t]) for t in _TYPE_ORDER}
    seen: set[str] = set()

    def _take(
        src: list[NudgeCandidate],
        *,
        newest_first: bool,
    ) -> list[NudgeCandidate]:
        dated: list[tuple[datetime, NudgeCandidate]] = []
        undated: list[NudgeCandidate] = []
        for n in src:
            if n.thread_id in seen:
                continue
            if _reason_for(n) is None:
                continue
            trigger = _trigger_at(n)
            if trigger is None:
                undated.append(n)
            else:
                dated.append((trigger, n))
        dated.sort(key=lambda x: x[0], reverse=newest_first)
        ordered = [n for _t, n in dated] + undated
        out: list[NudgeCandidate] = []
        for n in ordered:
            if n.thread_id in seen:
                continue
            seen.add(n.thread_id)
            out.append(n)
        return out

    fresh_src = [
        n for n in items
        if n.nudge_type in _TIME_SENSITIVE and _is_fresh(n)
    ]
    backlog_src = [
        n for n in items
        if n.nudge_type in _TIME_SENSITIVE and not _is_fresh(n)
    ]
    sunumsuz_src = list(by_type[_SUNUMSUZ_TYPE])

    fresh = _take(fresh_src, newest_first=True)
    backlog = _take(backlog_src, newest_first=False)
    sunumsuz = _take(sunumsuz_src, newest_first=False)

    sunumsuz_keep = sunumsuz[: min(_SUNUMSUZ_QUOTA, _BUGUN_CAP)]
    taahhut_backlog = [
        n for n in backlog if n.nudge_type == "gecikmis_taahhut"
    ]
    other_backlog = [
        n for n in backlog if n.nudge_type != "gecikmis_taahhut"
    ]
    taahhut_keep = taahhut_backlog[: min(_BACKLOG_QUOTA, _BUGUN_CAP)]
    rest_cap = _BUGUN_CAP - len(sunumsuz_keep) - len(taahhut_keep)
    selected: list[NudgeCandidate] = []
    selected.extend(fresh[: max(0, rest_cap)])
    remain = max(0, rest_cap) - len(selected)
    if remain > 0 and other_backlog:
        selected.extend(other_backlog[: min(_BACKLOG_QUOTA, remain)])
    selected.extend(taahhut_keep)
    selected.extend(sunumsuz_keep)

    shown = {t: 0 for t in _TYPE_ORDER}
    for n in selected:
        shown[n.nudge_type] += 1
    return selected, suitable, shown, 0


def _post_cliq(
    webhook_url: str,
    text: str,
    userids: str,
    buttons: list[dict[str, Any]] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "message",
        "text": text,
        "userids": userids,
    }
    if buttons:
        payload["card"] = {
            "theme": "modern-inline",
            "title": "Karar",
            "buttons": buttons,
        }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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


def record_karar(
    conn: psycopg.Connection,
    *,
    org_id: str,
    rep_id: str,
    thread_id: str,
    lead_id: str | None,
    karar: str,
) -> int:
    """Cliq buton kararı — ertesi gün aynı lead karar_bekleyen'de çıkmaz."""
    if karar not in _KARAR_CHOICES:
        raise ValueError(f"bilinmeyen karar: {karar}")
    n = NudgeCandidate(
        rep_id=rep_id,
        thread_id=thread_id,
        lead_id=lead_id,
        commitment_id=None,
        nudge_type=_KARAR_TYPE,
        sort_key=0.0,
        phone=None,
    )
    return _insert_nudge(
        conn,
        org_id=org_id,
        n=n,
        payload={"karar": karar, "source": "cliq_button"},
    )


def _print_temas_sites() -> None:
    print(
        f"temas fonksiyonu: temas_mi / is_temas_sql — "
        f"sure esigi kaldirilan yer={TEMAS_MIGRATION_SITES} "
        "(weekly_report 13, send_nudges 3, update_lead_status 1, "
        "is_temas_sql govde 1)"
    )


def _print_exclusion(
    conn: psycopg.Connection, org_id: str
) -> None:
    print(
        "dislama filtresi: send_nudges SQL'de yoktu; eklendi. "
        f"Statuler: {', '.join(_EXCLUDED_STATUSES)}"
    )
    print(
        "  pencere_aciliyor once pusula_state=active ile kapali "
        "statuleri dolayli eleyebilirdi; kayip/planlanmis/taahhut/"
        "karar_bekleyen'de dogrudan filtre yoktu."
    )
    for status in _EXCLUDED_STATUSES:
        row = conn.execute(
            """
            SELECT count(*)::int FROM leads
            WHERE org_id = %s AND status = %s
            """,
            (org_id, status),
        ).fetchone()
        print(f"  {status}: {int(row[0] or 0) if row else 0} lead")


def _print_call_filter_compare(
    conn: psycopg.Connection, org_id: str
) -> None:
    """30 gun temsilci basi cagri: planned dahil vs haric."""
    print("--- 30g cagri once -> sonra (overdue/scheduled haric) ---")
    rows = conn.execute(
        """
        SELECT r.full_name,
          count(*)::int AS once,
          count(*) FILTER (WHERE """
        + _NOT_PLANNED_E
        + """)::int AS sonra
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND r.category = 'sales' AND r.active = true
          AND e.channel = 'call'
          AND e.direction = 'outbound'
          AND e.occurred_at <= now()
          AND e.occurred_at >= now() - interval '30 days'
        GROUP BY r.full_name
        ORDER BY r.full_name
        """,
        (org_id,),
    ).fetchall()
    for name, before, after in rows:
        print(f"  {name}: {int(before)} -> {int(after)}")
    if not rows:
        print("  (sales cagri yok)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Günlük dürtü üretir; Cliq'e gönderir."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Cliq'e gönder ve nudges'a yaz (varsayılan: dry-run)",
    )
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Mesaj gününü bu tarihe sabitle (Pazartesi koçluk satırı)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    as_of: datetime | None = None
    if args.as_of:
        try:
            as_of_date = date.fromisoformat(args.as_of)
        except ValueError:
            print(f"gecersiz --as-of tarihi: {args.as_of}")
            print("üretilen=0, gönderilen=0, hata=1")
            return 1
        as_of = datetime(
            as_of_date.year, as_of_date.month, as_of_date.day,
            12, 0, tzinfo=_TZ,
        )

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
            _print_temas_sites()
            _print_exclusion(conn, org_id)
            _print_call_filter_compare(conn, org_id)
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
                f"dün ({yesterday}) çevirme: "
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
                if n.nudge_type in suitable_raw[n.rep_id]:
                    suitable_raw[n.rep_id][n.nudge_type] += 1

            print("--- ikinci_arama / sunumsuz_temas aday (temsilci) ---")
            for rid in sorted(
                recipient_ids,
                key=lambda x: (recipients[x][2], recipients[x][0]),
            ):
                name = recipients[rid][0]
                ikinci_n = suitable_raw[rid].get(_IKINCI_TYPE, 0)
                sunumsuz_n = suitable_raw[rid].get(_SUNUMSUZ_TYPE, 0)
                print(
                    f"  {name}: ikinci_arama={ikinci_n} "
                    f"sunumsuz_temas={sunumsuz_n}"
                )

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
            ikinci_by_rep: dict[str, IkinciAramaStats] = {}
            for rep_id in sorted(
                recipient_ids,
                key=lambda x: (recipients[x][2], recipients[x][0]),
            ):
                items = by_rep.get(rep_id) or []
                selected, suitable_elig, shown, _unused = _select_for_rep(
                    items
                )
                karar_items = karar_by_rep.get(rep_id) or []
                karar_shown = karar_items[:_KARAR_MAX]
                name, email, category = recipients[rep_id]
                dunden = _load_dunden(conn, org_id, rep_id)
                disc_total, disc_names = _load_discipline(conn, org_id, rep_id)
                ikinci_lines: list[str] | None = None
                ikinci_stats: IkinciAramaStats | None = None
                if _is_monday(as_of):
                    ikinci_stats = _load_ikinci_arama_stats(
                        conn, org_id, rep_id
                    )
                    ikinci_by_rep[rep_id] = ikinci_stats
                    ikinci_lines = _ikinci_arama_lines(ikinci_stats)
                msg = _build_message(
                    rep_name=name,
                    rep_email=email,
                    selected=selected,
                    karar=karar_shown,
                    dunden=dunden,
                    discipline_total=disc_total,
                    discipline_names=disc_names,
                    ikinci_lines=ikinci_lines,
                )
                plans.append(
                    (
                        rep_id,
                        selected,
                        karar_shown,
                        suitable_elig,
                        shown,
                        0,
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
                rep_id, selected, karar_shown, suitable, shown, _ov, _msg
            ) in plans:
                name, _email, category = recipients[rep_id]
                detail = ", ".join(
                    f"{t} uygun={suitable[t]}/gösterilen={shown[t]}"
                    for t in _TYPE_ORDER
                    if suitable[t] or shown[t]
                )
                old30 = 0
                now_local = datetime.now(_TZ)
                for n in selected:
                    trigger = _trigger_at(n)
                    if trigger is None:
                        continue
                    age = now_local - trigger
                    if age.days >= 30:
                        old30 += 1
                print(
                    f"  {name} [{category}]: bugun={len(selected)} "
                    f"({detail}), karar={len(karar_shown)}, "
                    f"tetik_30g_ustu={old30}"
                )
                st = ikinci_by_rep.get(rep_id)
                if st is not None:
                    print(
                        f"    ikinci_arama_60g="
                        f"{st.two_plus}/{st.two_plus_won}/"
                        f"{st.one_call}/{st.one_call_won}"
                    )
                if old30 > 2:
                    print(
                        f"    uyari: 30 günden eski tetikleyici {old30} > 2"
                    )
                if "ve " in _msg and "tane daha" in _msg:
                    print("    uyari: bakiye satiri mesajda var")

            if dry_run:
                print("dry-run: gönderilmedi. Yazmak için --apply kullan.")
                for p in plans:
                    rid = p[0]
                    name, _e, _c = recipients[rid]
                    print(f"--- mesaj: {name} ---")
                    print(p[6])
            else:
                assert webhook_url
                if shadow_email is None:
                    print(
                        f"gonderim oncesi: mesaj={len(plans)} "
                        "(gercek temsilciler)"
                    )
                    for (
                        rep_id, _sel, _kar, _su, _sh, _ov, _msg
                    ) in plans:
                        name, email, category = recipients[rep_id]
                        dest = email or "(email yok)"
                        print(f"  {name} [{category}] -> {dest}")
                for (
                    rep_id, selected, karar_shown, suitable, shown, overflow, msg
                ) in plans:
                    name, email, category = recipients[rep_id]
                    dest = shadow_email or email
                    if not dest:
                        errors += 1
                        print(f"hata (cliq {name}): email yok")
                        continue
                    try:
                        _post_cliq(
                            webhook_url,
                            msg,
                            dest,
                            buttons=_karar_buttons(karar_shown),
                        )
                    except (
                        urllib.error.URLError,
                        urllib.error.HTTPError,
                        TimeoutError,
                    ) as exc:
                        errors += 1
                        print(f"hata (cliq {name}): {exc}")
                        continue
                    print(f"cliq gitti: {name} -> {dest}")

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
