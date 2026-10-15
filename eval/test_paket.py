"""Paket bazlı müşteri analizi (saf)."""

from datetime import date

from pusula.paket import group_of, summarize

TODAY = date(2026, 10, 15)


def test_group_of() -> None:
    assert group_of("premium", None) == "premium" and group_of("Elite", None) == "elite"
    assert group_of("Free", date(2026, 3, 1)) == "free"
    assert group_of(None, date(2026, 9, 1)) == "bosaltilmis"
    assert group_of("  ", None) == "bos"
    assert group_of("Gold", None) == "diger"


def test_summarize_groups_and_exits() -> None:
    rows = [
        {"paket": "Premium", "owner": "Nihat Kemer", "son1": 3, "bit": date(2026, 11, 1), "abonelik_satis": True, "reco": True},
        {"paket": "premium", "owner": "Ayşe", "son1": 0, "bit": date(2026, 10, 1)},
        {"paket": "free", "owner": "Burcu Özkan", "son1": 1, "bit": None, "free_since": date(2026, 10, 1)},
        {"paket": None, "owner": None, "free_since": date(2026, 3, 30)},
        {"paket": None, "owner": None},
    ]
    out = summarize(rows, TODAY)
    assert (out["toplam"], out["aktif"], out["pasif"], out["bos"]) == (5, 2, 2, 1)
    premium = out["gruplar"][0]
    assert premium["ad"] == "Premium" and premium["musteri"] == 2 and premium["pay"] == 40.0
    assert premium["sahip"] == {"Nihat Kemer": 1, "Burcu Özkan": 0, "Diğer": 1}
    assert (premium["satiyor"], premium["bitis_gecmis"], premium["bitis_yakin"], premium["reco"]) == (1, 1, 1, 1)
    free = next(g for g in out["gruplar"] if g["key"] == "free")
    assert free["cikis_30g"] == 1 and free["cikis_bu_yil"] == 1
    assert [(m["ay"], m["toplam"]) for m in out["cikislar"]] == [(date(2026, 3, 1), 1), (date(2026, 10, 1), 1)]
    assert out["cikislar"][0]["bosaltilmis"] == 1
