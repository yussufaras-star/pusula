"""Kaynak verimi oranları (saf)."""

from pusula.sales_report import yield_row


def test_yield_rates_use_lead_and_worked_denominators() -> None:
    r = yield_row("Meta", {"lead": 100, "dokunulmamis": 20, "denendi": 30, "ulasildi": 50,
                           "musteri": 6, "satan_lead": 5, "satis": 6, "ciro": 150000.0})
    assert r["islenme"] == 80.0        # dokunulan / atanan
    assert r["ulasma"] == 62.5         # ulaşılan / dokunulan
    assert r["satis_orani"] == 5.0     # satış yapılan lead / atanan
    assert r["lead_basi_ciro"] == 1500.0


def test_empty_source_has_no_rates() -> None:
    r = yield_row(None, {})
    assert r["lead"] == 0 and r["islenme"] is None and r["lead_basi_ciro"] is None
