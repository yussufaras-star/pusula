"""Tempo dürtmesi — hedefin gerisindeki temsilciye Cliq mesajı (onay kuralıyla).

Kullanım:
    python scripts/pace_nudges.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.pace_nudges import run_pace_nudges


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()

    report = run_pace_nudges()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if report.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
