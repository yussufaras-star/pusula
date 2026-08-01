"""Açık commitment'ları CRM call_status ve sonraki aramalara göre günceller.

Kurallar (öncelik sırasıyla):
  1. Aynı thread'de due_at'ten sonra gerçekleşmiş, scheduled olmayan
     bir call event varsa → fulfilled (+ fulfilled_event_id)
  2. due_at geçmişte VE kaynak event meta.call_status = 'overdue'
     → broken
  3. Diğerleri open kalır

Kullanım:
    python scripts/update_commitments.py
    python scripts/update_commitments.py --apply

Varsayılan --dry-run. .env otomatik yüklenir; DATABASE_URL gerekir.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from pusula.config import get_org_id

_CANDIDATES_SQL = """
    SELECT
        c.id AS commitment_id,
        c.due_at < now() AS is_past,
        e.meta->>'call_status' AS call_status,
        (
            SELECT e2.id
            FROM events e2
            WHERE e2.org_id = c.org_id
              AND e2.thread_id = c.thread_id
              AND e2.channel = 'call'
              AND coalesce(e2.meta->>'scheduled', 'false') <> 'true'
              AND c.due_at IS NOT NULL
              AND e2.occurred_at > c.due_at
            ORDER BY e2.occurred_at ASC
            LIMIT 1
        ) AS fulfill_event_id
    FROM commitments c
    LEFT JOIN events e ON e.id = c.source_event_id
    WHERE c.org_id = %s
      AND c.status = 'open'
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Açık commitment durumlarını günceller (broken/fulfilled)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Gerçekten yaz (varsayılan: dry-run)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL ortam değişkeni tanımlı değil")
        return 1

    org_id = get_org_id()
    try:
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(_CANDIDATES_SQL, (org_id,)).fetchall()
            to_broken: list[int] = []
            to_fulfilled: list[tuple[int, int]] = []

            for commitment_id, is_past, call_status, fulfill_event_id in rows:
                if fulfill_event_id is not None:
                    to_fulfilled.append((int(commitment_id), int(fulfill_event_id)))
                elif is_past and call_status == "overdue":
                    to_broken.append(int(commitment_id))

            stay_open = len(rows) - len(to_fulfilled) - len(to_broken)
            print(f"açık commitment: {len(rows)} (org={org_id})")
            print(f"  → fulfilled: {len(to_fulfilled)}")
            print(f"  → broken:    {len(to_broken)}")
            print(f"  → open:      {stay_open}")

            if dry_run:
                print("dry-run: yazılmadı. Yazmak için --apply kullan.")
                return 0

            fulfilled_n = 0
            for commitment_id, event_id in to_fulfilled:
                cur = conn.execute(
                    """
                    UPDATE commitments SET
                        status = 'fulfilled',
                        fulfilled_event_id = %s
                    WHERE id = %s AND org_id = %s AND status = 'open'
                    """,
                    (event_id, commitment_id, org_id),
                )
                fulfilled_n += cur.rowcount
            broken_n = 0
            if to_broken:
                broken_n = conn.execute(
                    """
                    UPDATE commitments SET status = 'broken'
                    WHERE org_id = %s AND status = 'open' AND id = ANY(%s)
                    """,
                    (org_id, to_broken),
                ).rowcount

            print(f"yazıldı: fulfilled={fulfilled_n}, broken={broken_n}")
    except psycopg.Error as exc:
        print(f"commitment güncelleme başarısız: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
