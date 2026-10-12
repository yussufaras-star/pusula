"""Zoho Meeting gerçek katılım süreleri → meeting_sessions.

Kullanım:
    python scripts/meeting_ingest.py            # son 2 gün (saatlik akış)
    python scripts/meeting_ingest.py --days 90  # geriye dönük doldurma
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.zoho_meeting import sync_meetings
from pusula.metrics import TZ


def main() -> int:
    parser = argparse.ArgumentParser(description="Zoho Meeting katılım senkronu.")
    parser.add_argument("--days", type=float, default=2.0, help="Geriye bakış (gün)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    report = sync_meetings(datetime.now(TZ), lookback=timedelta(days=args.days))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
