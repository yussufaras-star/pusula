"""Gelen lead akışı: güne sayım, tekil kişi, içe aktarım günleri."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone

from pusula.ingest.lead_inflow import (
    IMPORT_DAYS,
    LeadRec,
    _month_chunks,
    bucket,
    day_rows,
    mark_unique,
    norm_phone,
    person_keys,
)

TR = timezone(timedelta(hours=3))


def rec(lead_id: str, day: int, hour: int, *keys: str, month: int = 1, converted: bool = False) -> LeadRec:
    return LeadRec(lead_id, datetime(2026, month, day, hour, tzinfo=TR), keys, converted)


def test_bucket_uses_istanbul_day() -> None:
    counts = bucket([
        "2026-01-05T23:30:00+03:00",
        "2026-01-05T21:10:00Z",  # İstanbul 00:10 → 6 Ocak
        "2026-01-06T09:00:00+03:00",
        None,
        "",
    ])
    assert counts == Counter({date(2026, 1, 5): 1, date(2026, 1, 6): 2})


def test_person_keys_normalize_email_and_phone() -> None:
    assert norm_phone("+90 (532) 111 22 33") == "5321112233"
    assert norm_phone("05321112233") == "5321112233"
    assert norm_phone("12345") is None
    keys = person_keys({"Email": " Ali@Mail.com ", "Mobile": "0532 111 22 33", "Phone": "+905321112233"})
    assert keys == ("e:ali@mail.com", "p:5321112233")
    assert person_keys({"Email": "yok", "Phone": None}) == ()


def test_mark_unique_counts_first_appearance_of_person() -> None:
    records = [
        rec("a", 5, 9, "e:x@y.com"),
        rec("b", 5, 9, "e:x@y.com"),  # aynı saniye: id sırası
        rec("c", 6, 10, "p:5321112233", "e:x@y.com"),  # telefon yeni ama e-posta eski → ikili
        rec("d", 7, 11, "p:5321112233"),  # c üzerinden aynı kişi
        rec("e", 7, 12),  # kimliksiz → yeni sayılır
    ]
    flags, changed = mark_unique(records, {})
    assert flags == {"a": True, "b": False, "c": False, "d": False, "e": True}
    assert changed["e:x@y.com"][1] == "a" and changed["p:5321112233"][1] == "c"
    # Yeniden sayım (önceki anahtarlar saklı) aynı sonucu verir
    again, changed2 = mark_unique(records, changed)
    assert again == flags and changed2 == {}
    # Önceki çalışmada daha erken görülmüş kişi bugün ikilidir
    later = [rec("z", 9, 9, "e:x@y.com")]
    assert mark_unique(later, changed)[0] == {"z": False}


def test_day_rows_fill_zeros_and_zero_import_days() -> None:
    records = [
        rec("a", 1, 10, "e:a@a.com", month=4),
        rec("b", 3, 10, "e:b@b.com", month=4, converted=True),
        rec("c", 3, 11, "e:b@b.com", month=4),
    ]
    unique = {"b": True, "c": False}
    rows = day_rows(date(2026, 3, 31), date(2026, 4, 3), records, unique)
    assert rows == [
        (date(2026, 3, 31), 0, 0, 0, False),
        (date(2026, 4, 1), 1, 0, 0, True),
        (date(2026, 4, 2), 0, 0, 0, True),
        (date(2026, 4, 3), 2, 1, 1, False),
    ]
    assert date(2026, 4, 2) in IMPORT_DAYS


def test_month_chunks_cover_range() -> None:
    assert _month_chunks(date(2026, 11, 15), date(2027, 1, 3)) == [
        (date(2026, 11, 15), date(2026, 11, 30)),
        (date(2026, 12, 1), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 1, 3)),
    ]
