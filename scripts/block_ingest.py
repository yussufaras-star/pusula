"""Saatlik tazelik ingest — dört kaynak, 6 saat lookback.

Kullanım:
    python scripts/block_ingest.py
    python scripts/block_ingest.py --source zoho_crm_calls
    python scripts/block_ingest.py --dry-run

Sıra: aramalar → randevular → lead'ler → kişiler.
Aynı saat penceresinde ikinci çalıştırma Zoho'ya gitmez.
Günlük ingest sırasına dokunmaz; watermark geri yazılır.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.blocks import ISTANBUL, LOOKBACK_HOURS, to_istanbul
from pusula.config import get_org_id
from pusula.db import client
from pusula.db.models import SyncState
from pusula.ingest import get
from pusula.ingest.crm_contacts import sync_contacts
from pusula.ingest.lead_identity import sync_leads
from pusula.panel_data import connect, today_arama_count

logger = logging.getLogger(__name__)

_CALLS = "zoho_crm_calls"
_BOOKINGS = "zoho_bookings"
_LEADS = "zoho_leads"
_CONTACTS = "zoho_contacts"

SourceKey = Literal["aramalar", "randevular", "leadler", "kisiler"]
_SOURCE_ORDER: tuple[SourceKey, ...] = (
    "aramalar",
    "randevular",
    "leadler",
    "kisiler",
)
_CLI_TO_KEY: dict[str, SourceKey] = {
    _CALLS: "aramalar",
    _BOOKINGS: "randevular",
    _LEADS: "leadler",
    _CONTACTS: "kisiler",
}


@dataclass
class SourceResult:
    key: SourceKey
    fetched: int
    written: int
    short_circuit: bool
    failed: int = 0


def _hour_window(now: datetime) -> tuple[datetime, datetime]:
    """Saatlik pencere: son :07 (veya şimdi :07) → +1 saat.

    Cron dakika 07'de tetiklenir; elle üst üste çalıştırmada aynı
    pencereye düşer, ikinci koşu kısa devreye girer.
    """
    local = to_istanbul(now)
    start = local.replace(minute=7, second=0, microsecond=0)
    if local < start:
        start = start - timedelta(hours=1)
    return start, start + timedelta(hours=1)


def _marker_source(key: SourceKey, start: datetime) -> str:
    stamp = start.strftime("%Y-%m-%dT%H:%M")
    return f"hourly_slot:{stamp}:{key}"


def _table_fresh(sql: str, params: tuple[object, ...]) -> bool:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return bool(row and row[0])


def _already_done(now: datetime, key: SourceKey) -> bool:
    """Pencerede yazım var veya bu saat başarıyla tarandı."""
    start, end = _hour_window(now)
    org_id = get_org_id()
    if key == "aramalar":
        fresh = _table_fresh(
            """
            SELECT EXISTS (
                SELECT 1 FROM events
                WHERE org_id = %s AND channel = 'call'
                  AND created_at >= %s AND created_at < %s
            )
            """,
            (org_id, start, end),
        )
    elif key == "randevular":
        fresh = _table_fresh(
            """
            SELECT EXISTS (
                SELECT 1 FROM events
                WHERE org_id = %s AND channel = 'meeting'
                  AND created_at >= %s AND created_at < %s
            )
            """,
            (org_id, start, end),
        )
    elif key == "leadler":
        fresh = _table_fresh(
            """
            SELECT EXISTS (
                SELECT 1 FROM leads
                WHERE org_id = %s
                  AND created_at >= %s AND created_at < %s
            )
            """,
            (org_id, start, end),
        )
    else:
        fresh = _table_fresh(
            """
            SELECT EXISTS (
                SELECT 1 FROM contacts
                WHERE org_id = %s
                  AND created_at >= %s AND created_at < %s
            )
            """,
            (org_id, start, end),
        )
    if fresh:
        return True
    marker = client.get_sync_state(_marker_source(key, start))
    if marker is None or marker.last_synced_at is None:
        return False
    stamped = to_istanbul(marker.last_synced_at)
    return start <= stamped < end


def _mark_done(now: datetime, key: SourceKey) -> None:
    start, _end = _hour_window(now)
    client.set_sync_state(
        SyncState(
            source_name=_marker_source(key, start),
            last_synced_at=now,
            last_cursor=key,
        )
    )


def _print_source_detail(
    label: str,
    *,
    fetched: int,
    written: int,
    duplicated: int = 0,
    skipped: int = 0,
    failed: int = 0,
) -> None:
    print(f"kaynak: {label}")
    print(f"rowcount: {written}")
    print(f"cekilen: {fetched}")
    print(f"yazilan: {written}")
    print(f"duplicated: {duplicated}")
    print(f"skipped: {skipped}")
    print(f"failed: {failed}")


def _short_circuit(now: datetime, key: SourceKey, label: str) -> SourceResult | None:
    if not _already_done(now, key):
        return None
    print("zaten guncel")
    _print_source_detail(label, fetched=0, written=0)
    return SourceResult(key=key, fetched=0, written=0, short_circuit=True)


def _run_calls(*, dry_run: bool, now: datetime) -> SourceResult:
    skipped = _short_circuit(now, "aramalar", _CALLS)
    if skipped is not None:
        return skipped
    since = now - timedelta(hours=LOOKBACK_HOURS)
    print(f"since: {since.isoformat(timespec='seconds')} (lookback {LOOKBACK_HOURS} saat)")
    ingester = get(_CALLS)()
    prior = None if dry_run else client.get_sync_state(_CALLS)
    result = ingester.run(since=since, dry_run=dry_run)
    if prior is not None:
        # Günlük imleç yerinde kalsın; 6 saatlik pencere onu geri sarmasın.
        client.set_sync_state(prior)
    _print_source_detail(
        result.source_name,
        fetched=result.fetched,
        written=result.inserted,
        duplicated=result.duplicated,
        skipped=result.skipped,
        failed=result.failed,
    )
    print(f"gunluk toplam: {today_arama_count()}")
    if not dry_run and result.failed == 0:
        _mark_done(now, "aramalar")
    return SourceResult(
        key="aramalar",
        fetched=result.fetched,
        written=result.inserted,
        short_circuit=False,
        failed=result.failed,
    )


def _run_bookings(*, dry_run: bool, now: datetime) -> SourceResult:
    skipped = _short_circuit(now, "randevular", _BOOKINGS)
    if skipped is not None:
        return skipped
    since = now - timedelta(hours=LOOKBACK_HOURS)
    print(f"since: {since.isoformat(timespec='seconds')} (lookback {LOOKBACK_HOURS} saat)")
    ingester = get(_BOOKINGS)()
    setattr(ingester, "lookback", timedelta(hours=LOOKBACK_HOURS))
    prior = None if dry_run else client.get_sync_state(_BOOKINGS)
    result = ingester.run(since=since, dry_run=dry_run)
    if prior is not None:
        client.set_sync_state(prior)
    _print_source_detail(
        result.source_name,
        fetched=result.fetched,
        written=result.inserted,
        duplicated=result.duplicated,
        skipped=result.skipped,
        failed=result.failed,
    )
    if not dry_run and result.failed == 0:
        _mark_done(now, "randevular")
    return SourceResult(
        key="randevular",
        fetched=result.fetched,
        written=result.inserted,
        short_circuit=False,
        failed=result.failed,
    )


def _run_leads(*, dry_run: bool, now: datetime) -> SourceResult:
    skipped = _short_circuit(now, "leadler", _LEADS)
    if skipped is not None:
        return skipped
    since = now - timedelta(hours=LOOKBACK_HOURS)
    print(f"since: {since.isoformat(timespec='seconds')} (lookback {LOOKBACK_HOURS} saat)")
    stats = sync_leads(since=since, dry_run=dry_run)
    _print_source_detail(
        _LEADS,
        fetched=int(stats["fetched"]),
        written=int(stats["written"]),
        failed=int(stats["errors"]),
    )
    if not dry_run and int(stats["errors"]) == 0:
        _mark_done(now, "leadler")
    return SourceResult(
        key="leadler",
        fetched=int(stats["fetched"]),
        written=int(stats["written"]),
        short_circuit=False,
        failed=int(stats["errors"]),
    )


def _run_contacts(*, dry_run: bool, now: datetime) -> SourceResult:
    skipped = _short_circuit(now, "kisiler", _CONTACTS)
    if skipped is not None:
        return skipped
    since = now - timedelta(hours=LOOKBACK_HOURS)
    print(f"since: {since.isoformat(timespec='seconds')} (lookback {LOOKBACK_HOURS} saat)")
    stats = sync_contacts(since=since, dry_run=dry_run)
    _print_source_detail(
        _CONTACTS,
        fetched=int(stats["fetched"]),
        written=int(stats["written"]),
        failed=int(stats["errors"]),
    )
    if not dry_run and int(stats["errors"]) == 0:
        _mark_done(now, "kisiler")
    return SourceResult(
        key="kisiler",
        fetched=int(stats["fetched"]),
        written=int(stats["written"]),
        short_circuit=False,
        failed=int(stats["errors"]),
    )


def _print_summary(results: list[SourceResult]) -> None:
    parts: list[str] = []
    for item in results:
        short = "evet" if item.short_circuit else "hayir"
        parts.append(
            f"{item.key} okunan={item.fetched} yazilan={item.written} "
            f"kisa_devre={short}"
        )
    print("ozet: " + " · ".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser(description="Saatlik dört kaynak ingest.")
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma")
    parser.add_argument(
        "--source",
        choices=(_CALLS, _BOOKINGS, _LEADS, _CONTACTS),
        help="Tek kaynak; verilmezse dördü sırayla",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()

    now = datetime.now(ISTANBUL)
    start, end = _hour_window(now)
    print(
        f"saat penceresi: {start.isoformat(timespec='minutes')} → "
        f"{end.isoformat(timespec='minutes')}"
    )

    runners = {
        "aramalar": _run_calls,
        "randevular": _run_bookings,
        "leadler": _run_leads,
        "kisiler": _run_contacts,
    }
    if args.source is not None:
        keys: tuple[SourceKey, ...] = (_CLI_TO_KEY[args.source],)
    else:
        keys = _SOURCE_ORDER

    results: list[SourceResult] = []
    for key in keys:
        print(f"=== {key} ===")
        results.append(runners[key](dry_run=args.dry_run, now=now))

    if args.dry_run:
        print("dry-run: DB'ye hiçbir şey yazılmadı")
    _print_summary(results)
    if any(item.failed > 0 for item in results):
        print("hata: failed > 0, run başarısız")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
