"""Canlı kokpit ve KPI vektörü: veritabanı gerektirmeyen saf hesaplar."""

from datetime import date, datetime

import pytest

from pusula.live import baseline_until, build_live, rep_status, scale
from pusula.metrics import (
    TZ,
    Span,
    available_minutes,
    combine,
    finish,
    idle_minutes,
    in_shift,
    occupancy_minutes,
)

MONDAY = date(2026, 10, 12)
SATURDAY = date(2026, 10, 10)
SUNDAY = date(2026, 10, 11)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def test_full_weekday_has_seven_hours_forty_five_minutes() -> None:
    assert available_minutes(Span.day(MONDAY), at(MONDAY, 23)) == pytest.approx(465)


def test_full_saturday_has_four_hours_forty_five_minutes() -> None:
    assert available_minutes(Span.day(SATURDAY), at(SATURDAY, 23)) == pytest.approx(285)


def test_sunday_and_before_shift_have_no_minutes() -> None:
    assert available_minutes(Span.day(SUNDAY), at(SUNDAY, 23)) == 0
    assert available_minutes(Span.day(MONDAY), at(MONDAY, 8, 30)) == 0


def test_mid_day_minutes_are_proportional_to_elapsed_shift() -> None:
    # 09:00-13:30 = 270 dk mesai; 465/540 çalışma oranı.
    assert available_minutes(Span.day(MONDAY), at(MONDAY, 13, 30)) == pytest.approx(
        270 * 465 / 540
    )


def test_multi_day_span_sums_shift_days() -> None:
    span = Span.days(SATURDAY, MONDAY)
    assert available_minutes(span, at(MONDAY, 23)) == pytest.approx(285 + 465)


def test_occupancy_matches_panel_assumptions() -> None:
    row = {
        "arama": 30,
        "giden_temas": 10,
        "call_sec": 1800,
        "meet_dk": 30,
        "whatsapp": 5,
    }
    # 30 + 30 + 20×0.5 + 10×1.5 + 30×(20/60) + 5×1
    assert occupancy_minutes(row) == pytest.approx(30 + 30 + 10 + 15 + 10 + 5)


def test_finish_derives_rates_and_caps_occupancy() -> None:
    row = combine(
        [
            {"arama": 10, "giden_temas": 4, "lead_payda": 8, "lead_pay": 2,
             "katildi": 3, "katilmadi": 1, "sure_toplam": 400.0, "call_sec": 99999},
        ]
    )
    out = finish(row, avail_min=100)

    assert out["ulasma_orani"] == 25.0
    assert out["katilim_orani"] == 75.0
    assert out["sure_ort"] == 100.0
    assert out["doluluk"] == 100.0


def test_finish_without_denominator_gives_none() -> None:
    out = finish(combine([]), avail_min=0)
    assert out["doluluk"] is None
    assert out["ulasma_orani"] is None


def test_idle_counts_from_shift_start_when_no_activity() -> None:
    now = at(MONDAY, 9, 40)
    assert idle_minutes(None, now=now) == 40


def test_idle_is_capped_by_data_freshness() -> None:
    now = at(MONDAY, 11, 0)
    last = at(MONDAY, 10, 0)
    as_of = at(MONDAY, 10, 30)
    assert idle_minutes(last, now=now, data_as_of=as_of) == 30


def test_idle_outside_shift_is_none() -> None:
    assert idle_minutes(None, now=at(MONDAY, 19)) is None
    assert not in_shift(at(SUNDAY, 11))


def test_rep_status_thresholds() -> None:
    now = at(MONDAY, 11)
    first = at(MONDAY, 9, 5)
    assert rep_status(now=now, first_at=None, idle_min=120) == "baslamadi"
    assert rep_status(now=now, first_at=first, idle_min=5) == "aktif"
    assert rep_status(now=now, first_at=first, idle_min=20) == "bosta"
    assert rep_status(now=now, first_at=first, idle_min=45) == "hareketsiz"
    assert rep_status(now=at(MONDAY, 20), first_at=first, idle_min=None) == "mesai_disi"


def test_baseline_until_prorates_current_hour() -> None:
    hours = {9: {"arama": 10}, 10: {"arama": 10}, 11: {"arama": 10}}
    out = baseline_until(hours, at(MONDAY, 10, 30))
    assert out["arama"] == pytest.approx(15)


def test_scale_divides_counts_only() -> None:
    out = scale({"arama": 90, "ulasilan": 30, "sure_ort": 120.0}, 1 / 90)
    assert out["arama"] == 1
    assert out["sure_ort"] == 120.0


def test_build_live_assembles_team_and_rep_cards() -> None:
    now = at(MONDAY, 10, 30)
    today_rows = [
        {"rep_id": "r1", "bucket": "9", "arama": 12, "giden_temas": 4, "donus": 1,
         "ulasilan": 5, "lead_payda": 10, "lead_pay": 4, "call_sec": 600},
        {"rep_id": "r1", "bucket": "10", "arama": 6, "giden_temas": 2, "donus": 0,
         "ulasilan": 2, "lead_payda": 6, "lead_pay": 2, "call_sec": 300},
    ]
    history = {"r1": {9: {"arama": 10.0}, 10: {"arama": 10.0}}}
    activity = {
        "r1": {"first_at": at(MONDAY, 9, 2), "last_active_at": at(MONDAY, 10, 20)},
    }
    out = build_live(
        day=MONDAY,
        now=now,
        reps=[("r1", "Ayşe"), ("r2", "Mehmet")],
        today_rows=today_rows,
        history=history,
        activity=activity,
        as_of=now,
    )

    assert out["team"]["today"]["arama"] == 18
    assert out["team"]["expected"]["arama"] == pytest.approx(15)
    cards = {c["rep_id"]: c for c in out["reps"]}
    assert cards["r1"]["status"] == "aktif"
    assert cards["r1"]["idle_min"] == 10
    assert cards["r2"]["status"] == "baslamadi"
    assert [h["hour"] for h in out["hours"]] == list(range(9, 18))
    assert out["hours"][0]["done"] and not out["hours"][1]["done"]
