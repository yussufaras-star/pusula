"""Günlük temsilci anlık görüntüsü (rep_snapshots) yazar.

Aynı snapshot_date için idempotent upsert (PK: snapshot_date, rep_id).

Metrikler (Europe/Istanbul takvim haftası):
  bekleyen_lead  — pusula_state = 'active' lead sayısı
  kayip_randevu  — kayıp randevu aday sayısı (send_nudges ile aynı kural)
  acik_taahhut   — status = open commitment (lead owner üzerinden)
  tutulan_hafta  — bu hafta fulfilled (fulfilled event occurred_at)
  bozulan_hafta  — bu hafta broken (due_at bu haftada)

Kullanım:
    python scripts/take_snapshot.py
    python scripts/take_snapshot.py --date 2026-08-03

DATABASE_URL_POOLED (yoksa DATABASE_URL) gerekir.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from pusula.config import get_org_id

_TZ = ZoneInfo("Europe/Istanbul")

_EXPECTED_COLS = (
    "snapshot_date",
    "rep_id",
    "bekleyen_lead",
    "kayip_randevu",
    "acik_taahhut",
    "tutulan_hafta",
    "bozulan_hafta",
)

_KAYIP_COUNT_SQL = """
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
    )
    SELECT coalesce(r.rep_id, t.owner_rep_id) AS rep_id, count(*)::int
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
          LEFT JOIN call_outcomes co
            ON co.org_id = e3.org_id
           AND (
                (
                    nullif(e3.meta->>'outcome_key', '') IS NOT NULL
                    AND co.outcome_key = e3.meta->>'outcome_key'
                )
                OR (
                    nullif(e3.meta->>'outcome_key', '') IS NULL
                    AND nullif(e3.meta->>'call_result', '') IS NOT NULL
                    AND co.raw_value = e3.meta->>'call_result'
                )
           )
          WHERE e3.org_id = r.org_id
            AND e3.thread_id = r.thread_id
            AND e3.occurred_at > r.randevu_at
            AND coalesce(e3.meta->>'scheduled', 'false') <> 'true'
            AND (
                (e3.channel = 'call' AND e3.direction = 'inbound')
                OR e3.channel = 'meeting'
                OR (
                    e3.channel = 'call'
                    AND (e3.meta->>'call_duration_sec')::numeric >= 30
                    AND coalesce(co.category, '') <> 'not_reached'
                )
            )
      )
    GROUP BY 1
"""


def _verify_columns(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'rep_snapshots'
        ORDER BY ordinal_position
        """
    ).fetchall()
    cols = [str(r[0]) for r in rows]
    missing = [c for c in _EXPECTED_COLS if c not in cols]
    if missing:
        raise RuntimeError(
            "rep_snapshots kolonları eksik: " + ", ".join(missing)
            + f" (mevcut: {cols})"
        )
    return cols


def _week_bounds(day: date) -> tuple[datetime, datetime]:
    monday = day - timedelta(days=day.weekday())
    start = datetime(monday.year, monday.month, monday.day, tzinfo=_TZ)
    end = start + timedelta(days=7)
    return start, end


