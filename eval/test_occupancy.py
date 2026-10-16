"""Zaman çizelgesi doluluğu: dolu, mola ve kayıp dakika (veritabanısız)."""

from datetime import date, datetime, timedelta

import pytest

from pusula.metrics import TZ, finish, combine
from pusula.occupancy import aggregate, attach, day_timeline, gaps_report, merge

MONDAY = date(2026, 10, 12)
SATURDAY = date(2026, 10, 10)


def at(hour: int, minute: int = 0, day: date = MONDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def call(h: int, m: int, talk_sec: int = 0):
    """BCW 1 dk önce, ACW 1 dk sonra (SQL'deki aralıkla aynı)."""
    start = at(h, m)
    return (start - timedelta(minutes=1), start + timedelta(seconds=talk_sec + 60))


def test_overlapping_intervals_count_once() -> None:
    merged = merge([call(10, 0, 120), call(10, 2, 60), (at(10, 30), at(10, 31))])
    assert merged == [(at(9, 59), at(10, 4)), (at(10, 30), at(10, 31))]


def test_full_day_splits_into_busy_break_and_lost() -> None:
    # 09:00-12:00 dolu, 12:00-13:30 boş (90 dk), 13:30-18:00 dolu.
    busy = [(at(9), at(12)), (at(13, 30), at(18))]
    t = day_timeline(busy, MONDAY, at(23))
    assert t.total("dolu") == pytest.approx(450)
    assert t.total("mola") == pytest.approx(75)
    assert t.total("kayip") == pytest.approx(15)
    # Mola boşluğun başından: 12:00-13:15 mola, 13:15-13:30 kayıp.
    kinds = [(s.start, s.kind) for s in t.segments]
    assert (at(12), "mola") in kinds and (at(13, 15), "kayip") in kinds


def test_break_is_taken_from_longest_gaps_first() -> None:
    # Boşluklar: 09:00-09:20 (20 dk, geç başlama), 11:00-11:50 (50), 15:00-15:40 (40).
    busy = [(at(9, 20), at(11)), (at(11, 50), at(15)), (at(15, 40), at(18))]
    t = day_timeline(busy, MONDAY, at(23))
    # 75 dk mola: 50 + 25'i (40'lık boşluktan). Kayıp: 20 + 15.
    assert t.total("mola") == pytest.approx(75)
    assert t.total("kayip") == pytest.approx(35)
    assert t.by_hour("kayip")[9] == pytest.approx(20)


def test_running_day_counts_only_elapsed_shift() -> None:
    t = day_timeline([call(9, 10, 240)], MONDAY, at(10))
    total = t.total("dolu") + t.total("mola") + t.total("kayip")
    assert total == pytest.approx(60)
    assert t.total("dolu") == pytest.approx(6)


def test_saturday_shift_ends_at_three() -> None:
    t = day_timeline([], SATURDAY, at(23, day=SATURDAY))
    assert t.total("mola") + t.total("kayip") == pytest.approx(360)


def test_activity_outside_shift_is_clipped() -> None:
    t = day_timeline([(at(8), at(9, 30)), (at(17, 50), at(19))], MONDAY, at(23))
    assert t.total("dolu") == pytest.approx(40)


def test_aggregate_skips_absent_days_but_not_today() -> None:
    absent = day_timeline([], MONDAY, at(23))
    worked = day_timeline([(at(9), at(18))], MONDAY, at(23))
    rows = aggregate({("r1", MONDAY): absent, ("r2", MONDAY): worked}, "rep")
    assert [r["rep_id"] for r in rows] == ["r2"]
    rows = aggregate({("r1", MONDAY): absent}, "rep", running_day=MONDAY)
    assert rows[0]["kayip_dk"] + rows[0]["mola_dk"] == pytest.approx(540)


def test_attach_feeds_finish() -> None:
    worked = day_timeline([(at(9), at(12)), (at(13, 30), at(18))], MONDAY, at(23))
    occ = aggregate({("r1", MONDAY): worked}, "rep_hour")
    rows = attach([{"rep_id": "r1", "bucket": "9", "arama": 5}], occ)
    out = finish(combine(rows))
    assert out["arama"] == 5
    assert out["doluluk"] == pytest.approx(round(100 * 450 / 465, 1))


def test_gaps_report_lists_longest_first() -> None:
    busy = [(at(9, 20), at(11)), (at(11, 50), at(15)), (at(15, 40), at(18))]
    gaps = gaps_report(day_timeline(busy, MONDAY, at(23)))
    assert gaps[0]["kind"] == "mola" and gaps[0]["minutes"] == 50
    assert {g["kind"] for g in gaps} == {"mola", "kayip"}


def test_unentered_meeting_is_lost_time_not_break() -> None:
    """Sonucu girilmemiş toplantı ayrı görünür, kayıba sayılır, moladan düşülmez."""
    from pusula.occupancy import aggregate

    busy = [(at(9), at(12)), (at(13, 30), at(18))]
    pending = [(at(12), at(12, 30))]
    t = day_timeline(busy, MONDAY, at(23), pending=pending)
    assert t.total("girilmemis") == pytest.approx(30)
    # Kalan 60 dk serbest boşluk tamamen mola olur (75 dk hakkın 60'ı).
    assert t.total("mola") == pytest.approx(60)
    assert t.total("kayip") == pytest.approx(0)
    rows = aggregate({("r1", MONDAY): t}, "rep")
    assert rows[0]["kayip_dk"] == pytest.approx(30)
    assert rows[0]["girilmemis_dk"] == pytest.approx(30)


def test_meeting_overlapping_busy_time_is_not_double_counted() -> None:
    busy = [(at(9), at(18))]
    t = day_timeline(busy, MONDAY, at(23), pending=[(at(10), at(10, 30))])
    assert t.total("girilmemis") == 0
    assert t.total("dolu") == pytest.approx(540)


def test_leave_days_have_no_timeline() -> None:
    from pusula.metrics import Span
    from pusula.occupancy import timelines_for

    worked = {("r1", MONDAY): [(at(9), at(18), "dolu")], ("r2", MONDAY): []}
    out = timelines_for(
        Span.day(MONDAY), at(23), rep_ids=["r1", "r2"], intervals=worked, leaves={("r2", MONDAY)}
    )
    assert list(out) == [("r1", MONDAY)]


def test_meeting_part_of_busy_time_is_shown_separately_but_counts_as_busy() -> None:
    from pusula.occupancy import aggregate

    busy = [(at(9), at(12)), (at(10), at(10, 40)), (at(13, 30), at(18))]
    t = day_timeline(busy, MONDAY, at(23), meetings=[(at(10), at(10, 40))])
    assert t.total("toplanti") == pytest.approx(40)
    assert t.total("dolu") == pytest.approx(410)
    rows = aggregate({("r1", MONDAY): t}, "rep")
    assert rows[0]["dolu_dk"] == pytest.approx(450)
    assert rows[0]["toplanti_dk"] == pytest.approx(40)
    assert all(g["kind"] != "toplanti" for g in gaps_report(t))


def test_excused_time_is_neither_lost_nor_break() -> None:
    # 09-10 dolu, 10-12 boş (eğitim 10-11), 12-18 dolu.
    busy = [(at(9), at(10)), (at(12), at(18))]
    t = day_timeline(busy, MONDAY, at(23), excused=[(at(10), at(11))])
    assert t.total("muaf") == pytest.approx(60)
    assert t.total("mola") == pytest.approx(60)
    assert t.total("kayip") == pytest.approx(0)
    row = finish(aggregate({("r1", MONDAY): t}, "rep")[0])
    assert row["doluluk"] == 100.0
    assert row["muaf_dk"] == pytest.approx(60)


def test_activity_inside_excused_window_still_counts_as_busy() -> None:
    busy = [(at(9), at(18))]
    t = day_timeline(busy, MONDAY, at(23), excused=[(at(10), at(11))])
    assert t.total("dolu") == pytest.approx(540)
    assert t.total("muaf") == 0


def test_excused_windows_reach_timelines_for() -> None:
    from pusula.leaves import Excused
    from pusula.metrics import Span
    from pusula.occupancy import timelines_for

    worked = {("r1", MONDAY): [(at(9), at(10), "dolu")]}
    x = Excused(1, "r1", MONDAY, at(10), at(18), "Eğitim")
    out = timelines_for(
        Span.day(MONDAY), at(23), rep_ids=["r1"], intervals=worked, leaves=set(),
        excused={("r1", MONDAY): [x]},
    )
    t = out[("r1", MONDAY)]
    assert t.total("muaf") == pytest.approx(480)
    assert t.total("kayip") == 0


def test_short_gaps_between_activities_count_as_busy() -> None:
    # 09:00-10:00 dolu, 3 dk boşluk, 10:03-11:00 dolu, 20 dk boşluk, 11:20-12:00.
    busy = [(at(9), at(10)), (at(10, 3), at(11)), (at(11, 20), at(12))]
    t = day_timeline(busy, MONDAY, at(12))
    assert t.total("dolu") == pytest.approx(160)
    assert t.total("mola") == pytest.approx(20)
    assert t.total("kayip") == pytest.approx(0)


def test_short_gap_at_day_start_is_not_busy() -> None:
    t = day_timeline([(at(9, 3), at(10))], MONDAY, at(10))
    assert t.total("dolu") == pytest.approx(57)
    assert t.total("mola") == pytest.approx(3)
