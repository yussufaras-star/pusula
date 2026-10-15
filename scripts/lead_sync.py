"""Zoho Leads ↔ leads tablosu: sahip, statü ve silinen/dönüştürülen lead'ler.

Kullanım:
    python scripts/lead_sync.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest.lead_sync import sync_leads


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    print(json.dumps(sync_leads(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
