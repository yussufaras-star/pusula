"""Contacts (tümü) + Deals (since) ingest — satış döngüsü.

Kullanım:
    python scripts/ingest_sales_cycle.py
    python scripts/ingest_sales_cycle.py --since 2026-04-01
    python scripts/ingest_sales_cycle.py --contacts-only
    python scripts/ingest_sales_cycle.py --deals-only --since 2026-04-01
    python scripts/ingest_sales_cycle.py --deals-only --backfill-from 2025-09-01 --until 2025-12-31
    python scripts/ingest_sales_cycle.py --deals-only --backfill-from 2025-09-01 --until 2025-12-31 --apply

Varsayılan: contacts tam sync, deals --since 2026-04-01
(Created_Time OR Modified_Time).
Backfill: Created_Time kapalı aralık, yazmaz; --apply ile
ON CONFLICT DO NOTHING (mevcut 2026 satırına dokunulmaz).
.env otomatik yüklenir; eksik Zoho anahtarları .env.txt ile doldurulabilir.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.base import to_istanbul
from pusula.ingest.crm_contacts import sync_contacts
from pusula.ingest.crm_deals import backfill_deals, sync_deals


def _parse_day(raw: str, flag: str) -> date | None:
    text = raw.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        print(f"{flag} çözümlenemedi: {raw!r}")
        return None


def _run_backfill(args: argparse.Namespace) -> int:
    """Tek seferlik 2025 penceresi. --apply yoksa yazılmaz."""
    if args.contacts_only:
        print("backfill yalnız deals; --contacts-only kullanma")
        return 1
    if args.apply and args.dry_run:
        print("--apply ve --dry-run birlikte olmaz")
        return 1
    start = _parse_day(args.backfill_from, "--backfill-from")
    if start is None:
        return 1
    until_raw = args.until or "2025-12-31"
    until = _parse_day(until_raw, "--until")
    if until is None:
        return 1
    apply = bool(args.apply)
    mode = "apply" if apply else "dry-run"
    print(
        f"=== Deals backfill {mode} "
        f"Created_Time {start.isoformat()} .. {until.isoformat()} (dahil) ==="
    )
    stats = backfill_deals(start=start, until=until, apply=apply)
    print(
        f"fetched={stats['fetched']} in_window={stats['in_window']} "
        f"skipped_2026={stats['skipped_2026']} "
        f"new={stats['new']} already={stats['already']} "
        f"written={stats['written']} amount_empty={stats['amount_empty']} "
        f"ownerless={stats['ownerless']} hata={stats['errors']}"
    )
    print("ay | stage | adet | yeni | mevcut | sahipsiz | tutar")
    for row in stats.get("monthly") or []:
        tutar = row.get("tutar")
        tutar_s = "None" if tutar is None else f"{tutar:.2f}"
        print(
            f"{row['ay']} | {row['stage']} | {row['adet']} | "
            f"{row['yeni']} | {row['mevcut']} | {row['sahipsiz']} | {tutar_s}"
        )
    names = stats.get("unmatched_names") or []
    print(
        f"eslesmeyen sahip={stats.get('unmatched_count', 0)} "
        f"isimler={names if names else 'yok'}"
    )
    from pusula.db import client

    with client.transaction() as conn:
        by_stage = conn.execute(
            """
            SELECT stage, count(*)::int
            FROM deals
            GROUP BY 1
            ORDER BY 1
            """
        ).fetchall()
        dated = conn.execute(
            """
            SELECT count(*) FILTER (
              WHERE stage = 'Kapandı Kazanıldı'
            )::int,
            count(*) FILTER (
              WHERE stage = 'İptaller-İadeler'
            )::int
            FROM deals
            """
        ).fetchone()
    print("mevcut deals stage sayimi:")
    for stage, n in by_stage:
        print(f"  {stage}={n}")
    if dated:
        print(
            f"  Kapandı Kazanıldı={dated[0]} İptaller-İadeler={dated[1]}"
        )
    if not apply:
        print("dry-run: yazılmadı.")
        return 0 if int(stats.get("errors") or 0) == 0 else 1
    print(f"apply: written={stats['written']}")
    return 0 if int(stats.get("errors") or 0) == 0 else 1


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
        help="Deals Created/Modified_Time alt sınırı (ISO 8601, varsayılan 2026-04-01)",
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
    parser.add_argument(
        "--lookback-days",
        type=int,
        help="Contacts Created_Time veya --deals-only Deals penceresi (cron)",
    )
    parser.add_argument(
        "--backfill-from",
        help="Geriye dönük Created_Time başlangıç günü (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--until",
        help="Geriye dönük Created_Time son günü dahil (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Backfill yaz; yoksa dry-run",
    )
    args = parser.parse_args()
    _load_env()

    if args.backfill_from:
        return _run_backfill(args)

    try:
        since = to_istanbul(datetime.fromisoformat(args.since))
    except ValueError:
        print(f"--since çözümlenemedi: {args.since!r}")
        return 1

    run_contacts = not args.deals_only
    run_deals = not args.contacts_only

    contacts_since: datetime | None = None
    if args.lookback_days is not None:
        if args.lookback_days < 1:
            print("--lookback-days en az 1 olmalı")
            return 1
        contacts_since = datetime.now(ZoneInfo("Europe/Istanbul")) - timedelta(
            days=args.lookback_days
        )
        contacts_since = contacts_since.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif args.contacts_only and any(
        arg == "--since" or arg.startswith("--since=") for arg in sys.argv
    ):
        contacts_since = since

    if run_contacts:
        if contacts_since is None:
            print("=== Contacts (tümü) ===")
        else:
            print(
                f"=== Contacts (Created_Time >= {contacts_since.isoformat()}) ==="
            )
        cstats = sync_contacts(since=contacts_since, dry_run=args.dry_run)
        print(
            f"fetched={cstats['fetched']} written={cstats['written']} "
            f"inserted={cstats.get('inserted', 0)} "
            f"updated={cstats.get('updated', 0)} "
            f"threadli={cstats['with_thread']} leadli={cstats['with_lead']} "
            f"hata={cstats['errors']}"
        )

    if run_deals:
        deals_since = since
        if args.deals_only and args.lookback_days is not None:
            if args.lookback_days < 1:
                print("--lookback-days en az 1 olmalı")
                return 1
            deals_since = datetime.now(ZoneInfo("Europe/Istanbul")) - timedelta(
                days=args.lookback_days
            )
            deals_since = deals_since.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        print(
            f"=== Deals (Created_Time|Modified_Time >= {deals_since.isoformat()}) ==="
        )
        dstats = sync_deals(since=deals_since, dry_run=args.dry_run)
        print(
            f"fetched={dstats['fetched']} written={dstats['written']} "
            f"new={dstats.get('new', 0)} already={dstats.get('already', 0)} "
            f"amount_empty={dstats.get('amount_empty', 0)} "
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
