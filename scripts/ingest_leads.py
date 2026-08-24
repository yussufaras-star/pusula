"""Zoho Leads Created_Time delta → leads tablosu.

Kullanım:
    python scripts/ingest_leads.py --since 2026-08-19
    python scripts/ingest_leads.py --lookback-days 14
    python scripts/ingest_leads.py --since 2026-08-19 --dry-run

.env otomatik yüklenir; DATABASE_URL ve Zoho kimlik bilgileri gerekir.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.base import to_istanbul
from pusula.ingest.lead_identity import sync_leads


def main() -> int:
    parser = argparse.ArgumentParser(description="Zoho Leads Created_Time sync.")
    parser.add_argument("--since", help="ISO 8601 alt sınır (Europe/Istanbul)")
    parser.add_argument(
        "--lookback-days",
        type=int,
        help="since yoksa bugünden geriye gün (cron için)",
    )
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma")
    args = parser.parse_args()
    load_dotenv()

    if args.since:
        try:
            since = to_istanbul(datetime.fromisoformat(args.since))
        except ValueError:
            print(f"--since çözümlenemedi: {args.since!r}")
            return 1
    elif args.lookback_days is not None:
        if args.lookback_days < 1:
            print("--lookback-days en az 1 olmalı")
            return 1
        since = datetime.now(ZoneInfo("Europe/Istanbul")) - timedelta(
            days=args.lookback_days
        )
        since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        print("--since veya --lookback-days gerekli")
        return 1

    print(f"=== Leads (Created_Time >= {since.isoformat()}) ===")
    stats = sync_leads(since=since, dry_run=args.dry_run)
    print(
        f"fetched={stats['fetched']} written={stats['written']} "
        f"inserted={stats['inserted']} updated={stats['updated']} "
        f"hata={stats['errors']}"
    )
    if args.dry_run:
        print("dry-run: yazılmadı.")
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
