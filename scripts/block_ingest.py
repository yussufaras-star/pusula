"""Blok sonu calls ingest — lookback 6 saat, yalnız çağrı.

Kullanım:
    python scripts/block_ingest.py
    python scripts/block_ingest.py --dry-run

Günlük ingest sırasına dokunmaz. Watermark, çekilen pencerenin
son kaydına çekilir (kayıt yoksa olduğu gibi kalır).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.blocks import ISTANBUL, LOOKBACK_HOURS, ended_block
from pusula.db import client
from pusula.ingest import get
from pusula.panel_data import today_arama_count

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Blok sonu calls ingest.")
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()

    now = datetime.now(ISTANBUL)
    block = ended_block(now)
    since = now - timedelta(hours=LOOKBACK_HOURS)
    print(f"blok: {block.label}")
    print(f"since: {since.isoformat(timespec='seconds')} (lookback {LOOKBACK_HOURS} saat)")

    ingester_cls = get("zoho_crm_calls")
    ingester = ingester_cls()
    prior = None if args.dry_run else client.get_sync_state("zoho_crm_calls")
    result = ingester.run(since=since, dry_run=args.dry_run)
    if prior is not None:
        # Günlük imleç yerinde kalsın; 6 saatlik pencere onu geri sarmasın.
        client.set_sync_state(prior)
    daily = today_arama_count()
    print(f"cekilen: {result.fetched}")
    print(f"yazilan: {result.inserted}")
    print(f"duplicated: {result.duplicated}")
    print(f"skipped: {result.skipped}")
    print(f"failed: {result.failed}")
    print(f"gunluk toplam: {daily}")
    if args.dry_run:
        print("dry-run: DB'ye hiçbir şey yazılmadı")
    if result.failed > 0:
        print("hata: failed > 0, run başarısız")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
