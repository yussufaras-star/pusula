"""Lead reklam kaynağı: alan temizliği."""

from pusula.ingest.lead_attribution import clean, row_from_record


def test_clean_merges_case_and_blank() -> None:
    assert clean("  Meta ") == "meta"
    assert clean("") is None and clean(None) is None and clean(5) is None


def test_row_from_record() -> None:
    rec = {"id": 123, "UTM_Source": "Google", "UTM_Campaign": "rexven_pmax", "UTM_Content": " "}
    assert row_from_record(rec) == ("123", "google", "rexven_pmax", None)
    assert row_from_record({"UTM_Source": "meta"}) is None
