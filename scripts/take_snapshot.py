"""Günlük temsilci anlık görüntüsü (rep_snapshots) yazar.

Aynı snapshot_date için idempotent upsert (PK: snapshot_date, rep_id).

Metrikler (Europe/Istanbul takvim haftası):
  bekleyen_lead  — pusula_state = 'active' lead sayısı
  kayip_randevu  — kayıp randevu aday sayısı (send_nudges ile aynı kural)
  acik_taahhut   — status = open commitment (lead owner üzerinden)
  tutulan_hafta  — bu hafta fulfilled (fulfill event occurred_at bu haftada)
  bozulan_hafta  — bu hafta broken (due_at bu haftada)

Alicilar: category='sales' + PUSULA_EXTRA_RECIPIENTS (rep_id CSV).

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
from pusula.temas import is_temas_sql, outcome_join

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
""" + outcome_join("e3") + """
          WHERE e3.org_id = r.org_id
            AND e3.thread_id = r.thread_id
            AND e3.occurred_at > r.randevu_at
            AND coalesce(e3.meta->>'scheduled', 'false') <> 'true'
            AND (
                (e3.channel = 'call' AND e3.direction = 'inbound')
                OR e3.channel = 'meeting'
                OR (
                    e3.channel = 'call'
                    AND """ + is_temas_sql("e3") + """
                )
            )
      )
    GROUP BY 1
"""


def _parse_extra_recipients() -> list[str]:
    raw = os.environ.get("PUSULA_EXTRA_RECIPIENTS") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


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


def _load_snapshot_reps(
    conn: psycopg.Connection, org_id: str
) -> list[tuple[str, str]]:
    """(rep_id, full_name) — sales + PUSULA_EXTRA_RECIPIENTS."""
    sales = conn.execute(
        """
        SELECT rep_id, full_name FROM reps
        WHERE org_id = %s AND active = true AND category = 'sales'
        ORDER BY full_name
        """,
        (org_id,),
    ).fetchall()
    out: dict[str, str] = {str(r[0]): str(r[1]) for r in sales}
    extras = _parse_extra_recipients()
    if extras:
        rows = conn.execute(
            """
            SELECT rep_id, full_name FROM reps
            WHERE org_id = %s AND rep_id = ANY(%s)
            """,
            (org_id, extras),
        ).fetchall()
        found = {str(r[0]): str(r[1]) for r in rows}
        for rid in extras:
            if rid in found:
                out[rid] = found[rid]
            else:
                print(f"uyarı: PUSULA_EXTRA_RECIPIENTS bilinmeyen rep_id={rid}")
    elif not out:
        print("uyarı: sales yok ve PUSULA_EXTRA_RECIPIENTS boş")
    return sorted(out.items(), key=lambda x: x[1])


def take_snapshot(
    conn: psycopg.Connection,
    org_id: str,
    snapshot_day: date,
) -> int:
    _verify_columns(conn)
    week_start, week_end = _week_bounds(snapshot_day)
    print(
        f"hafta={week_start.date()} .. {week_end.date()} "
        f"(snapshot_date={snapshot_day})"
    )

    reps = _load_snapshot_reps(conn, org_id)
    if not reps:
        print("yazilacak temsilci yok")
        return 0
    rep_ids = [r[0] for r in reps]
    names = {r[0]: r[1] for r in reps}

    # Haftalık olay teşhisi (tutulan/bozulan 0 ise sebep net olsun).
    n_fulfill = conn.execute(
        """
        SELECT count(*)::int
        FROM commitments c
        JOIN events e ON e.id = c.fulfilled_event_id
        WHERE c.org_id = %s
          AND c.status = 'fulfilled'
          AND e.occurred_at >= %s
          AND e.occurred_at < %s
        """,
        (org_id, week_start, week_end),
    ).fetchone()
    n_broken_due = conn.execute(
        """
        SELECT count(*)::int
        FROM commitments c
        WHERE c.org_id = %s
          AND c.status = 'broken'
          AND c.due_at >= %s
          AND c.due_at < %s
        """,
        (org_id, week_start, week_end),
    ).fetchone()
    print(
        f"hafta olay: fulfilled_event={int(n_fulfill[0] or 0)}, "
        f"broken_due_at={int(n_broken_due[0] or 0)}"
    )
    if int(n_fulfill[0] or 0) == 0:
        print(
            "not: tutulan_hafta=0 olacak — bu hafta fulfill event "
            "(occurred_at) yok"
        )
    if int(n_broken_due[0] or 0) == 0:
        print(
            "not: bozulan_hafta=0 olacak — bu hafta due_at'li broken yok"
        )

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

    # tutulan: fulfill call'ın occurred_at'i bu takvim haftasında
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

    # bozulan: due_at bu takvim haftasında + status=broken
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

    rows = []
    print("metrikler:")
    for rep_id in rep_ids:
        row = (
            snapshot_day,
            rep_id,
            bekleyen.get(rep_id, 0),
            kayip.get(rep_id, 0),
            acik.get(rep_id, 0),
            tutulan.get(rep_id, 0),
            bozulan.get(rep_id, 0),
        )
        rows.append(row)
        print(
            f"  {names[rep_id]}: bekleyen={row[2]} kayip={row[3]} "
            f"acik={row[4]} tutulan={row[5]} bozulan={row[6]}"
        )

    print(
        "toplam: "
        f"bekleyen={sum(r[2] for r in rows)} "
        f"kayip={sum(r[3] for r in rows)} "
        f"acik={sum(r[4] for r in rows)} "
        f"tutulan={sum(r[5] for r in rows)} "
        f"bozulan={sum(r[6] for r in rows)}"
    )

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

    extras = _parse_extra_recipients()
    print(
        f"PUSULA_EXTRA_RECIPIENTS: "
        f"{len(extras)} id" + (f" ({', '.join(extras)})" if extras else " (bos)")
    )

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
