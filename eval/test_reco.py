"""Re-Co analizi: gruplar ve iş listesi önceliği (saf)."""

from datetime import date

from pusula.ingest.contact_profiles import row_from_record
from pusula.reco import priority, segment, summarize

TODAY = date(2026, 10, 13)


def row(cid: str, son1: int | None, reco_n: int, bit: date | None, ciro: float = 0.0) -> dict:
    return {"contact_id": cid, "full_name": cid, "owner_name": "Nihat Kemer", "paket": "Premium",
            "abonelik_bit": bit, "son1_satis": son1, "toplam_satis": son1, "reco_n": reco_n, "reco_ciro": ciro}


def test_segments_follow_manager_definitions() -> None:
    assert segment(12, False) == "satiyor_yenilememis"
    assert segment(12, True) == "satiyor_yenilemis"
    assert segment(0, True) == "satmiyor"
    assert segment(None, False) == "satmiyor"


def test_priority_puts_renewal_window_first() -> None:
    rows = [
        row("uzak", 50, 0, date(2027, 6, 1)),
        row("yakinda", 50, 0, date(2026, 10, 20)),
        row("yeni_bitti", 50, 0, date(2026, 10, 1)),
        row("tarihsiz", 50, 0, None),
        row("eski_bitti", 50, 0, date(2026, 3, 1)),
    ]
    out = summarize(rows, TODAY)
    assert [x["contact_id"] for x in out["liste"]] == ["yakinda", "yeni_bitti", "eski_bitti", "uzak", "tarihsiz"]
    assert out["liste"][0]["gun"] == 7
    assert priority(None, TODAY) == (2, 0, 0)


def test_summary_counts() -> None:
    rows = [
        row("a", 10, 0, date(2026, 10, 30)),
        row("b", 30, 1, date(2027, 10, 1), 15000.0),
        row("c", 0, 0, date(2026, 9, 1)),
        row("d", None, 1, None, 9000.0),
    ]
    seg = {s["key"]: s for s in summarize(rows, TODAY)["segments"]}
    assert seg["satiyor_yenilememis"]["musteri"] == 1 and seg["satiyor_yenilememis"]["bitis_30"] == 1
    assert seg["satiyor_yenilemis"]["reco_ciro"] == 15000.0 and seg["satiyor_yenilemis"]["medyan_son1"] == 30
    assert seg["satmiyor"]["musteri"] == 2 and seg["satmiyor"]["abonelik_bitmis"] == 1
    assert seg["satmiyor"]["abonelik_yok"] == 1 and seg["satmiyor"]["reco_satis"] == 1


def test_contact_row_parsing() -> None:
    rec = {"id": 5, "Full_Name": " Ali Veli ", "Owner": {"id": "9", "name": "Nihat Kemer"}, "Paket": "Premium",
           "Abonelik_Biti": "2027-01-31", "Son_1_Y_ll_k_Sat": 120, "Total_Sat": 300}
    r = row_from_record(rec)
    assert r[:4] == ("5", "Ali Veli", "9", "Nihat Kemer")
    assert r[6] == date(2027, 1, 31) and r[7] == 120 and r[8] == 300
