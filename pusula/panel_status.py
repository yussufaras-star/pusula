"""Panel üst çubuğu: saatlik ingest hazırlığı ve kaynak tazeliği."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pusula.config import get_org_id
from pusula.freshness import FRESHNESS_THRESHOLDS, is_mesai
from pusula.panel_data import connect

_TZ = ZoneInfo("Europe/Istanbul")
INGEST_MINUTE = 7
_GRACE = timedelta(minutes=15)
_WEEKDAY_HOURS: tuple[int, ...] = tuple(range(9, 20))
_SATURDAY_HOURS: tuple[int, ...] = tuple(range(9, 16))

SourceKey = Literal["aramalar", "randevular", "leadler", "kisiler"]

_SOURCE_LABELS: dict[str, str] = {
    "aramalar": "aramalar",
    "randevular": "randevular",
    "leadler": "lead'ler",
    "kisiler": "kişiler",
}


@dataclass(frozen=True)
class SourceFresh:
    key: str
    label: str
    latest: datetime | None
    stale: bool


@dataclass(frozen=True)
class PanelReadiness:
    last_at: datetime | None
    next_at: datetime | None
    due_at: datetime | None
    missed: bool
    sources: list[SourceFresh]
    all_fresh: bool
    warn: bool
    now: datetime


def _as_istanbul(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_TZ)
    return value.astimezone(_TZ)


def _fmt_clock(value: datetime) -> str:
    return _as_istanbul(value).strftime("%H:%M")


def ingest_hours_for(day: date) -> tuple[int, ...]:
    """O gün beklenen ingest saatleri (Istanbul). Pazar boş."""
    weekday = day.weekday()
    if weekday == 6:
        return ()
    if weekday == 5:
        return _SATURDAY_HOURS
    return _WEEKDAY_HOURS


def ingest_slots(day: date) -> list[datetime]:
    return [
        datetime.combine(day, time(hour, INGEST_MINUTE), tzinfo=_TZ)
        for hour in ingest_hours_for(day)
    ]


def next_ingest_at(now: datetime) -> datetime | None:
    """Şimdiden sonraki ilk ingest yuvası. Pazarı atlar."""
    local = _as_istanbul(now)
    for offset in range(0, 8):
        day = local.date() + timedelta(days=offset)
        for slot in ingest_slots(day):
            if slot > local:
                return slot
    return None


def last_due_slot(now: datetime) -> datetime | None:
    """Başlamış olması gereken son yuva."""
    local = _as_istanbul(now)
    for offset in range(0, 8):
        day = local.date() - timedelta(days=offset)
        due = [slot for slot in ingest_slots(day) if slot <= local]
        if due:
            return due[-1]
    return None


def slot_succeeded(
    slot: datetime,
    stamps: list[datetime],
) -> bool:
    """Yuvada [slot, slot+1s) yazım veya işaret var mı."""
    end = slot + timedelta(hours=1)
    for stamp in stamps:
        if slot <= stamp < end:
            return True
    return False


def load_panel_readiness(now: datetime | None = None) -> PanelReadiness:
    """Son başarılı saatlik ingest ve dört kaynağın tazeliği."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    org_id = get_org_id()
    lookback = local - timedelta(days=3)

    with connect() as conn:
        marker_rows = conn.execute(
            """
            SELECT last_synced_at
            FROM sync_state
            WHERE org_id = %s
              AND source_name LIKE 'hourly_slot:%%'
              AND last_synced_at >= %s
            """,
            (org_id, lookback),
        ).fetchall()
        write_rows = conn.execute(
            """
            SELECT created_at FROM events
            WHERE org_id = %s AND channel IN ('call', 'meeting')
              AND created_at >= %s
            UNION ALL
            SELECT created_at FROM leads
            WHERE org_id = %s AND created_at >= %s
            UNION ALL
            SELECT created_at FROM contacts
            WHERE org_id = %s AND created_at >= %s
            """,
            (org_id, lookback, org_id, lookback, org_id, lookback),
        ).fetchall()
        call_latest = conn.execute(
            """
            SELECT max(created_at) FROM events
            WHERE org_id = %s AND channel = 'call'
            """,
            (org_id,),
        ).fetchone()
        meet_latest = conn.execute(
            """
            SELECT max(created_at) FROM events
            WHERE org_id = %s AND channel = 'meeting'
            """,
            (org_id,),
        ).fetchone()
        lead_latest = conn.execute(
            "SELECT max(created_at) FROM leads WHERE org_id = %s",
            (org_id,),
        ).fetchone()
        contact_latest = conn.execute(
            "SELECT max(created_at) FROM contacts WHERE org_id = %s",
            (org_id,),
        ).fetchone()

    stamps: list[datetime] = []
    for row in marker_rows:
        ts = _ts(row)
        if ts is not None:
            stamps.append(_as_istanbul(ts))
    for row in write_rows:
        ts = _ts(row)
        if ts is not None:
            stamps.append(_as_istanbul(ts))

    last_at = max(stamps) if stamps else None
    next_at = next_ingest_at(local)
    due_at = last_due_slot(local)
    missed = False
    if due_at is not None and local >= due_at + _GRACE:
        missed = not slot_succeeded(due_at, stamps)

    raw_latest: dict[str, datetime | None] = {
        "aramalar": _ts(call_latest),
        "randevular": _ts(meet_latest),
        "leadler": _ts(lead_latest),
        "kisiler": _ts(contact_latest),
    }
    sources: list[SourceFresh] = []
    for key, latest in raw_latest.items():
        thresh = FRESHNESS_THRESHOLDS[key]
        stale = latest is None or (local - _as_istanbul(latest)) > thresh
        sources.append(
            SourceFresh(
                key=key,
                label=_SOURCE_LABELS[key],
                latest=latest,
                stale=stale,
            )
        )
    all_fresh = all(not item.stale for item in sources)
    warn = should_warn(missed, local)
    return PanelReadiness(
        last_at=last_at,
        next_at=next_at,
        due_at=due_at,
        missed=missed,
        sources=sources,
        all_fresh=all_fresh,
        warn=warn,
        now=local,
    )


def _ts(row: Any) -> datetime | None:
    if not row or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, datetime):
        return value
    return None


def should_warn(missed: bool, now: datetime) -> bool:
    """Uyarı yalnız mesaide beklenen ingest kaçtıysa."""
    if not is_mesai(now):
        return False
    return missed


def format_block_line(ready: PanelReadiness) -> str:
    """Son başarılı ingest ve sonraki beklenen saat."""
    last = _fmt_clock(ready.last_at) if ready.last_at is not None else "yok"
    nxt = _fmt_clock(ready.next_at) if ready.next_at is not None else "—"
    if ready.missed and ready.due_at is not None:
        due = _fmt_clock(ready.due_at)
        return f"son ingest {last} · :red[{due} kaçtı] · sonraki {nxt}"
    return f"son ingest {last} · sonraki {nxt}"


def format_source_line(ready: PanelReadiness) -> str:
    """Son yazım saati. Eşikler panel (i) balonunda."""
    bits: list[str] = []
    for item in ready.sources:
        clock = _fmt_clock(item.latest) if item.latest is not None else "—"
        bits.append(f"{item.label} ({clock})")
    return " · ".join(bits)


def format_impact_line(ready: PanelReadiness) -> str | None:
    """Kaçan ingest kartları bir saat geride bırakabilir."""
    if not ready.missed or ready.due_at is None:
        return None
    due = _fmt_clock(ready.due_at)
    return (
        f"{due} ingest çalışmadı. Kartlar bir saat geride kalabilir; "
        "günlük özet ve dönemsel tablolar etkilenmedi."
    )
