"""Görüşme Analizleri: kayıtları topla, yazıya dök, analiz et, haftalık özet yaz.

Kullanım:
    python scripts/conversation_analysis.py                 # dünün haftası (gece işi)
    python scripts/conversation_analysis.py --week 2026-10-12
    python scripts/conversation_analysis.py --stage topla --stage yaziya_dok --limit 5

Gece işi dünün haftasını işler: pazartesi gecesi biten haftayı kapatır,
diğer geceler süren haftayı günceller. Yarıda kalan kayıtlar sonraki
çalışmada devam eder.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.conversations import run, week_start_of
from pusula.metrics import TZ

STAGES = ("topla", "yaziya_dok", "analiz", "haftalik")


def main() -> int:
    parser = argparse.ArgumentParser(description="Görüşme Analizleri hattı.")
    parser.add_argument("--week", help="Haftanın herhangi bir günü (YYYY-AA-GG); boşsa dünün haftası")
    parser.add_argument("--stage", action="append", choices=STAGES, help="Yalnız bu adım(lar)")
    parser.add_argument("--limit", type=int, default=None, help="Adım başına en fazla kayıt (deneme için)")
    parser.add_argument("--budget-min", type=float, default=None, help="Süre sınırı (dakika)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    day = date.fromisoformat(args.week) if args.week else datetime.now(TZ).date() - timedelta(days=1)
    report = run(
        week_start_of(day),
        args.stage or STAGES,
        budget_sec=args.budget_min * 60 if args.budget_min else None,
        limit=args.limit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
