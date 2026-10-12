"""Toplantı tablosu: durum, süre, sonuç girişi ve takip araması (veritabanısız)."""

from datetime import datetime, timedelta

from pusula.meetings import classify, summarize
from pusula.metrics import TZ

T0 = datetime(2026, 10, 12, 14, 0, tzinfo=TZ)
NOW = T0 + timedelta(hours=3)


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def row(**kw):
    base = {"rep_id": "r1", "start": T0, "planned_min": 30, "crm_status": "sonuc_girilmedi",
            "customer": "Müşteri", "customer_sec": 0}
    base.update(kw)
    return base


def test_customer_came_per_zoho_meeting_even_without_crm_result() -> None:
    item = classify(row(presenter_join=m(0), presenter_leave=m(35),
                        customer_join=m(2), customer_leave=m(33), customer_sec=1860), NOW)
    assert item["state"] == "geldi" and item["source"] == "zoho_meeting"
    assert item["minutes"] == 33
    assert not item["result_entered"]


def test_no_show_with_follow_up_call() -> None:
    item = classify(row(presenter_join=m(0), presenter_leave=m(45), crm_status="katilmadi",
                        call_after=m(16)), NOW)
    assert item["state"] == "gelmedi" and item["followed_up"]
    assert item["minutes"] == 10  # boş odada en fazla 10 dk


def test_crm_attended_but_nobody_in_room_is_mismatch() -> None:
    item = classify(row(presenter_join=m(0), presenter_leave=m(30), crm_status="katildi"), NOW)
    assert item["state"] == "gelmedi" and item["mismatch"]


def test_crm_only_meeting_uses_result() -> None:
    came = classify(row(crm_status="katildi"), NOW)
    assert came["state"] == "geldi" and came["source"] == "crm" and came["minutes"] == 30
    unknown = classify(row(), NOW)
    assert unknown["state"] == "bilinmiyor" and unknown["minutes"] == 0


def test_future_ongoing_and_cancelled() -> None:
    assert classify(row(start=m(60)), m(30))["state"] == "bekleniyor"
    assert classify(row(), m(10))["state"] == "suruyor"
    assert classify(row(presenter_join=m(0)), m(50))["state"] == "suruyor"
    assert classify(row(crm_status="iptal_edildi"), NOW)["state"] == "iptal"


def test_summary_counts_and_attendance_rate() -> None:
    items = [
        classify(row(crm_status="katildi"), NOW),
        classify(row(crm_status="katilmadi", call_after=m(40)), NOW),
        classify(row(crm_status="katilmadi"), NOW),
        classify(row(), NOW),
        classify(row(crm_status="iptal_edildi"), NOW),
        classify(row(start=m(400)), NOW),
    ]
    s = summarize(items)["r1"]
    assert (s["planlanan"], s["gelen"], s["gelmeyen"], s["bilinmiyor"], s["iptal"]) == (5, 1, 2, 1, 1)
    assert (s["aranan"], s["aranmayan"]) == (1, 1)
    assert (s["sonuc_girilen"], s["sonuc_girilmemis"]) == (3, 1)
    assert s["katilim_orani"] == 33.3
    assert s["toplam_dk"] == 30
