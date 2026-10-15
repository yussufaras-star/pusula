"""Zoho People onaylı tam gün izinleri → rep_leaves.

Kullanım:
    python scripts/people_leaves.py              # 60 gün geri, 60 gün ileri
    python scripts/people_leaves.py --back 300   # geriye dönük doldurma
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.people_leaves import sync_people_leaves
from pusula.metrics import TZ


def main() -> int:
    parser = argparse.ArgumentParser(description="Zoho People izin senkronu.")
    parser.add_argument("--back", type=int, default=60, help="Geriye bakış (gün)")
    parser.add_argument("--ahead", type=int, default=60, help="İleriye bakış (gün)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    report = sync_people_leaves(datetime.now(TZ).date(), back_days=args.back, ahead_days=args.ahead)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
