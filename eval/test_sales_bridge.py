"""Ciro köprüsü: hizalama, ayrıştırma kimliği ve başlık (saf)."""

import math
from datetime import date

from pusula.sales_bridge import aligned_ranges, build_bridge, decompose, factors, period_label


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


def test_movers_list_everyone_biggest_drop_first() -> None:
    a_reps = [{"rep_id": "1", "name": "Ali", "ciro": 100.0}, {"rep_id": "2", "name": "Ayşe", "ciro": 50.0},
              {"rep_id": "3", "name": "Can", "ciro": 10.0}]
    b_reps = [{"rep_id": "1", "ciro": 90.0}, {"rep_id": "2", "ciro": 200.0}, {"rep_id": "3", "ciro": 10.0}]
    out = build_bridge((date(2026, 11, 1),) * 2, (date(2026, 10, 1),) * 2, row(1, 1, 1, 1, 1.0),
                       row(1, 1, 1, 1, 1.0), a_reps=a_reps, b_reps=b_reps)
    assert [m["name"] for m in out["movers"]] == ["Ayşe", "Can", "Ali"]


def test_package_mix_sorted_by_ciro_change() -> None:
    from pusula.sales_bridge import mix_rows

    out = mix_rows({"Premium": (29, 1343822.0), "Kamp": (22, 134650.0)},
                   {"Premium": (48, 2262933.0), "Kamp": (5, 34825.0), "Plus": (2, 61283.0)})
    assert [r["paket"] for r in out] == ["Premium", "Kamp", "Plus"]
    assert out[2]["adet_a"] == 0 and out[2]["ort_a"] is None
    assert out[1]["fark"] == 99825.0


def kasim_ekim() -> dict:
    """Gerçek 1-22 Kasım ↔ 1-22 Ekim rakamlarına yakın senaryo."""
    a_row = {**row(18, 2389, 1360, 52, 1507047.0), "sure_ort": 224.0, "katilim_orani": 34.3}
    b_row = {**row(19, 2164, 1380, 57, 2461921.0), "sure_ort": 294.7, "katilim_orani": 46.3}
    mix = [{"paket": "Rexven-Premium", "adet_a": 29, "adet_b": 48, "fark": -919111.0},
           {"paket": "Kamp", "adet_a": 22, "adet_b": 5, "fark": 99825.0}]
    a_reps = [{"rep_id": "1", "name": "Abdullah", "ciro": 180643.0, "satis": 8},
              {"rep_id": "2", "name": "Serkan", "ciro": 468186.0, "satis": 14}]
    b_reps = [{"rep_id": "1", "ciro": 787087.0, "satis": 17}, {"rep_id": "2", "ciro": 292472.0, "satis": 8}]
    return build_bridge((date(2026, 11, 1), date(2026, 11, 22)), (date(2026, 10, 1), date(2026, 10, 22)),
                        a_row, b_row, a_reps=a_reps, b_reps=b_reps, mix=mix)


def test_period_labels() -> None:
    assert period_label(date(2026, 11, 1), date(2026, 11, 22)) == "1–22 Kasım"
    assert period_label(date(2026, 10, 27), date(2026, 11, 2)) == "27 Ekim – 2 Kasım"
    assert period_label(date(2026, 11, 23), date(2026, 11, 23)) == "23 Kasım"


def test_findings_lead_with_biggest_drop_and_name_package() -> None:
    out = kasim_ekim()
    assert out["ozet"] == "Ciro %39 düştü"
    assert out["a"]["label"] == "1–22 Kasım" and out["b"]["label"] == "1–22 Ekim"
    first = out["tespitler"][0]
    assert first["ton"] == "neg" and first["metin"].startswith("Satış başına ortalama tutar 43 B TL → 29 B TL")
    assert "Rexven-Premium (48 → 29 adet)" in first["metin"]
    texts = [t["metin"] for t in out["tespitler"]]
    assert any(t.startswith("Görüşmeler kısaldı: ortalama 4 dk 55 sn → 3 dk 44 sn") for t in texts)
    assert any(t.startswith("Toplantıya katılım düştü: %46 → %34") for t in texts)
    assert any(t.startswith("En çok düşen temsilci Abdullah") for t in texts)
    # Lehe etken (günlük lead) de görünür, yeşil.
    assert any(t["ton"] == "pos" and t["metin"].startswith("Günde aranan lead") for t in out["tespitler"])


def test_advice_follows_negative_drivers_and_keeps_people() -> None:
    out = kasim_ekim()
    tips = out["oneriler"]
    assert len(tips) == 5
    assert tips[0].startswith("Premium sunumunu")
    assert tips[-2].startswith("Abdullah ile bu hafta birebir")
    assert tips[-1].startswith("Serkan adlı temsilcinin")
    # Lehe etken için öneri çıkmaz.
    assert not any(t.startswith("Arama hacmini") for t in tips)


def test_no_advice_when_everything_improves() -> None:
    out = build_bridge((date(2026, 11, 1),) * 2, (date(2026, 10, 1),) * 2, row(1, 10, 6, 2, 100.0),
                       row(1, 10, 5, 1, 50.0))
    assert out["ozet"] == "Ciro %100 arttı"
    assert out["oneriler"] == []
    assert all(t["ton"] == "pos" for t in out["tespitler"])


def test_quality_rows_rate_change_in_points() -> None:
    from pusula.sales_bridge import quality_rows

    out = {r["key"]: r for r in quality_rows({"sure_ort": 224.0, "katilim_orani": 34.3, "randevu": 199},
                                             {"sure_ort": 294.7, "katilim_orani": 46.3, "randevu": 240})}
    assert round(out["sure_ort"]["degisim"], 2) == -0.24
    assert round(out["katilim_orani"]["degisim"], 1) == -12.0
    assert out["call_sec"]["degisim"] is None
