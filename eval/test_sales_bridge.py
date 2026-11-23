"""Ciro köprüsü: hizalama, ayrıştırma kimliği ve başlık (saf)."""

import math
from datetime import date

from pusula.sales_bridge import aligned_ranges, build_bridge, decompose, factors, headline


def row(days: int, payda: int, pay: int, satis: int, ciro: float) -> dict:
    return {"work_days": days, "lead_payda": payda, "lead_pay": pay, "satis": satis, "ciro": ciro}


def test_running_month_ends_yesterday_and_past_month_cut_to_same_days() -> None:
    today = date(2026, 11, 23)
    a, b = aligned_ranges((date(2026, 11, 1), today), (date(2026, 10, 1), date(2026, 10, 31)), today)
    assert a == (date(2026, 11, 1), date(2026, 11, 22))
    assert b == (date(2026, 10, 1), date(2026, 10, 22))


def test_no_align_keeps_full_past_month_and_single_today_stays() -> None:
    today = date(2026, 11, 23)
    _, b = aligned_ranges((date(2026, 11, 1), today), (date(2026, 10, 1), date(2026, 10, 31)), today, align=False)
    assert b == (date(2026, 10, 1), date(2026, 10, 31))
    a, _ = aligned_ranges((today, today), (date(2026, 11, 16), date(2026, 11, 16)), today)
    assert a == (today, today)


def test_factors_multiply_to_ciro() -> None:
    f = factors(row(20, 1000, 600, 30, 900000.0))
    assert math.isclose(math.prod(f.values()), 900000.0)


def test_effects_sum_to_delta() -> None:
    fa = factors(row(18, 800, 480, 20, 700000.0))
    fb = factors(row(20, 1000, 600, 30, 900000.0))
    effects = decompose(fa, fb)
    assert effects is not None
    assert math.isclose(sum(effects.values()), -200000.0)
    # Ulaşma oranı aynı (%60): etkisi sıfır.
    assert math.isclose(effects["ulasma"], 0, abs_tol=1e-6)
    assert effects["donusum"] < 0 and effects["ort_satis"] > 0


def test_zero_side_is_not_decomposed() -> None:
    out = build_bridge(
        (date(2026, 11, 1), date(2026, 11, 22)),
        (date(2026, 10, 1), date(2026, 10, 22)),
        row(18, 100, 50, 0, 0.0),
        row(19, 120, 60, 3, 90000.0),
    )
    assert out["ayristi"] is False
    assert out["fark"] == -90000.0
    assert all(f["etki"] is None for f in out["factors"])


def test_movers_sorted_by_absolute_change() -> None:
    a_reps = [{"rep_id": "1", "name": "Ali", "ciro": 100.0}, {"rep_id": "2", "name": "Ayşe", "ciro": 50.0},
              {"rep_id": "3", "name": "Can", "ciro": 10.0}]
    b_reps = [{"rep_id": "1", "ciro": 90.0}, {"rep_id": "2", "ciro": 200.0}, {"rep_id": "3", "ciro": 10.0}]
    out = build_bridge((date(2026, 11, 1),) * 2, (date(2026, 10, 1),) * 2, row(1, 1, 1, 1, 1.0),
                       row(1, 1, 1, 1, 1.0), a_reps=a_reps, b_reps=b_reps)
    assert [m["name"] for m in out["movers"]] == ["Ayşe", "Ali"]


def test_headline_names_biggest_driver_and_counter_effect() -> None:
    text = headline(-400000.0, 900000.0, {"is_gunu": -50000.0, "gunluk_lead": -250000.0, "ulasma": 0.0,
                                         "donusum": -250000.0, "ort_satis": 150000.0})
    assert text.startswith("A dönemi B'ye göre %44 düşük")
    assert "günlük aranan lead" in text
    assert "Ortalama satış ters yönde +" in text


def test_package_mix_sorted_by_ciro_change() -> None:
    from pusula.sales_bridge import mix_rows

    out = mix_rows({"Premium": (29, 1343822.0), "Kamp": (22, 134650.0)},
                   {"Premium": (48, 2262933.0), "Kamp": (5, 34825.0), "Plus": (2, 61283.0)})
    assert [r["paket"] for r in out] == ["Premium", "Kamp", "Plus"]
    assert out[2]["adet_a"] == 0 and out[2]["ort_a"] is None
    assert out[1]["fark"] == 99825.0


def test_headline_mentions_biggest_package_shift() -> None:
    mix = [{"paket": "Rexven-Premium", "adet_a": 29, "adet_b": 48, "fark": -919111.0}]
    out = build_bridge((date(2026, 11, 1),) * 2, (date(2026, 10, 1),) * 2, row(1, 1, 1, 1, 1.0),
                       row(1, 1, 1, 1, 2.0), mix=mix)
    assert out["headline"].endswith("Paketlerde en büyük kayma Rexven-Premium: 48 → 29 adet.")
