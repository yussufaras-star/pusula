"""Event'i ve kimliği olmayan orphan thread satırlarını temizler.

Kullanım:
    python scripts/cleanup_orphan_threads.py
    python scripts/cleanup_orphan_threads.py --apply

Varsayılan --dry-run: sadece sayıyı basar, silmez.
--apply ile siler. .env otomatik yüklenir; DATABASE_URL gerekir.
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

# Hiç event'i yok VE hiç identity'si yok.
_ORPHAN_SQL = """
    SELECT t.thread_id
    FROM threads t
    WHERE t.org_id = %s
      AND NOT EXISTS (
          SELECT 1 FROM events e
          WHERE e.org_id = t.org_id AND e.thread_id = t.thread_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM identities i
          WHERE i.org_id = t.org_id AND i.thread_id = t.thread_id
      )
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Event'siz ve kimliksiz orphan thread'leri temizler."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Gerçekten sil (varsayılan: dry-run)",
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
            rows = conn.execute(_ORPHAN_SQL, (org_id,)).fetchall()
            thread_ids = [row[0] for row in rows]
            count = len(thread_ids)
            print(f"orphan thread: {count} satır (org={org_id})")
            if count == 0:
                return 0
            if dry_run:
                print("dry-run: silinmedi. Silmek için --apply kullan.")
                return 0
            deleted = conn.execute(
                "DELETE FROM threads WHERE org_id = %s AND thread_id = ANY(%s)",
                (org_id, thread_ids),
            ).rowcount
            print(f"silindi: {deleted}")
    except psycopg.Error as exc:
        print(f"temizlik başarısız: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
