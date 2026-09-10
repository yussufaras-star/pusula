"""Ciro dönem, iş günü, izdüşüm — DB yok."""

from datetime import date

from pusula.panel_ciro import (
    REAL_SALES_START,
    add_months,
    is_workday,
    month_first,
    nth_workday,
    resolve_ciro_period,
    with_monthly_team_totals,
    workdays_in_month,
    workdays_inclusive,
)
from pusula.panel_data import DateWindow, fmt_window


def test_september_2026_workdays() -> None:
    today = date(2026, 9, 10)
    assert workdays_inclusive(date(2026, 9, 1), today) == 9
    assert workdays_in_month(2026, 9) == 26
    assert nth_workday(date(2026, 9, 1), 9) == today
    assert is_workday(date(2026, 9, 5)) is True  # cumartesi
    assert is_workday(date(2026, 9, 6)) is False  # pazar


def test_forecast_formula_sep_10() -> None:
    mtd = 1_196_874.0
    elapsed = 9
    month_wd = 26
    daily = mtd / elapsed
    forecast = daily * month_wd
    assert round(forecast) == 3_457_636


def test_bu_ay_default_window() -> None:
    today = date(2026, 9, 10)
    start, end = resolve_ciro_period("bu_ay", today)
    assert start == date(2026, 9, 1)
    assert end == today
    start6, end6 = resolve_ciro_period("son_6_ay", today)
    assert start6 == REAL_SALES_START
    assert end6 == today
    all_s, all_e = resolve_ciro_period("tum_zamanlar", today)
    assert all_s == REAL_SALES_START
    assert all_e == today
    prev_s, prev_e = resolve_ciro_period("gecen_ay", today)
    assert prev_s == date(2026, 8, 1)
    assert prev_e == date(2026, 8, 31)


def test_custom_range_clamped_before_april() -> None:
    today = date(2026, 9, 10)
    start, end = resolve_ciro_period(
        "ozel", today, date(2026, 1, 1), date(2026, 3, 15)
    )
    assert start == REAL_SALES_START
    assert end == REAL_SALES_START


def test_nth_workday_skips_sunday() -> None:
    # 1 Haziran 2026 Pazartesi. 7. gün Pazar; 9. iş günü 10 Haziran.
    assert nth_workday(date(2026, 6, 1), 9) == date(2026, 6, 10)


def test_add_months_and_month_first() -> None:
    assert add_months(date(2026, 9, 1), -2) == date(2026, 7, 1)
    assert month_first(date(2026, 9, 10)) == date(2026, 9, 1)


def test_team_total_row_appended_per_month() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Istanbul")
    sep = datetime(2026, 9, 1, tzinfo=tz)
    rows = [
        {
            "rep_id": "a",
            "temsilci": "A",
            "ay": sep,
            "ay_etiket": "Eylül 2026",
            "satis": 2,
            "ciro": 100.0,
            "ortalama": 50.0,
        },
        {
            "rep_id": "b",
            "temsilci": "B",
            "ay": sep,
            "ay_etiket": "Eylül 2026",
            "satis": 1,
            "ciro": 50.0,
            "ortalama": 50.0,
        },
    ]
    out = with_monthly_team_totals(rows)
    assert len(out) == 3
    total = out[-1]
    assert total["temsilci"] == "Ekip toplamı"
    assert total["satis"] == 3
    assert total["ciro"] == 150.0


def test_window_caption_contains_range() -> None:
    text = fmt_window(DateWindow(date(2026, 9, 1), date(2026, 9, 10)))
    assert "Eylül" in text
    assert "2026" in text
