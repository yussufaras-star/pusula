"""Contacts (tümü) + Deals (since) ingest — satış döngüsü.

Kullanım:
    python scripts/ingest_sales_cycle.py
    python scripts/ingest_sales_cycle.py --since 2026-04-01
    python scripts/ingest_sales_cycle.py --contacts-only
    python scripts/ingest_sales_cycle.py --deals-only --since 2026-04-01

Varsayılan: contacts tam sync, deals --since 2026-04-01.
.env otomatik yüklenir; eksik Zoho anahtarları .env.txt ile doldurulabilir.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.base import to_istanbul
from pusula.ingest.crm_contacts import sync_contacts
from pusula.ingest.crm_deals import sync_deals


def _load_env() -> None:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env", override=True)
    # .env'de eksik kalan anahtarları .env.txt'ten tamamla (override etme).
    load_dotenv(root / ".env.txt", override=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zoho Contacts + Deals sync (satış döngüsü)."
    )
    parser.add_argument(
        "--since",
        default="2026-04-01T00:00:00",
        help="Deals Created_Time alt sınırı (ISO 8601, varsayılan 2026-04-01)",
    )
    parser.add_argument(
        "--contacts-only",
        action="store_true",
        help="Sadece Contacts sync",
    )
    parser.add_argument(
        "--deals-only",
        action="store_true",
        help="Sadece Deals sync",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB'ye yazma",
    )
    args = parser.parse_args()
    _load_env()

    try:
        since = to_istanbul(datetime.fromisoformat(args.since))
    except ValueError:
        print(f"--since çözümlenemedi: {args.since!r}")
        return 1

    run_contacts = not args.deals_only
    run_deals = not args.contacts_only

    if run_contacts:
        print("=== Contacts (tümü) ===")
        cstats = sync_contacts(dry_run=args.dry_run)
        print(
            f"fetched={cstats['fetched']} written={cstats['written']} "
            f"threadli={cstats['with_thread']} leadli={cstats['with_lead']} "
            f"hata={cstats['errors']}"
        )

    if run_deals:
        print(f"=== Deals (Created_Time >= {since.isoformat()}) ===")
        dstats = sync_deals(since=since, dry_run=args.dry_run)
        print(
            f"fetched={dstats['fetched']} written={dstats['written']} "
            f"threadli={dstats['with_thread']} dongulu={dstats['with_cycle']} "
            f"zincir_kopuk={dstats['chain_broken']} "
            f"guvenilmez={dstats['unreliable_cycle']} "
            f"threadsiz={dstats['no_thread']} hata={dstats['errors']}"
        )

    if args.dry_run:
        print("dry-run: yazılmadı.")
        return 0

    # Doğrulama sorguları
    from pusula.db import client

    with client.transaction() as conn:
        print("\n=== leads ay (created_at = Pusula insert) ===")
        for r in conn.execute(
            """
            select date_trunc('month', created_at)::date as ay, count(*)
            from leads group by 1 order by 1
            """
        ).fetchall():
            print(r)
        print("=== leads ay (assigned_at = Zoho Created_Time) ===")
        for r in conn.execute(
            """
            select date_trunc('month', assigned_at)::date as ay, count(*)
            from leads group by 1 order by 1
            """
        ).fetchall():
            print(r)
        print("=== deals ===")
        print(
            conn.execute(
                """
                select count(*) as toplam,
                       count(thread_id) as threadli,
                       count(cycle_start_at) as dongulu,
                       count(*) filter (where cycle_start_reliable) as guvenilir
                from deals
                """
            ).fetchone()
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
