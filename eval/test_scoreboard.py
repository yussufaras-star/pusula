"""Haftalık puan tablosu: gelişim ve puan sıralaması, yetersiz veri, liderler (saf)."""

from datetime import date, timedelta

from pusula.scoreboard import MIN_CALLS, build, pool

WEEK = date(2026, 10, 26)
PREV = WEEK - timedelta(weeks=1)


def calls(rep: str, week: date, score: float, n: int = MIN_CALLS, **dims: float) -> list:
    base = {k: score for k in ("K1", "K2", "K3", "K4", "K5")}
    return [(rep, week, {**base, **dims})] * n


def test_pool_averages_calls_per_dimension() -> None:
    out = pool([{"K1": 2.0, "K2": 4.0}, {"K1": 4.0}])
    assert out["n"] == 2
    assert out["puanlar"]["K1"] == 3.0 and out["puanlar"]["K2"] == 4.0 and out["puanlar"]["K3"] is None
    assert out["ort"] == 3.5


def test_growth_ranking_lets_low_scorer_win() -> None:
    data = calls("a", PREV, 3.0) + calls("a", WEEK, 3.1)  # yüksek puan, az gelişim
    data += calls("b", PREV, 2.0) + calls("b", WEEK, 2.5)  # düşük puan, çok gelişim
    out = build(data, {"a": "Ada", "b": "Bora"}, WEEK)
    rows = {r["rep_id"]: r for r in out["temsilciler"]}
    assert rows["b"]["gelisim_ort"] == 0.5 and rows["b"]["sira_gelisim"] == 1
    assert rows["a"]["sira_puan"] == 1 and rows["a"]["sira_gelisim"] == 2
    assert out["en_cok_gelisen"] == ["Bora"]


def test_too_few_calls_is_not_ranked() -> None:
    data = calls("a", PREV, 2.0) + calls("a", WEEK, 4.0, n=MIN_CALLS - 1)
    data += calls("b", WEEK, 3.0)  # kıyas dönemi yok
    out = build(data, {"a": "Ada", "b": "Bora"}, WEEK)
    rows = {r["rep_id"]: r for r in out["temsilciler"]}
    assert not rows["a"]["yeterli"] and rows["a"]["sira_puan"] is None and rows["a"]["gelisim_ort"] is None
    assert rows["b"]["yeterli"] and not rows["b"]["kiyas_var"]
    assert rows["b"]["sira_puan"] == 1 and rows["b"]["sira_gelisim"] is None
    assert out["liderler"]["K1"]["puan"] == ["Bora"]


def test_dimension_leaders_focus_and_ties() -> None:
    data = calls("a", PREV, 2.0) + calls("a", WEEK, 2.0, K3=4.0, K4=1.0)
    data += calls("b", PREV, 2.0) + calls("b", WEEK, 2.0, K3=4.02)
    out = build(data, {"a": "Ada", "b": "Bora"}, WEEK)
    assert sorted(out["liderler"]["K3"]["puan"]) == ["Ada", "Bora"]  # 0,05 içinde eşit
    assert out["liderler"]["K1"]["gelisim"] == []  # gelişim yoksa lider yok
    a = next(r for r in out["temsilciler"] if r["rep_id"] == "a")
    assert (a["en_iyi_gelisim"], a["odak"]) == ("K3", "K4")


def test_baseline_uses_only_previous_four_weeks() -> None:
    old = WEEK - timedelta(weeks=5)
    data = calls("a", old, 5.0) + calls("a", PREV, 2.0) + calls("a", WEEK, 3.0)
    out = build(data, {"a": "Ada"}, WEEK)
    a = out["temsilciler"][0]
    assert a["onceki_ort"] == 2.0 and a["gelisim_ort"] == 1.0
    assert out["ekip"]["gelisim_ort"] == 1.0


def test_month_period_compares_with_previous_month() -> None:
    from pusula.scoreboard import PERIOD_MIN

    n = PERIOD_MIN["ay"]
    data = calls("a", date(2026, 9, 30), 5.0, n=n)  # iki ay önce: kıyasa girmez
    data += calls("a", date(2026, 10, 3), 2.0, n=n) + calls("a", date(2026, 11, 20), 3.0, n=n)
    data += calls("b", date(2026, 11, 2), 4.0, n=n - 1)  # ayda yetersiz
    out = build(data, {"a": "Ada", "b": "Bora"}, date(2026, 11, 16), "ay")
    rows = {r["rep_id"]: r for r in out["temsilciler"]}
    assert (out["bas"], out["son"], out["etiket"]) == (date(2026, 11, 1), date(2026, 11, 30), "Kasım 2026")
    assert rows["a"]["onceki_ort"] == 2.0 and rows["a"]["gelisim_ort"] == 1.0
    assert not rows["b"]["yeterli"] and out["liderler"]["K1"]["puan"] == ["Ada"]


def test_quarter_bounds_and_labels() -> None:
    from pusula.scoreboard import period_bounds

    b = period_bounds("ceyrek", date(2026, 1, 12))
    assert (b["bas"], b["bit"], b["kiyas_bas"]) == (date(2026, 1, 1), date(2026, 4, 1), date(2025, 10, 1))
    assert (b["etiket"], b["kiyas_etiket"]) == ("Q1 2026", "önceki çeyrek (Q4 2025)")
    assert period_bounds("hafta", date(2026, 11, 18))["etiket"] == "16 Kasım – 22 Kasım"
