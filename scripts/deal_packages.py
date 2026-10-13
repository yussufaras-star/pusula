"""Deal paket/aşama/tutar — aylık prim hedefi için (ilk çalışma tüm deal'ler).

Kullanım:
    python scripts/deal_packages.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.deal_packages import sync_deal_packages
from pusula.metrics import TZ


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    print(json.dumps(sync_deal_packages(datetime.now(TZ)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
