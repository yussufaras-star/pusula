"""SONAR-41 tutarlılık havuzunu kuyruğa al (pusula/conv_pool.py).

Kullanım:
    python scripts/conv_pool.py --since 2026-07-25 --sample 600 --dry-run
    python scripts/conv_pool.py --since 2026-07-25 --sample 600

Kuyruğa alınan görüşmeler gece işinde (gorusme-analizi.yml) yazıya dökülüp
analiz edilir; komut tekrar çalıştırılabilir, aynı görüşmeleri seçer.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.conv_pool import enqueue
from pusula.metrics import TZ


def main() -> int:
    parser = argparse.ArgumentParser(description="SONAR-41 tutarlılık havuzu.")
    parser.add_argument("--since", required=True, help="İlk gün (YYYY-AA-GG); Verimor kayıtları ~3 ay saklar")
    parser.add_argument("--sample", type=int, default=600, help="Satışsız görüşme örneği")
    parser.add_argument("--dry-run", action="store_true", help="Yalnız say, kuyruğa alma")
    args = parser.parse_args()
    load_dotenv()
    report = enqueue(date.fromisoformat(args.since), datetime.now(TZ).date(), args.sample, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
