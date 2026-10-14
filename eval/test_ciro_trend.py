"""Ciro trendi: % değişim, hafta/ay serileri, aynı güne kadar kıyas (saf)."""

from datetime import date

from pusula.ciro_trend import build_trend, change, same_day_last_month, to_date, weekly

TODAY = date(2026, 10, 14)  # çarşamba


def test_change_is_relative_to_previous() -> None:
    assert change(120, 100) == 20.0
    assert change(80, 100) == -20.0
    assert change(50, 0) is None


def test_same_day_last_month_clamps_short_months() -> None:
    assert same_day_last_month(date(2026, 3, 31)) == date(2026, 2, 28)
    assert same_day_last_month(TODAY) == date(2026, 9, 14)


def test_to_date_compares_equal_spans() -> None:
    sales = [
        ("a", date(2026, 10, 12), 2, 100.0),  # bu hafta pazartesi
        ("a", date(2026, 10, 14), 1, 50.0),   # bugün
        ("a", date(2026, 10, 6), 1, 100.0),   # geçen hafta salı (aynı güne kadar)
        ("a", date(2026, 10, 8), 5, 999.0),   # geçen hafta perşembe: kıyasa girmez
        ("a", date(2026, 9, 3), 1, 300.0),    # geçen ay 1-14 arası
        ("a", date(2026, 9, 20), 1, 999.0),   # geçen ay 14'ünden sonra: girmez
    ]
    out = to_date(sales, TODAY)
    assert out["hafta"]["ciro"] == 150.0 and out["hafta"]["onceki_ciro"] == 1099.0 - 999.0
    assert out["hafta"]["degisim"] == 50.0
    # Ay: 12, 14, 6, 8 Ekim = 1249; Eylül 1-14 = 300.
    assert out["ay"]["ciro"] == 1249.0 and out["ay"]["onceki_ciro"] == 300.0


def test_weekly_marks_running_week_and_change() -> None:
    sales = [("a", date(2026, 10, 5), 1, 100.0), ("a", date(2026, 10, 13), 1, 150.0)]
    rows = weekly(sales, TODAY, weeks=2)
    assert [r["bas"] for r in rows] == [date(2026, 10, 5), date(2026, 10, 12)]
    assert rows[-1]["suruyor"] and rows[-1]["degisim"] == 50.0


def test_build_trend_months_include_future_dated_closings() -> None:
    sales = [("a", date(2026, 9, 10), 1, 200.0), ("a", date(2026, 10, 30), 1, 300.0)]
    out = build_trend(sales, ["a", "b"], TODAY)
    last = out["aylar"][-1]
    assert last["ciro"] == 300.0 and last["degisim"] == 50.0 and last["suruyor"]
    assert out["temsilci"]["b"]["ay"]["ciro"] == 0
