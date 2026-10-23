"""Aylık prim hedefi: kademe, tahmin, sonraki kademe (saf)."""

from datetime import date, datetime

from pusula.commission import elapsed_workdays, next_tier, rate_for, rep_progress
from pusula.ingest.deal_packages import row_from_record
from pusula.metrics import TZ


def test_tiers_follow_manager_rule() -> None:
    # 25 altı %2,5 · 25-29 %3 · 30+ %5
    assert [rate_for(n) for n in (0, 24, 25, 28, 29, 30, 41)] == [2.5, 2.5, 3.0, 3.0, 3.0, 5.0, 5.0]
    # Sonraki adım her zaman %5 kademesi.
    assert next_tier(20) == (30, 5.0)
    assert next_tier(28) == (30, 5.0)
    assert next_tier(30) is None


def test_prim_is_on_all_sales_ciro() -> None:
    p = rep_progress(counted=20, all_sales=25, ciro=800000.0, elapsed=26, month_wd=26)
    assert p["oran"] == 2.5 and p["prim"] == 20000.0
    assert p["sonraki"]["kalan"] == 10 and p["sonraki"]["ek_prim"] == 20000.0
    # Ay bitti: tahmin = gerçekleşen, günlük gereken yok.
    assert p["tahmin_sayilan"] == 20.0 and p["sonraki"]["gunluk_gereken"] is None


def test_forecast_projects_pace_to_month_end() -> None:
    p = rep_progress(counted=12, all_sales=14, ciro=420000.0, elapsed=10, month_wd=26)
    assert p["tahmin_sayilan"] == 31.2
    assert p["tahmin_oran"] == 5.0
    assert p["tahmin_ciro"] == 1092000.0
    assert p["tahmin_prim"] == 54600.0
    assert p["sonraki"]["gunluk_gereken"] == round(18 / 16, 2)


def test_elapsed_counts_today_by_shift_fraction() -> None:
    # Ekim 2026: 26 iş günü (pazarlar ve 29 Ekim hariç). 13 Ekim Salı 13:30 → 10 tam gün (4 ve 11 Ekim pazar) + yarım.
    done, total = elapsed_workdays(date(2026, 10, 1), datetime(2026, 10, 13, 13, 30, tzinfo=TZ))
    assert total == 26
    assert done == 10.5
    assert elapsed_workdays(date(2026, 9, 1), datetime(2026, 10, 13, tzinfo=TZ)) == (26.0, 26)


def test_deal_row_parsing() -> None:
    rec = {"id": 9, "Paket": {"name": "Rexven-Premium", "id": "1"}, "Type": "Yeni Müşteri",
           "Stage": "Kapandı Kazanıldı", "Amount": 34000, "Owner": {"id": "77", "name": "X"},
           "Closing_Date": "2026-10-12", "Created_Time": "2026-10-10T10:00:00+03:00"}
    row = row_from_record(rec)
    assert row[:7] == ("9", "Rexven-Premium", "Yeni Müşteri", "Kapandı Kazanıldı", 34000.0, "77", date(2026, 10, 12))


def test_foreign_currency_deal_converted_to_try() -> None:
    from pusula.ingest.crm_deals import amount_try

    # 950,27 € kur 0.018167 → 52.307,48 TL (Abdullah, 7 Ekim).
    assert float(amount_try({"Amount": 950.27, "Exchange_Rate": 0.018167})) == 52307.48
    assert float(amount_try({"Amount": 45800, "Exchange_Rate": 1})) == 45800.0
    assert float(amount_try({"Amount": 45800})) == 45800.0
    assert amount_try({"Amount": None, "Exchange_Rate": 0.02}) is None
    row = row_from_record({"id": 1, "Amount": 855.22, "Exchange_Rate": 0.018051, "Stage": "Kapandı Kazanıldı"})
    assert round(row[4], 2) == 47377.98


def test_classify_deal_explains_why_not_counted() -> None:
    from pusula.commission import classify_deal

    oct1 = date(2026, 10, 1)
    won = "Kapandı Kazanıldı"
    assert classify_deal(won, "Rexven-Premium", date(2026, 10, 14), oct1) == {"ciro": True, "sayilan": True, "neden": None}
    assert classify_deal("Ödeme Bekleniyor", "Rexven-Premium", date(2026, 10, 14), oct1)["neden"].startswith("Aşama: Ödeme")
    assert "bu ay değil" in classify_deal(won, "Rexven-Plus", date(2026, 11, 2), oct1)["neden"]
    empty = classify_deal(won, None, date(2026, 10, 3), oct1)
    assert empty["ciro"] and not empty["sayilan"] and empty["neden"].startswith("Paket alanı boş")
    assert classify_deal(won, "Kamp", date(2026, 10, 3), oct1)["neden"] == "Kamp adede girmez; ciroya girer"


def test_past_day_view_uses_end_of_that_day() -> None:
    from pusula.commission import as_of_moment
    from pusula.web.app import commission_asof

    today = date(2026, 10, 14)
    assert commission_asof("2026-10-13", today) == date(2026, 10, 13)
    assert commission_asof("2026-10-14", today) is None and commission_asof(None, today) is None
    # 13 Ekim gün sonu: 10 tam iş günü + 13'ü tamamı = 11.
    done, total = elapsed_workdays(date(2026, 10, 1), as_of_moment(date(2026, 10, 13)))
    assert (done, total) == (11.0, 26)
