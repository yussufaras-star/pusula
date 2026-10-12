"""Hourly KPI guardrails and nudge time-window behavior."""

from datetime import datetime
from zoneinfo import ZoneInfo

from pusula.hourly_nudges import _control_hour, format_kpi_message
from pusula.kpi_standards import (
    evaluate_hourly_kpis,
    hour_kinds_for,
    latest_ready_hour,
)

_TZ = ZoneInfo("Europe/Istanbul")


def test_call_and_reach_alerts_use_historical_same_hour_standards() -> None:
    alerts = evaluate_hourly_kpis(
        {
            "arama": 4,
            "lead_payda": 5,
            "ulasma_orani": 10.0,
        },
        {
            "hist_n": 20,
            "arama": 10.0,
            "lead_payda": 50,
            "ulasma_orani": 30.0,
        },
        hour=9,
        is_meeting_hour=False,
    )

    assert [item["key"] for item in alerts] == ["outbound_calls", "reach_rate"]
    assert alerts[0]["target"] == 8.0
    assert alerts[1]["target"] == 24.0


def test_call_at_standard_and_low_sample_rates_do_not_alert() -> None:
    alerts = evaluate_hourly_kpis(
        {"arama": 8, "lead_payda": 4, "ulasma_orani": 0.0},
        {
            "hist_n": 9,
            "arama": 10.0,
            "lead_payda": 40,
            "ulasma_orani": 50.0,
        },
        hour=9,
        is_meeting_hour=False,
    )

    assert alerts == []


def test_meeting_attendance_and_missing_outcomes_alert() -> None:
    alerts = evaluate_hourly_kpis(
        {
            "katilim_payda": 5,
            "katilim_orani": 60.0,
            "sonuc_girilmedi": 2,
        },
        {},
        hour=11,
        is_meeting_hour=True,
    )

    assert [item["key"] for item in alerts] == [
        "meeting_attendance",
        "meeting_outcome",
    ]


def test_meeting_rates_require_minimum_sample() -> None:
    alerts = evaluate_hourly_kpis(
        {
            "katilim_payda": 4,
            "katilim_orani": 0.0,
            "sonuc_girilmedi": 0,
        },
        {},
        hour=11,
        is_meeting_hour=True,
    )

    assert alerts == []


def test_saturday_is_meeting_block_and_mixed_hours_check_both() -> None:
    saturday = datetime(2026, 10, 17).date()
    weekday = datetime(2026, 10, 12).date()
    assert hour_kinds_for(saturday, 14) == (False, True)  # cumartesi tamamı toplantı
    assert hour_kinds_for(weekday, 11) == (False, True)
    assert hour_kinds_for(weekday, 9) == (True, False)

    alerts = evaluate_hourly_kpis(
        {
            "arama": 0,
            "lead_payda": 5,
            "ulasma_orani": 0.0,
            "katilim_payda": 5,
            "katilim_orani": 60.0,
            "sonuc_girilmedi": 1,
        },
        {
            "hist_n": 20,
            "arama": 10,
            "lead_payda": 50,
            "ulasma_orani": 40.0,
        },
        hour=14,
        is_meeting_hour=True,
        is_call_hour=True,
    )

    assert [item["key"] for item in alerts] == [
        "outbound_calls",
        "reach_rate",
        "meeting_attendance",
        "meeting_outcome",
    ]


def test_hourly_control_runs_only_after_data_window_and_inside_hours() -> None:
    assert _control_hour(datetime(2026, 10, 12, 10, 10, tzinfo=_TZ)) is None
    assert _control_hour(datetime(2026, 10, 12, 10, 20, tzinfo=_TZ)) == 9
    assert _control_hour(datetime(2026, 10, 12, 18, 20, tzinfo=_TZ)) == 17
    assert _control_hour(datetime(2026, 10, 12, 19, 20, tzinfo=_TZ)) is None
    assert _control_hour(datetime(2026, 10, 17, 14, 50, tzinfo=_TZ)) == 13
    assert _control_hour(datetime(2026, 10, 17, 15, 20, tzinfo=_TZ)) is None
    assert latest_ready_hour(datetime(2026, 10, 17, 15, 14, tzinfo=_TZ)) == 13
    assert latest_ready_hour(datetime(2026, 10, 17, 15, 15, tzinfo=_TZ)) == 14


def test_nudge_message_reports_threshold_and_next_step() -> None:
    message = format_kpi_message(
        day=datetime(2026, 10, 12).date(),
        hour=9,
        alerts=[
            {
                "key": "outbound_calls",
                "label": "Giden arama",
                "actual": 4,
                "target": 8,
                "unit": "arama",
                "sample": None,
                "hour": 9,
            }
        ],
        recipient_name="Temsilci",
        is_team=False,
    )

    assert "09:00–10:00" in message
    assert "hedef en az 8" in message
    assert "Sonraki uygun blokta" in message
