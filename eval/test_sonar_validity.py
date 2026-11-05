"""SONAR-41 tutarlılık analizi: satışlı / satışsız kıyas, dilimler, kontroller (saf)."""

from datetime import date

from pusula.sonar_validity import Call, build

SINCE = date(2026, 10, 12)


def call(score: float, sold_after: int | None = None, interest: int | None = 3, **dims: float) -> Call:
    return Call({**{k: score for k in ("K1", "K2", "K3", "K4", "K5")}, **dims}, sold_after, interest)


def test_compares_sold_and_unsold_by_dimension() -> None:
    calls = [call(3.0, 0, K4=4.0), call(3.0, 5, K4=4.0)] + [call(2.0) for _ in range(4)]
    out = build(calls, SINCE)
    assert (out["satisli"]["n"], out["satissiz"]["n"]) == (2, 4)
    assert out["satisli"]["puanlar"]["K4"] == 4.0
    assert out["boyut_fark"]["K4"] == 2.0 and out["boyut_fark"]["K1"] == 1.0
    assert out["en_ayirici"][0] == "K4"
    assert out["fark"] == round(3.2 - 2.0, 2)
    assert out["guven"] == "ön sonuç"


def test_bands_give_sale_rate_per_score_range() -> None:
    calls = [call(1.5) for _ in range(9)] + [call(1.8, 0)] + [call(3.4, 1), call(3.6)]
    bands = {b["etiket"]: b for b in build(calls, SINCE)["dilimler"]}
    assert (bands["2'nin altı"]["n"], bands["2'nin altı"]["oran"]) == (10, 10.0)
    assert (bands["3 ve üstü"]["satis"], bands["3 ve üstü"]["oran"]) == (1, 50.0)
    assert bands["2 – 2,5"]["oran"] is None


def test_controls_interest_and_pre_sale_calls() -> None:
    calls = [call(3.0, 0, interest=5), call(2.8, 7, interest=4), call(2.5, interest=5), call(2.0, interest=2)]
    k = build(calls, SINCE)["kontrol"]
    assert (k["ilgili_satisli"]["n"], k["ilgili_satissiz"]["n"]) == (2, 1)
    assert k["ilgili_satissiz"]["ort"] == 2.5
    assert (k["satis_oncesi"]["n"], k["satis_oncesi"]["ort"]) == (1, 2.8)  # aynı gün satış hariç
