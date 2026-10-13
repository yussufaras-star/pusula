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
