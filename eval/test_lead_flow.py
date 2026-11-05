"""Lead akışı: haftalık gelen lead, temsilci başı günlük lead, % değişim (saf)."""

from datetime import date, timedelta

from pusula.lead_flow import build, daily_per_rep, four_weeks, to_date, week_rows

TODAY = date(2026, 11, 5)  # perşembe
THIS = date(2026, 11, 2)
LAST = date(2026, 10, 26)


def days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_daily_per_rep_divides_by_reps_and_calendar_days() -> None:
    assigned = [("a", d, 2) for d in days(LAST, 7)] + [("b", d, 4) for d in days(LAST, 7)]
    assert daily_per_rep(assigned, LAST, LAST + timedelta(days=6)) == 3.0
    assert daily_per_rep(assigned, THIS, THIS) is None  # kimse lead almadı


def test_week_rows_change_and_running_week_excludes_today() -> None:
    inflow = {d: 10 for d in days(LAST - timedelta(weeks=1), 7)}
    inflow.update({d: 12 for d in days(LAST, 7)})
    inflow.update({d: 50 for d in days(THIS, 4)})
    assigned = [("a", d, 7) for d in days(LAST - timedelta(weeks=1), 7)]
    assigned += [("a", d, 14) for d in days(LAST, 7)]
    assigned += [("a", d, 1) for d in days(THIS, 3)] + [("a", TODAY, 100)]  # bugün yarım
    rows = week_rows(inflow, assigned, 2, TODAY)
    assert [r["bas"] for r in rows] == [LAST - timedelta(weeks=1), LAST, THIS]
    last = rows[1]
    assert (last["gelen"], last["gunluk_gelen"], last["kisi_gunluk"]) == (84, 12.0, 14.0)
    assert (last["degisim_gelen"], last["degisim_kisi_gunluk"]) == (20.0, 100.0)
    running = rows[2]
    assert running["suruyor"] and running["degisim_gelen"] is None
    assert running["gelen"] == 200  # gelen bugünü de sayar
    assert running["kisi_gunluk"] == 1.0  # günlük ortalama düne kadar


def test_to_date_compares_completed_days_only() -> None:
    inflow = {d: 10 for d in days(LAST, 7)} | {d: 8 for d in days(THIS, 4)}
    assigned = [("a", d, 10) for d in days(LAST, 3)] + [("a", d, 9) for d in days(THIS, 3)]
    t = to_date(inflow, assigned, TODAY)
    assert t is not None
    assert (t["gun"], t["onceki_bit"]) == (3, date(2026, 10, 28))
    assert (t["gelen"], t["onceki_gelen"], t["degisim_gelen"]) == (24, 30, -20.0)
    assert (t["kisi_gunluk"], t["onceki_kisi_gunluk"], t["degisim_kisi_gunluk"]) == (9.0, 10.0, -10.0)
    assert to_date(inflow, assigned, THIS) is None  # pazartesi: tamamlanmış gün yok


def test_four_weeks_needs_eight_completed_weeks() -> None:
    done = [{"gelen": 100, "kisi_gunluk": 10.0}] * 4 + [{"gelen": 80, "kisi_gunluk": 8.0}] * 4
    done = [dict(r, bas=LAST - timedelta(weeks=7 - i)) for i, r in enumerate(done)]
    out = four_weeks(done)
    assert out is not None
    assert (out["gelen"], out["onceki_gelen"], out["degisim_gelen"]) == (80.0, 100.0, -20.0)
    assert out["degisim_kisi_gunluk"] == -20.0
    assert four_weeks(done[1:]) is None


def test_rep_rows_daily_series_and_missing_weeks() -> None:
    assigned = [("a", d, 14) for d in days(LAST, 7)] + [("b", d, 7) for d in days(LAST, 7)]
    assigned += [("a", d, 7) for d in days(LAST - timedelta(weeks=1), 7)]  # b o hafta yok
    out = build({}, assigned, {"a": "Ada", "b": "Bora"}, 2, TODAY)
    reps = {r["rep_id"]: r for r in out["temsilciler"]}
    assert reps["a"]["seri"] == [7.0, 14.0, None]
    assert (reps["a"]["son"], reps["a"]["onceki"], reps["a"]["degisim"]) == (14.0, 7.0, 100.0)
    assert reps["b"]["seri"][0] is None and reps["b"]["degisim"] is None
    assert reps["b"]["name"] == "Bora"
    assert out["son_hafta"]["bas"] == LAST
    assert out["son_hafta"]["kisi_gunluk"] == 10.5
