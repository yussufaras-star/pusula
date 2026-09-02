"""Panel hazırlık çubuğu — eşikler, blok durumu, mesai dışı uyarı yok.

Kullanım:
    python scripts/panel_check.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from pusula.blocks import ISTANBUL
from pusula.freshness import FRESHNESS_THRESHOLDS, is_mesai
from pusula.panel_status import (
    format_block_line,
    format_source_line,
    load_panel_readiness,
)


def main() -> int:
    print("esikler:")
    for key, value in FRESHNESS_THRESHOLDS.items():
        print(f"  {key}: {value}")
    now = datetime.now(ISTANBUL)
    print(f"simdi: {now.isoformat(timespec='minutes')} mesai={is_mesai(now)}")
    weekend = now + timedelta(days=(5 - now.weekday()) % 7 or 7)
    weekend = weekend.replace(hour=12, minute=0, second=0, microsecond=0)
    night = now.replace(hour=21, minute=0, second=0, microsecond=0)
    print(f"hafta sonu mesai={is_mesai(weekend)}")
    print(f"gece mesai={is_mesai(night)}")
    if is_mesai(weekend) or is_mesai(night):
        print("hata: mesai disi True dondu")
        return 1

    ready = load_panel_readiness(now)
    print("bloklar:")
    print(f"  {format_block_line(ready.blocks)}")
    for item in ready.blocks:
        print(f"  {item.label} {item.state} written={item.written_at}")
    print("tazelik:")
    print(f"  {format_source_line(ready)}")
    print(f"  all_fresh={ready.all_fresh} warn={ready.warn}")
    off = load_panel_readiness(night)
    print(f"mesai disi warn={off.warn} (beklenen False)")
    if off.warn:
        print("hata: mesai disinda uyari")
        return 1
    failed = [item.label for item in ready.blocks if item.state == "calismadi"]
    print(f"calismadi: {failed if failed else '(yok)'}")
    print("panel_check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
