"""Lead reklam kaynağı: alan temizliği."""

from pusula.ingest.lead_attribution import clean, row_from_record


def test_clean_merges_case_and_blank() -> None:
    assert clean("  Meta ") == "meta"
    assert clean("") is None and clean(None) is None and clean(5) is None


def test_row_from_record() -> None:
    rec = {"id": 123, "UTM_Source": "Google", "UTM_Campaign": "rexven_pmax", "UTM_Content": " "}
    assert row_from_record(rec) == ("123", "google", "rexven_pmax", None, None)
    created = row_from_record({"id": 1, "Created_Time": "2026-10-15T09:30:00+03:00"})
    assert created and created[4] is not None and created[4].isoformat().startswith("2026-10-15T")
    assert row_from_record({"UTM_Source": "meta"}) is None
