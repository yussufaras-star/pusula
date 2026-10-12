"""Raporlar: huni oranları ve ekip ortalaması (saf)."""

from pusula.metrics import combine, finish
from pusula.sales_report import derive, team_rows


def rep(arama: int, payda: int, pay: int, randevu: int, katildi: int, satis: int, ciro: float) -> dict:
    row = finish(
        combine([{"arama": arama, "lead_payda": payda, "lead_pay": pay, "randevu": randevu,
                  "katildi": katildi, "katilmadi": katildi}])
    )
    row.update({"satis": satis, "ciro": ciro, "atanan": 10})
    return derive(row, 10)


def test_funnel_rates_sale_against_reached_not_attended() -> None:
    r = rep(400, 200, 140, 28, 7, 14, 420000.0)
    assert r["ulasma_orani"] == 70.0
    assert r["toplanti_donus"] == 20.0
    assert r["katilim_orani"] == 50.0
    # Satış katılandan fazla olabilir (telefonda kapanış); oran ulaşılana göre.
    assert r["satis_orani"] == 10.0
    assert r["ort_satis"] == 30000.0
    assert r["gunluk_arama"] == 40.0


def test_team_average_ignores_inactive_and_keeps_rates() -> None:
    a = rep(400, 200, 140, 28, 7, 14, 420000.0)
    b = rep(200, 100, 50, 10, 3, 2, 60000.0)  # tek sayı: ortalamada küsurat
    idle = rep(0, 0, 0, 0, 0, 0, 0.0)
    total, avg = team_rows([a, b, idle], 10)
    assert total["satis"] == 16 and total["ciro"] == 480000.0
    assert total["n_reps"] == 2
    assert avg["ciro"] == 240000.0
    assert avg["lead_payda"] == 150.0
    # Ortalama oranı toplam oranıyla aynı (sayımlar birlikte bölünür).
    assert avg["ulasma_orani"] == total["ulasma_orani"] == round(100 * 190 / 300, 1)
    assert avg["satis_orani"] == total["satis_orani"]
    assert avg["katilim_orani"] == total["katilim_orani"] == 50.0
