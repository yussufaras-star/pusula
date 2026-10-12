"""Canlı ingest — aramalar, WhatsApp, CRM işlemleri; çeyrekte bir pencereli kaynaklar.

Kullanım:
    python scripts/live_ingest.py
    python scripts/live_ingest.py --force   # mesai dışında da, tüm adımlar
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.live_ingest import run_live_ingest


def main() -> int:
    parser = argparse.ArgumentParser(description="Canlı ingest.")
    parser.add_argument("--force", action="store_true", help="Mesai dışında da çalıştır")
    parser.add_argument("--budget", type=float, default=240.0, help="Adım başlatma süresi (sn)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()

    report = run_live_ingest(force=args.force, budget_sec=args.budget)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    steps = report.get("steps", {})
    failed = [name for name, step in steps.items() if isinstance(step, dict) and "error" in step]
    if failed:
        print(f"hata: basarisiz adimlar: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
