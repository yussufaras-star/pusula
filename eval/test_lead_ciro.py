"""Lead ↔ ciro korelasyonu: dönemleme, Pearson r, gecikme (saf)."""

from datetime import date, timedelta

from pusula.lead_ciro import build, bucket, lags, pearson, series, strength

TODAY = date(2026, 10, 15)  # perşembe


def test_bucket_by_granularity() -> None:
    assert bucket(TODAY, "gun") == TODAY
    assert bucket(TODAY, "hafta") == date(2026, 10, 12)
    assert bucket(TODAY, "ay") == date(2026, 10, 1)
    assert bucket(TODAY, "yil") == date(2026, 1, 1)


def test_pearson_and_strength() -> None:
    assert pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == 1.0
    assert pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) == -1.0
    assert pearson([1, 2, 3], [1, 2, 3]) is None  # az nokta
    assert pearson([1, 1, 1, 1, 1], [1, 2, 3, 4, 5]) is None  # sabit seri
    assert strength(0.85) == "güçlü pozitif"
    assert strength(-0.5) == "orta negatif"
    assert strength(0.1) == "yok"


def test_series_monthly_marks_running_and_sums() -> None:
    leads = {date(2026, 9, 3): 10, date(2026, 9, 20): 5, date(2026, 10, 2): 4}
    ciro = {date(2026, 9, 10): 1000.0, date(2026, 10, 30): 500.0}  # ileri tarihli kapanış ayda sayılır
    sales = {date(2026, 9, 10): 2, date(2026, 10, 30): 1}
    rows = series(leads, ciro, sales, "ay", TODAY)
    sep, oct_ = rows[-2], rows[-1]
    assert (sep["lead"], sep["ciro"], sep["satis"], sep["suruyor"]) == (15, 1000.0, 2, False)
    assert sep["ciro_per_lead"] == round(1000 / 15, 2)
    assert (oct_["lead"], oct_["ciro"], oct_["suruyor"]) == (4, 500.0, True)
    assert rows[0]["bas"] == date(2025, 10, 1)


def test_lag_finds_delayed_relation_and_skips_running_week() -> None:
    # Lead'ler haftadan haftaya değişir, ciro bir hafta sonra aynı deseni izler.
    pattern = [5, 9, 3, 8, 2, 7, 4, 10, 6, 1]
    mondays = [date(2026, 10, 12) - timedelta(weeks=len(pattern) - i) for i in range(len(pattern))]
    leads = {d: n for d, n in zip(mondays, pattern)}
    ciro = {d + timedelta(weeks=1): 100.0 * n for d, n in zip(mondays, pattern)}
    ciro[date(2026, 10, 13)] = 999999.0  # süren hafta: hesaba girmez
    out = build(leads, ciro, {}, "hafta", TODAY)
    assert out["en_iyi"]["gecikme"] == 1 and out["en_iyi"]["r"] == 1.0
    assert out["r"] is not None and out["r"] < out["en_iyi"]["r"]
    assert all(r["n"] <= 26 for r in out["gecikmeler"])
    rows = out["seri"]
    assert rows[-1]["suruyor"] and lags(rows, "hafta")[0]["n"] == len(rows) - 1


def test_yearly_has_too_few_points() -> None:
    out = build({date(2026, 3, 1): 100}, {date(2026, 3, 2): 5000.0}, {}, "yil", TODAY)
    assert [r["etiket"] for r in out["seri"]] == ["2025", "2026"]
    assert out["r"] is None and out["en_iyi"] is None
