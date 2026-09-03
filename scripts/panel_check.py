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
    BlockReady,
    format_block_line,
    format_source_line,
    load_panel_readiness,
    should_warn,
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

    weekday_ten = datetime(2026, 9, 3, 10, 0, tzinfo=ISTANBUL)
    weekday_noon = datetime(2026, 9, 3, 12, 0, tzinfo=ISTANBUL)
    ready_ten = load_panel_readiness(weekday_ten)
    print("10:00 senaryo:")
    print(f"  {format_block_line(ready_ten.blocks)}")
    print(f"  {format_source_line(ready_ten)}")
    print(f"  warn={ready_ten.warn} (beklenen False)")
    if ready_ten.warn:
        print("hata: 10:00 tazelik uyarisi")
        return 1
    waiting = [
        BlockReady(label="11:15", state="bekleniyor", written_at=None),
        BlockReady(label="14:15", state="bekleniyor", written_at=None),
        BlockReady(label="17:15", state="bekleniyor", written_at=None),
        BlockReady(label="18:15", state="bekleniyor", written_at=None),
    ]
    if should_warn(waiting, weekday_ten):
        print("hata: ilk blok oncesi uyari")
        return 1

    missed = [
        BlockReady(label="11:15", state="calismadi", written_at=None),
        BlockReady(label="14:15", state="bekleniyor", written_at=None),
        BlockReady(label="17:15", state="bekleniyor", written_at=None),
        BlockReady(label="18:15", state="bekleniyor", written_at=None),
    ]
    missed_warn = should_warn(missed, weekday_noon)
    print("11:15 kacmis senaryo:")
    print(f"  {format_block_line(missed)}")
    print(f"  warn={missed_warn} (beklenen True)")
    if not missed_warn:
        print("hata: 11:15 kacmis uyarisi yok")
        return 1
    ready_noon = load_panel_readiness(weekday_noon)
    print("12:00 canli (3 Eylul):")
    print(f"  {format_block_line(ready_noon.blocks)}")
    print(f"  warn={ready_noon.warn}")

    print("panel_check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
