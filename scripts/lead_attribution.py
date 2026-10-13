"""Lead reklam kaynağı alanları (UTM) — ilk çalıştırma tüm lead'ler, sonra değişenler.

Kullanım:
    python scripts/lead_attribution.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.lead_attribution import sync_lead_attribution
from pusula.metrics import TZ


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    print(json.dumps(sync_lead_attribution(datetime.now(TZ)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
