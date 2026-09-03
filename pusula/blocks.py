"""Gün içi zaman blokları.

09-11 arama, 11-14 toplantı, 14-17 arama, 17-18 toplantı.
Blok sonu ingest bitimden 15 dakika sonra çalışır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")
LOOKBACK_HOURS = 6
BOOKINGS_LOOKBACK_HOURS = 24


@dataclass(frozen=True)
class DayBlock:
    key: str
    label: str
    kind: str  # call | meeting | other
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

BLOK_DISI = DayBlock("blok_disi", "blok dışı", "other", 0, 0, 0, 0)


CardPhase = Literal["baslamadi", "devam_ediyor", "tamamlandi"]


def to_istanbul(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ISTANBUL)
    return value.astimezone(ISTANBUL)


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


def hour_in_planned_sql(hour_expr: str) -> str:
    """Planlı dört bloğun saat aralığı (OR)."""
    parts = [
        f"({hour_expr} >= {b.start_hour} AND {hour_expr} < {b.end_hour})"
        for b in PLANNED_BLOCKS
    ]
    return "(" + " OR ".join(parts) + ")"
