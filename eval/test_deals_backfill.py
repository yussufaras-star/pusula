"""Deals 2025 backfill yardımcıları — DB yok."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from pusula.ingest.crm_deals import (
    _BACKFILL_CUTOFF,
    _owner_fields,
    _panel_dt,
)

_TZ = ZoneInfo("Europe/Istanbul")


def test_panel_dt_prefers_closing_date() -> None:
    record = {
        "Closing_Date": "2025-11-15",
        "Created_Time": "2025-09-02T10:00:00+03:00",
    }
    got = _panel_dt(record)
    assert got is not None
    assert got.date() == date(2025, 11, 15)


def test_panel_dt_falls_back_to_created() -> None:
    record = {"Created_Time": "2025-09-02T10:00:00+03:00"}
    got = _panel_dt(record)
    assert got is not None
    assert got.date() == date(2025, 9, 2)


def test_backfill_cutoff_is_2026() -> None:
    assert _BACKFILL_CUTOFF == date(2026, 1, 1)
    closed_2025 = datetime(2025, 12, 31, tzinfo=_TZ).date()
    closed_2026 = datetime(2026, 1, 1, tzinfo=_TZ).date()
    assert closed_2025 < _BACKFILL_CUTOFF
    assert closed_2026 >= _BACKFILL_CUTOFF


def test_owner_fields_reads_id_and_name() -> None:
    oid, name = _owner_fields({"id": "123", "name": "Ayşe Kar"})
    assert oid == "123"
    assert name == "Ayşe Kar"
    assert _owner_fields(None) == (None, None)
