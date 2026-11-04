"""Haftalık bilanço — biten haftanın bilançosunu yoksa açar.

Kullanım:
    python scripts/weekly_balance.py                  # pazarı geçen son hafta
    python scripts/weekly_balance.py --week 2026-10-26

Her sabah gun-saati'nde çalışır; o hafta bilançosu olan şirketi atlar, yani
pazartesi kaçırılırsa ertesi gün açılır. Aktif hesabı olmayan şirket atlanır.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.finance.weekly import create_closed_week
from pusula.metrics import TZ
from pusula.panel_data import connect


def main() -> int:
    parser = argparse.ArgumentParser(description="Haftalık bilanço.")
    parser.add_argument("--week", type=date.fromisoformat, default=None, help="Haftanın pazartesisi (YYYY-AA-GG)")
    args = parser.parse_args()
    load_dotenv()
    with connect() as conn:
        report = create_closed_week(conn, datetime.now(TZ).date(), args.week)
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
