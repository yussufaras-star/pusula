"""Randevu dürtmeleri — 1 saat önce arama, 20 dk önce hatırlatma, oda açılmadı uyarısı.

Kullanım:
    python scripts/meeting_nudges.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.meeting_nudges import run_meeting_nudges


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()

    report = run_meeting_nudges()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if report.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
