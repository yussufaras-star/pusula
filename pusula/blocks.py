"""Gün içi zaman blokları.

Hafta içi: 09-11 arama, 11-14 toplantı, 14-17 arama, 17-18 toplantı.
Cumartesi: tek karışık blok 09-15. Pazar yok.
Ingest saat başı (dakika 07) çalışır; blok bitişine bağlı değildir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")
LOOKBACK_HOURS = 6
BOOKINGS_LOOKBACK_HOURS = 24
SAT_BADGE_MIN = 4


@dataclass(frozen=True)
class DayBlock:
    key: str
    label: str
    kind: str  # call | meeting | mixed | other
    start_hour: int
    end_hour: int  # yarım açık [start, end)
    ingest_hour: int
    ingest_minute: int


PLANNED_BLOCKS: tuple[DayBlock, ...] = (
    DayBlock("arama_09_11", "09-11 arama blogu", "call", 9, 11, 11, 15),
    DayBlock("toplanti_11_14", "11-14 toplanti blogu", "meeting", 11, 14, 14, 15),
    DayBlock("arama_14_17", "14-17 arama blogu", "call", 14, 17, 17, 15),
    DayBlock("toplanti_17_18", "17-18 toplanti blogu", "meeting", 17, 18, 18, 15),
)

SATURDAY_BLOCK = DayBlock(
    "cumartesi_09_15", "09-15", "mixed", 9, 15, 15, 7
)

BLOK_DISI = DayBlock("blok_disi", "blok dışı", "other", 0, 0, 0, 0)

_ALL_DISPLAY_BLOCKS: tuple[DayBlock, ...] = PLANNED_BLOCKS + (SATURDAY_BLOCK,)


CardPhase = Literal["baslamadi", "devam_ediyor", "tamamlandi"]


def to_istanbul(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ISTANBUL)
    return value.astimezone(ISTANBUL)


def blocks_for(day: date) -> tuple[DayBlock, ...]:
    """Seçilen günün planlı blokları. Pazar boş, cumartesi tek karışık."""
    weekday = day.weekday()
    if weekday == 5:
        return (SATURDAY_BLOCK,)
    if weekday == 6:
        return ()
    return PLANNED_BLOCKS


def block_by_key(key: str) -> DayBlock | None:
    if key == BLOK_DISI.key:
        return BLOK_DISI
    for item in _ALL_DISPLAY_BLOCKS:
        if item.key == key:
            return item
    return None


def card_phase(block: DayBlock, day: date, now: datetime) -> CardPhase:
    """Kart hali: [start, end) devam, end ve sonrasi tamamlandi."""
    local = to_istanbul(now)
    start_at = datetime.combine(day, time(hour=block.start_hour), tzinfo=ISTANBUL)
    end_at = datetime.combine(day, time(hour=block.end_hour), tzinfo=ISTANBUL)
    if local < start_at:
        return "baslamadi"
    if local < end_at:
        return "devam_ediyor"
    return "tamamlandi"


def ended_block(now: datetime) -> DayBlock:
    """Saate göre bitmiş son blok. Tetik 11:15 / 14:15 / 17:15 / 18:15."""
    local = to_istanbul(now)
    clock = time(local.hour, local.minute, local.second)
    chosen = PLANNED_BLOCKS[-1]
    for block in PLANNED_BLOCKS:
        trigger = time(block.ingest_hour, block.ingest_minute)
        if clock >= trigger:
            chosen = block
    return chosen


def hours_of(block: DayBlock) -> tuple[int, ...]:
    """Bloğun [start, end) saatleri. 09-11 → 9, 10. Cumartesi 09-15 → 9..14."""
    if block.end_hour <= block.start_hour:
        return ()
    return tuple(range(block.start_hour, block.end_hour))


def hour_in_blocks_sql(hour_expr: str, blocks: tuple[DayBlock, ...]) -> str:
    """Verilen blokların saat aralığı (OR). Boşsa FALSE."""
    if not blocks:
        return "FALSE"
    parts = [
        f"({hour_expr} >= {b.start_hour} AND {hour_expr} < {b.end_hour})"
        for b in blocks
    ]
    return "(" + " OR ".join(parts) + ")"


def hour_in_planned_sql(hour_expr: str) -> str:
    """Hafta içi dört bloğun saat aralığı (OR)."""
    return hour_in_blocks_sql(hour_expr, PLANNED_BLOCKS)
