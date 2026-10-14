"""Churn: vade olayları ve aylık oran (saf)."""

from datetime import date

from pusula.churn import due_events, monthly_churn, period_churn, plus_months

TODAY = date(2026, 10, 13)


def test_plus_months_clamps_month_end() -> None:
    assert plus_months(date(2025, 10, 31), 12) == date(2026, 10, 31)
    assert plus_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_early_renewal_extends_from_sale_date() -> None:
    ev = due_events([date(2025, 10, 5), date(2026, 9, 20)], TODAY)
    assert ev == [
        {"vade": date(2026, 10, 5), "sonuc": "yeniledi", "geri_kazanildi": False},
        {"vade": date(2027, 9, 20), "sonuc": "bekliyor", "geri_kazanildi": False},
    ]


def test_no_grace_lost_then_win_back() -> None:
    ev = due_events([date(2025, 9, 1), date(2026, 9, 10)], TODAY)
    assert ev[0] == {"vade": date(2026, 9, 1), "sonuc": "kaybedildi", "geri_kazanildi": True}
    assert ev[1]["sonuc"] == "bekliyor"
    assert due_events([date(2025, 8, 1)], TODAY) == [
        {"vade": date(2026, 8, 1), "sonuc": "kaybedildi", "geri_kazanildi": False}
    ]


def test_extra_purchase_early_in_term_is_not_renewal() -> None:
    # Aynı ay ikinci paket: yenileme değil; vade ilk satıştan 12 ay.
    ev = due_events([date(2025, 9, 1), date(2025, 9, 15)], TODAY)
    assert ev == [{"vade": date(2026, 9, 1), "sonuc": "kaybedildi", "geri_kazanildi": False}]


def test_monthly_rate_uses_decided_only() -> None:
    events = [
        {"vade": date(2026, 9, 3), "sonuc": "yeniledi"},
        {"vade": date(2026, 9, 9), "sonuc": "kaybedildi", "geri_kazanildi": True},
        {"vade": date(2026, 9, 20), "sonuc": "yeniledi"},
        {"vade": date(2026, 10, 2), "sonuc": "kaybedildi"},
        {"vade": date(2026, 10, 25), "sonuc": "bekliyor"},
        {"vade": date(2026, 11, 20), "sonuc": "yeniledi"},
    ]
    rows = {r["ay"]: r for r in monthly_churn(events, TODAY)}
    sep, oct_ = rows[date(2026, 9, 1)], rows[date(2026, 10, 1)]
    assert (sep["vadesi_gelen"], sep["kaybedildi"], sep["churn"], sep["geri_kazanildi"]) == (3, 1, 33.3, 1)
    assert (oct_["vadesi_gelen"], oct_["bekliyor"], oct_["churn"]) == (2, 1, 100.0)
    assert date(2027, 1, 1) in rows and date(2027, 2, 1) not in rows
    # Gelecek ay: erken yenileme sayılır ama oran verilmez.
    assert rows[date(2026, 11, 1)]["yeniledi"] == 1 and rows[date(2026, 11, 1)]["churn"] is None
    assert period_churn([sep, oct_])["churn"] == 50.0


def test_action_lists_split_upcoming_and_lost() -> None:
    from pusula.churn import action_lists

    today = date(2026, 10, 14)
    contacts = {
        # Vadesi 20 Kasım: yaklaşan (37 gün).
        "a": {"name": "Ali", "owner": "Nihat Kemer", "sales": [(date(2025, 11, 20), "Rexven-Premium")]},
        # Vadesi 1 Ekim geçti, yenileme yok: kaybedilen (13 gün).
        "b": {"name": "Banu", "owner": None, "sales": [(date(2025, 10, 1), "Rexven-Plus")]},
        # Vadesi 2027'de: listede yok.
        "c": {"name": "Can", "owner": None, "sales": [(date(2026, 9, 1), "Re-Co-Premium")]},
        # Vadeden önce yeniledi, yeni vade uzak: listede yok.
        "d": {"name": "Deniz", "owner": None, "sales": [(date(2025, 10, 5), "Rexven-Premium"), (date(2026, 9, 30), "Re-Co-Premium")]},
    }
    out = action_lists(contacts, today)
    assert [(r["musteri"], r["kalan_gun"]) for r in out["yaklasan"]] == [("Ali", 37)]
    assert [(r["musteri"], r["gecen_gun"]) for r in out["kaybedilen"]] == [("Banu", 13)]
    assert out["yaklasan"][0]["sahip"] == "Nihat Kemer" and out["kaybedilen"][0]["paket"] == "Rexven-Plus"


def test_pre_crm_customers_from_subscription_dates() -> None:
    from pusula.churn import action_lists, monthly_churn, pre_crm_start

    today = date(2026, 10, 14)
    # Re-Co satışı Mart 2026: önceki dönemin vadesi o gün → Mart'a "yeniledi".
    reco = [(date(2026, 3, 10), "Re-Co-Premium")]
    start = pre_crm_start(reco, date(2025, 3, 10), date(2027, 3, 10))
    assert start == date(2025, 3, 10)
    events = due_events([start] + [d for d, _ in reco], today)
    assert events[0] == {"vade": date(2026, 3, 10), "sonuc": "yeniledi", "geri_kazanildi": False}
    # CRM'de satışı yok, aboneliği Mayıs 2026'da bitmiş → Mayıs'ta kaybedildi.
    gone = pre_crm_start([], date(2025, 5, 2), date(2026, 5, 2))
    assert due_events([gone], today) == [{"vade": date(2026, 5, 2), "sonuc": "kaybedildi", "geri_kazanildi": False}]
    # CRM'de yeni müşteri (Premium, Kasım 2025): CRM öncesi değil.
    assert pre_crm_start([(date(2025, 11, 20), "Rexven-Premium")], date(2025, 11, 20), date(2026, 11, 20)) is None
    # Tablo Ocak 2026'dan başlar.
    rows = monthly_churn(events + due_events([gone], today), today)
    assert rows[0]["ay"] == date(2026, 1, 1)
    by = {r["ay"]: r for r in rows}
    assert by[date(2026, 3, 1)]["yeniledi"] == 1 and by[date(2026, 5, 1)]["kaybedildi"] == 1
    # Kayıp listesinde CRM öncesi müşteri, paket müşteri kaydından.
    out = action_lists({"x": {"name": "Eski", "owner": None, "sales": [], "pre": date(2025, 8, 1),
                              "paket": "Premium", "bas": date(2025, 8, 1)}}, today)
    assert out["kaybedilen"][0]["paket"] == "Premium" and out["kaybedilen"][0]["crm_oncesi"]
