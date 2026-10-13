"""Müşteri profilleri — Re-Co yenileme analizi (ilk çalışma tüm contact'lar).

Kullanım:
    python scripts/contact_profiles.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.contact_profiles import sync_contact_profiles
from pusula.metrics import TZ


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    print(json.dumps(sync_contact_profiles(datetime.now(TZ)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
