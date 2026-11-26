"""Kamp → Premium: durum, süreçte bekleme, oran ve almama nedeni (saf)."""

from datetime import date

from pusula.kamp_premium import UNKNOWN, build, reason

TODAY = date(2026, 11, 26)


def person(owner: str, ad: str, kamp: date, prem: date | None = None, **kw) -> dict:
    return {"owner": owner, "ad": ad, "kamp_gun": kamp, "premium_gun": prem, **kw}


def test_rate_counts_only_decided_and_rep_order() -> None:
    rows = [
        person("1", "Dilçe", date(2026, 9, 23), date(2026, 10, 11)),
        person("1", "Engin", date(2026, 9, 25), date(2026, 10, 13)),
        person("2", "Ayşegül", date(2026, 9, 30), arama_sonra=8, son_arama=date(2026, 11, 25), statu="Satış Fırsatı"),
        person("2", "Recep", date(2026, 9, 30), date(2026, 10, 6)),
        person("2", "Eda", date(2026, 11, 5)),
        person("x", "Naci", date(2026, 10, 2)),
    ]
    out = build(rows, {"1": "Miray", "2": "Abdullah"}, TODAY)
    assert [r["name"] for r in out["temsilciler"]] == ["Abdullah", "Miray", UNKNOWN]
    assert out["toplam"] == {"giden": 6, "aldi": 3, "almadi": 2, "surecte": 1}
    assert out["oran"] == 0.6
    assert out["ort_gun"] == round((18 + 18 + 6) / 3)
    abdullah = out["temsilciler"][0]
    assert [k["durum"] for k in abdullah["kisiler"]] == ["aldi", "almadi", "surecte"]
    assert abdullah["kisiler"][1]["neden"] == "Kamptan sonra 8 kez arandı (son 25.11) · CRM: Satış Fırsatı"


def test_reason_prefers_objection_and_flags_no_call() -> None:
    assert reason({"arama_sonra": 0}) == "Kamptan sonra Premium için aranmadı"
    text = reason({"arama_sonra": 2, "statu": "Düşünmek İstiyor",
                   "itirazlar": [{"tip": "fiyat", "alinti": "bu rakamı gözden çıkarsam mı"}]})
    assert text == "İtiraz: fiyat — “bu rakamı gözden çıkarsam mı” · CRM: Düşünmek İstiyor"


def test_empty() -> None:
    out = build([], {}, TODAY)
    assert out["oran"] is None and out["temsilciler"] == []


def test_after_sales_owner_named() -> None:
    out = build([person("6847331000000615001", "Naci", date(2026, 10, 2))], {}, TODAY)
    assert out["temsilciler"][0]["name"] == "Nihat Kemer"
