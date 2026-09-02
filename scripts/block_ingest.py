"""Blok sonu ingest — calls 6 saat, bookings 24 saat.

Kullanım:
    python scripts/block_ingest.py
    python scripts/block_ingest.py --source zoho_bookings
    python scripts/block_ingest.py --dry-run

Günlük ingest sırasına dokunmaz. Watermark, çekilen pencerenin
son kaydına çekilir; ardından günlük imleç geri yazılır.
Blok aralığında veri varsa Zoho çağrılmaz.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.blocks import (
    BOOKINGS_LOOKBACK_HOURS,
    ISTANBUL,
    LOOKBACK_HOURS,
    PLANNED_BLOCKS,
    DayBlock,
    ended_block,
    to_istanbul,
)
from pusula.config import get_org_id
from pusula.db import client
from pusula.ingest import get
from pusula.panel_data import connect, today_arama_count

logger = logging.getLogger(__name__)

_CALLS = "zoho_crm_calls"
_BOOKINGS = "zoho_bookings"


def _print_counts(
    source_name: str, inserted: int, duplicated: int, skipped: int, failed: int, fetched: int
) -> None:
    print(f"kaynak: {source_name}")
    print(f"rowcount: {inserted}")
    print(f"cekilen: {fetched}")
    print(f"yazilan: {inserted}")
    print(f"duplicated: {duplicated}")
    print(f"skipped: {skipped}")
    print(f"failed: {failed}")


def _slot_bounds(now: datetime, block: DayBlock) -> tuple[datetime, datetime]:
    """Bitmiş bloğun yazım penceresi: tetik → sonraki tetik."""
    local = to_istanbul(now)
    day = local.date()
    start = datetime.combine(
        day, time(block.ingest_hour, block.ingest_minute), tzinfo=ISTANBUL
    )
    if local < start:
        day = day - timedelta(days=1)
        start = datetime.combine(
            day, time(block.ingest_hour, block.ingest_minute), tzinfo=ISTANBUL
        )
    idx = next(i for i, item in enumerate(PLANNED_BLOCKS) if item.key == block.key)
    if idx + 1 < len(PLANNED_BLOCKS):
        nxt = PLANNED_BLOCKS[idx + 1]
        end = datetime.combine(
            day, time(nxt.ingest_hour, nxt.ingest_minute), tzinfo=ISTANBUL
        )
        if end <= start:
            end = end + timedelta(days=1)
    else:
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=ISTANBUL)
    return start, end


def _channel_for_source(source: str) -> str:
    return "meeting" if source == _BOOKINGS else "call"


def _already_written(now: datetime, block: DayBlock, channel: str) -> bool:
    """Bu blok aralığında channel için created_at var mı."""
    start, end = _slot_bounds(now, block)
    org_id = get_org_id()
    sql = """
        SELECT EXISTS (
            SELECT 1
            FROM events
            WHERE org_id = %s
              AND channel = %s
              AND created_at >= %s
              AND created_at < %s
        )
    """
    with connect() as conn:
        row = conn.execute(sql, (org_id, channel, start, end)).fetchone()
    return bool(row and row[0])


def _skip_fresh(source: str, now: datetime, block: DayBlock) -> bool:
    channel = _channel_for_source(source)
    if not _already_written(now, block, channel):
        return False
    print("zaten guncel")
    _print_counts(source, 0, 0, 0, 0, 0)
    return True


def _run_calls(*, dry_run: bool) -> int:
    now = datetime.now(ISTANBUL)
    block = ended_block(now)
    print(f"blok: {block.label}")
    if _skip_fresh(_CALLS, now, block):
        return 0
    since = now - timedelta(hours=LOOKBACK_HOURS)
    print(f"since: {since.isoformat(timespec='seconds')} (lookback {LOOKBACK_HOURS} saat)")
    ingester_cls = get(_CALLS)
    ingester = ingester_cls()
    prior = None if dry_run else client.get_sync_state(_CALLS)
    result = ingester.run(since=since, dry_run=dry_run)
    if prior is not None:
        # Günlük imleç yerinde kalsın; 6 saatlik pencere onu geri sarmasın.
        client.set_sync_state(prior)
    daily = today_arama_count()
    _print_counts(
        result.source_name,
        result.inserted,
        result.duplicated,
        result.skipped,
        result.failed,
        result.fetched,
    )
    print(f"gunluk toplam: {daily}")
    if dry_run:
        print("dry-run: DB'ye hiçbir şey yazılmadı")
    if result.failed > 0:
        print("hata: failed > 0, run başarısız")
        return 1
    return 0


def _run_bookings(*, dry_run: bool) -> int:
    now = datetime.now(ISTANBUL)
    block = ended_block(now)
    print(f"blok: {block.label}")
    if _skip_fresh(_BOOKINGS, now, block):
        return 0
    since = now - timedelta(hours=BOOKINGS_LOOKBACK_HOURS)
    print(
        f"since: {since.isoformat(timespec='seconds')} "
        f"(lookback {BOOKINGS_LOOKBACK_HOURS} saat)"
    )
    ingester_cls = get(_BOOKINGS)
    ingester = ingester_cls()
    setattr(ingester, "lookback", timedelta(hours=BOOKINGS_LOOKBACK_HOURS))
    prior = None if dry_run else client.get_sync_state(_BOOKINGS)
    result = ingester.run(since=since, dry_run=dry_run)
    if prior is not None:
        client.set_sync_state(prior)
    _print_counts(
        result.source_name,
        result.inserted,
        result.duplicated,
        result.skipped,
        result.failed,
        result.fetched,
    )
    if dry_run:
        print("dry-run: DB'ye hiçbir şey yazılmadı")
    if result.failed > 0:
        print("hata: failed > 0, run başarısız")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Blok sonu ingest.")
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma")
    parser.add_argument(
        "--source",
        choices=(_CALLS, _BOOKINGS),
        default=_CALLS,
        help="zoho_crm_calls (varsayılan) veya zoho_bookings",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()

    if args.source == _BOOKINGS:
        return _run_bookings(dry_run=args.dry_run)
    return _run_calls(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
