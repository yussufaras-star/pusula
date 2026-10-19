"""Zoho Leads modülüne gelen lead'in günlük sayımı (lead_inflow_daily).

Kullanım:
    python scripts/lead_inflow.py               # boşsa yıl başından, değilse son 3 gün
    python scripts/lead_inflow.py --since 2026-01-01
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.lead_inflow import now_istanbul, sync


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(sync(now_istanbul().date(), since=args.since), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
