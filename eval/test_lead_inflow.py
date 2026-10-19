"""Gelen lead akışı: Zoho Created_Time'ın güne sayımı ve içe aktarım günleri."""

from collections import Counter
from datetime import date

from pusula.ingest.lead_inflow import IMPORT_DAYS, _month_chunks, bucket, day_rows


def test_bucket_uses_istanbul_day() -> None:
    counts = bucket([
        "2026-01-05T23:30:00+03:00",
        "2026-01-05T21:10:00Z",  # İstanbul 00:10 → 6 Ocak
        "2026-01-06T09:00:00+03:00",
        None,
        "",
    ])
    assert counts == Counter({date(2026, 1, 5): 1, date(2026, 1, 6): 2})


def test_day_rows_fill_zeros_and_flag_import() -> None:
    total = Counter({date(2026, 4, 1): 4210, date(2026, 4, 3): 80})
    converted = Counter({date(2026, 4, 3): 2})
    rows = day_rows(date(2026, 3, 31), date(2026, 4, 3), total, converted)
    assert rows == [
        (date(2026, 3, 31), 0, 0, False),
        (date(2026, 4, 1), 4210, 0, True),
        (date(2026, 4, 2), 0, 0, True),
        (date(2026, 4, 3), 80, 2, False),
    ]
    assert date(2026, 4, 2) in IMPORT_DAYS


def test_month_chunks_cover_range() -> None:
    assert _month_chunks(date(2026, 11, 15), date(2027, 1, 3)) == [
        (date(2026, 11, 15), date(2026, 11, 30)),
        (date(2026, 12, 1), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 1, 3)),
    ]
