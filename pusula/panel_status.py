"""Panel üst çubuğu: blok ingest hazırlığı ve kaynak tazeliği."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pusula.blocks import PLANNED_BLOCKS, DayBlock
from pusula.config import get_org_id
from pusula.freshness import FRESHNESS_THRESHOLDS, is_mesai
from pusula.panel_data import connect

_TZ = ZoneInfo("Europe/Istanbul")

BlockState = Literal["tamamlandi", "bekleniyor", "calismadi"]
SourceKey = Literal["aramalar", "randevular", "leadler", "kisiler"]

_SOURCE_LABELS: dict[str, str] = {
    "aramalar": "aramalar",
    "randevular": "randevular",
    "leadler": "lead'ler",
    "kisiler": "kişiler",
}


@dataclass(frozen=True)
class BlockReady:
    label: str
    state: BlockState
    written_at: datetime | None


@dataclass(frozen=True)
class SourceFresh:
    key: str
    label: str
    latest: datetime | None
    stale: bool


@dataclass(frozen=True)
class PanelReadiness:
    blocks: list[BlockReady]
    sources: list[SourceFresh]
    all_fresh: bool
    warn: bool
    now: datetime


def _as_istanbul(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_TZ)
    return value.astimezone(_TZ)


def _trigger_at(day: date, block: DayBlock) -> datetime:
    return datetime.combine(
        day, time(block.ingest_hour, block.ingest_minute), tzinfo=_TZ
    )


def _block_windows(day: date) -> list[tuple[DayBlock, datetime, datetime]]:
    windows: list[tuple[DayBlock, datetime, datetime]] = []
    for index, block in enumerate(PLANNED_BLOCKS):
        start = _trigger_at(day, block)
        if index + 1 < len(PLANNED_BLOCKS):
            nxt = PLANNED_BLOCKS[index + 1]
            end = _trigger_at(day, nxt)
        else:
            end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=_TZ)
        windows.append((block, start, end))
    return windows


def _fmt_clock(value: datetime) -> str:
    return _as_istanbul(value).strftime("%H:%M")


def load_panel_readiness(now: datetime | None = None) -> PanelReadiness:
    """Bugünün blok yazımları ve dört kaynağın son created_at'i."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    day = local.date()
    org_id = get_org_id()
    windows = _block_windows(day)
    first_start = windows[0][1]
    last_end = windows[-1][2]

    with connect() as conn:
        slot_rows = conn.execute(
            """
            SELECT channel, created_at
            FROM events
            WHERE org_id = %s
              AND channel IN ('call', 'meeting')
              AND created_at >= %s
              AND created_at < %s
            """,
            (org_id, first_start, last_end),
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

    per_slot: dict[str, dict[str, datetime]] = {}
    for channel, created in slot_rows:
        if created is None:
            continue
        created_dt = _as_istanbul(created)
        for block, start, end in windows:
            if start <= created_dt < end:
                bucket = per_slot.setdefault(block.key, {})
                prev = bucket.get(str(channel))
                if prev is None or created_dt > prev:
                    bucket[str(channel)] = created_dt
                break

    blocks: list[BlockReady] = []
    for block, start, end in windows:
        label = f"{block.ingest_hour:02d}:{block.ingest_minute:02d}"
        if local < start:
            blocks.append(BlockReady(label=label, state="bekleniyor", written_at=None))
            continue
        slot = per_slot.get(block.key, {})
        call_at = slot.get("call")
        meet_at = slot.get("meeting")
        if call_at is not None and meet_at is not None:
            written = call_at if call_at >= meet_at else meet_at
            blocks.append(
                BlockReady(label=label, state="tamamlandi", written_at=written)
            )
        else:
            blocks.append(BlockReady(label=label, state="calismadi", written_at=None))

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
    warn = (not all_fresh) and is_mesai(local)
    return PanelReadiness(
        blocks=blocks,
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


def format_block_line(blocks: list[BlockReady]) -> str:
    parts: list[str] = []
    for item in blocks:
        if item.state == "tamamlandi" and item.written_at is not None:
            parts.append(f"{item.label} tamamlandı {_fmt_clock(item.written_at)}")
        elif item.state == "bekleniyor":
            parts.append(f"{item.label} bekleniyor")
        else:
            parts.append(f":red[{item.label} çalışmadı]")
    return " · ".join(parts)


def format_source_line(ready: PanelReadiness) -> str:
    if ready.all_fresh:
        return "aramalar, randevular, lead'ler, kişiler güncel"
    bits: list[str] = []
    for item in ready.sources:
        clock = _fmt_clock(item.latest) if item.latest is not None else "—"
        flag = "eski" if item.stale else "güncel"
        bits.append(f"{item.label} {flag} ({clock})")
    return " · ".join(bits)
