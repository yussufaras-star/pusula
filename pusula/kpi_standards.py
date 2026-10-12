"""Hourly KPI guardrails based on the existing panel definitions."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pusula.blocks import blocks_for

HISTORICAL_DAYS_MIN = 10
RATE_SAMPLE_MIN = 5
ACTIVITY_BASELINE_RATIO = 0.80
RATE_BASELINE_RATIO = 0.80
ATTENDANCE_RATE_MIN = 70.0
_TZ = ZoneInfo("Europe/Istanbul")

STANDARDS: dict[str, dict[str, Any]] = {
    "outbound_calls": {
        "label": "Giden arama",
        "rule": "Aynı gün türü ve saatin son 90 gün ortalamasının en az %80'i",
        "baseline_ratio": ACTIVITY_BASELINE_RATIO,
    },
    "reach_rate": {
        "label": "Lead ulaşma oranı",
        "rule": "Aynı saat geçmiş oranının en az %80'i; en az 5 lead",
        "baseline_ratio": RATE_BASELINE_RATIO,
        "minimum_sample": RATE_SAMPLE_MIN,
    },
    "meeting_attendance": {
        "label": "Toplantı katılım oranı",
        "rule": "En az %70; en az 5 sonucu girilmiş randevu",
        "minimum_rate": ATTENDANCE_RATE_MIN,
        "minimum_sample": RATE_SAMPLE_MIN,
    },
    "meeting_outcome": {
        "label": "Toplantı sonuç kaydı",
        "rule": "Tamamlanan toplantıların sonucu CRM'de kayıtlı olmalı",
    },
}


def hour_kinds_for(day: date, hour: int) -> tuple[bool, bool]:
    """Return whether the hour has call and/or meeting work in its block."""
    blocks = blocks_for(day)
    calls = any(
        block.kind in ("call", "mixed")
        and block.start_hour <= hour < block.end_hour
        for block in blocks
    )
    meetings = any(
        block.kind in ("meeting", "mixed")
        and block.start_hour <= hour < block.end_hour
        for block in blocks
    )
    return calls, meetings


def latest_ready_hour(now: datetime) -> int | None:
    """Latest hour with a completed scheduled ingest, including after-hours view."""
    local = now.astimezone(_TZ) if now.tzinfo else now.replace(tzinfo=_TZ)
    if local.weekday() == 6:
        return None
    if local.weekday() == 5:
        if local.hour > 15 or (local.hour == 15 and local.minute >= 15):
            return 14
        if local.hour == 15:
            return 13
        if not 10 <= local.hour <= 14:
            return None
    else:
        if local.hour >= 19:
            return 17
        if not 10 <= local.hour <= 18:
            return None
    if local.minute >= 15:
        return local.hour - 1
    return local.hour - 2 if local.hour >= 11 else None


def evaluate_hourly_kpis(
    current: dict[str, Any],
    historical: dict[str, Any],
    *,
    hour: int,
    is_meeting_hour: bool,
    is_call_hour: bool | None = None,
) -> list[dict[str, Any]]:
    """Return actionable gaps; rates are suppressed when sample sizes are small."""
    alerts: list[dict[str, Any]] = []
    hist_days = int(historical.get("hist_n") or 0)
    check_calls = not is_meeting_hour if is_call_hour is None else is_call_hour

    if check_calls:
        calls = int(current.get("arama") or 0)
        typical_calls = float(historical.get("arama") or 0)
        if hist_days >= HISTORICAL_DAYS_MIN and typical_calls > 0:
            target = round(typical_calls * ACTIVITY_BASELINE_RATIO, 1)
            if calls < target:
                alerts.append(
                    {
                        "key": "outbound_calls",
                        "label": "Giden arama",
                        "actual": calls,
                        "target": target,
                        "unit": "arama",
                        "sample": None,
                        "hour": hour,
                    }
                )

        lead_count = int(current.get("lead_payda") or 0)
        baseline_leads = int(historical.get("lead_payda") or 0)
        actual_rate = current.get("ulasma_orani")
        baseline_rate = historical.get("ulasma_orani")
        if (
            hist_days >= HISTORICAL_DAYS_MIN
            and lead_count >= RATE_SAMPLE_MIN
            and baseline_leads >= RATE_SAMPLE_MIN
            and actual_rate is not None
            and baseline_rate is not None
        ):
            target_rate = round(
                float(baseline_rate) * RATE_BASELINE_RATIO, 1
            )
            if float(actual_rate) < target_rate:
                alerts.append(
                    {
                        "key": "reach_rate",
                        "label": "Lead ulaşma oranı",
                        "actual": round(float(actual_rate), 1),
                        "target": target_rate,
                        "unit": "%",
                        "sample": lead_count,
                        "hour": hour,
                    }
                )

    if is_meeting_hour:
        resolved_meetings = int(current.get("katilim_payda") or 0)
        attendance = current.get("katilim_orani")
        if (
            resolved_meetings >= RATE_SAMPLE_MIN
            and attendance is not None
            and float(attendance) < ATTENDANCE_RATE_MIN
        ):
            alerts.append(
                {
                    "key": "meeting_attendance",
                    "label": "Toplantı katılım oranı",
                    "actual": round(float(attendance), 1),
                    "target": ATTENDANCE_RATE_MIN,
                    "unit": "%",
                    "sample": resolved_meetings,
                    "hour": hour,
                }
            )

        unrecorded = int(current.get("sonuc_girilmedi") or 0)
        if unrecorded:
            alerts.append(
                {
                    "key": "meeting_outcome",
                    "label": "Toplantı sonuç kaydı",
                    "actual": unrecorded,
                    "target": 0,
                    "unit": "kayıtsız randevu",
                    "sample": None,
                    "hour": hour,
                }
            )
    return alerts