def take_snapshot(
    conn: psycopg.Connection,
    org_id: str,
    snapshot_day: date,
) -> int:
    _verify_columns(conn)
    week_start, week_end = _week_bounds(snapshot_day)

    reps = [
        str(r[0])
        for r in conn.execute(
            """
            SELECT rep_id FROM reps
            WHERE org_id = %s AND active = true AND category = 'sales'
            ORDER BY rep_id
            """,
            (org_id,),
        ).fetchall()
    ]
    if not reps:
        # sales yoksa tüm aktifler
        reps = [
            str(r[0])
            for r in conn.execute(
                """
                SELECT rep_id FROM reps
                WHERE org_id = %s AND active = true
                ORDER BY rep_id
                """,
                (org_id,),
            ).fetchall()
        ]

    bekleyen = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            """
            SELECT owner_rep_id, count(*)::int
            FROM leads
            WHERE org_id = %s
              AND pusula_state = 'active'
              AND owner_rep_id IS NOT NULL
            GROUP BY 1
            """,
            (org_id,),
        ).fetchall()
    }

    kayip = {
        str(r[0]): int(r[1])
        for r in conn.execute(_KAYIP_COUNT_SQL, (org_id,)).fetchall()
    }

    acik = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            """
            SELECT l.owner_rep_id, count(*)::int
            FROM commitments c
            JOIN LATERAL (
                SELECT owner_rep_id
                FROM leads
                WHERE org_id = c.org_id AND thread_id = c.thread_id
                ORDER BY assigned_at DESC NULLS LAST
                LIMIT 1
            ) l ON true
            WHERE c.org_id = %s
              AND c.status = 'open'
              AND l.owner_rep_id IS NOT NULL
            GROUP BY 1
            """,
            (org_id,),
        ).fetchall()
    }

    tutulan = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            """
            SELECT l.owner_rep_id, count(*)::int
            FROM commitments c
            JOIN events e ON e.id = c.fulfilled_event_id
            JOIN LATERAL (
                SELECT owner_rep_id
                FROM leads
                WHERE org_id = c.org_id AND thread_id = c.thread_id
                ORDER BY assigned_at DESC NULLS LAST
                LIMIT 1
            ) l ON true
            WHERE c.org_id = %s
              AND c.status = 'fulfilled'
              AND e.occurred_at >= %s
              AND e.occurred_at < %s
              AND l.owner_rep_id IS NOT NULL
            GROUP BY 1
            """,
            (org_id, week_start, week_end),
        ).fetchall()
    }

    bozulan = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            """
            SELECT l.owner_rep_id, count(*)::int
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
              AND c.due_at >= %s
              AND c.due_at < %s
              AND l.owner_rep_id IS NOT NULL
            GROUP BY 1
            """,
            (org_id, week_start, week_end),
        ).fetchall()
    }

    rows = [
        (
            snapshot_day,
            rep_id,
            bekleyen.get(rep_id, 0),
            kayip.get(rep_id, 0),
            acik.get(rep_id, 0),
            tutulan.get(rep_id, 0),
            bozulan.get(rep_id, 0),
        )
        for rep_id in reps
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rep_snapshots (
                snapshot_date, rep_id,
                bekleyen_lead, kayip_randevu, acik_taahhut,
                tutulan_hafta, bozulan_hafta
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (snapshot_date, rep_id) DO UPDATE SET
                bekleyen_lead = EXCLUDED.bekleyen_lead,
                kayip_randevu = EXCLUDED.kayip_randevu,
                acik_taahhut = EXCLUDED.acik_taahhut,
                tutulan_hafta = EXCLUDED.tutulan_hafta,
                bozulan_hafta = EXCLUDED.bozulan_hafta
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="rep_snapshots için günlük satır yazar (upsert)."
    )
    parser.add_argument(
        "--date",
        help="Snapshot günü YYYY-MM-DD (varsayılan: bugün, Europe/Istanbul)",
    )
    args = parser.parse_args()

    load_dotenv()
    database_url = (
        os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
    )
    if not database_url:
        print("DATABASE_URL_POOLED / DATABASE_URL yok")
        return 1

    if args.date:
        snapshot_day = date.fromisoformat(args.date)
    else:
        snapshot_day = datetime.now(_TZ).date()

    org_id = get_org_id()
    try:
        with psycopg.connect(database_url, prepare_threshold=None) as conn:
            cols = _verify_columns(conn)
            print(f"rep_snapshots kolonları: {', '.join(cols)}")
            n = take_snapshot(conn, org_id, snapshot_day)
            dates = conn.execute(
                """
                SELECT snapshot_date, count(*)
                FROM rep_snapshots
                GROUP BY 1
                ORDER BY 1
                """
            ).fetchall()
    except (psycopg.Error, RuntimeError) as exc:
        print(f"hata: {exc}")
        return 1

    print(f"yazilan={n} snapshot_date={snapshot_day} org={org_id}")
    for d, cnt in dates:
        print(f"  {d}: {cnt} satır")
    return 0


if __name__ == "__main__":
    sys.exit(main())
