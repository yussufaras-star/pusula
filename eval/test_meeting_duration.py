"""Zoho Meeting gerçekleşen süre: eşleşme, milisaniye ve verimlilik SQL'i."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pusula.ingest.bookings import merge_preserved_meta
from pusula.ingest.zoho_meeting import (
    MATCH_SLACK,
    BookingSlot,
    MeetingDurationIngester,
    MeetingRequestError,
    SessionSpan,
    attendance_ms,
    duration_sec_from_ms,
    extract_participants,
    extract_session_rows,
    match_durations,
    rep_duration_sec,
    sales_rep_for_email,
    session_start,
)
from pusula.panel_activity import _attended_meeting_sql, activity_rank_between
from pusula.panel_data import _attended_meet_minutes_sql

_TZ = ZoneInfo("Europe/Istanbul")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 5, hour, minute, tzinfo=_TZ)


def test_duration_ms_is_not_minutes() -> None:
    assert duration_sec_from_ms(3600000) == 3600
    assert duration_sec_from_ms(82790) == 83
    assert duration_sec_from_ms("1800000") == 1800
    assert duration_sec_from_ms(0) is None
    assert duration_sec_from_ms(-5) is None
    assert duration_sec_from_ms(None) is None
    assert duration_sec_from_ms("yok") is None


def test_join_leave_is_the_real_duration() -> None:
    # meetings.zoho.com örneği: 02:20-02:21, duration 82790 ms.
    row = {
        "email": "ayse.kar@rexven.com",
        "role": "presenter",
        "joinTime": 1693903804737,
        "leaveTime": 1693903887527,
        "duration": 82790,
    }
    assert attendance_ms(row) == 82790
    assert rep_duration_sec([row], "ayse.kar@rexven.com") == 83


def test_scheduled_window_is_not_attendance() -> None:
    scheduled = 3600000
    planned_only = {
        "email": "ayse.kar@rexven.com",
        "role": "presenter",
        "duration": scheduled,
    }
    assert attendance_ms(planned_only, scheduled) is None
    assert rep_duration_sec([planned_only], "ayse.kar@rexven.com", scheduled) is None

    stayed = {
        "email": "ayse.kar@rexven.com",
        "role": "presenter",
        "joinTime": 1_000,
        "leaveTime": 1_000 + 12 * 60 * 1000,
        "duration": scheduled,
    }
    assert rep_duration_sec([stayed], "ayse.kar@rexven.com", scheduled) == 12 * 60


def test_rep_duration_sums_rejoins_and_ignores_others() -> None:
    rows = [
        {"email": "Ayse.Kar@rexven.com", "duration": 40000, "role": "presenter"},
        {"email": "ayse.kar@rexven.com", "duration": 42790, "role": "presenter"},
        {"email": "musteri@firma.com", "duration": 80000, "role": "participant"},
    ]
    assert rep_duration_sec(rows, "ayse.kar@rexven.com") == 83


def test_zero_email_match_does_not_take_another_presenter() -> None:
    rows = [
        {"email": "ayse.kar@rexven.com", "duration": 0, "role": "presenter"},
        {"email": "baska@rexven.com", "duration": 60000, "role": "presenter"},
    ]
    assert rep_duration_sec(rows, "ayse.kar@rexven.com") is None


def test_presenter_role_when_report_has_no_email() -> None:
    rows = [
        {"duration": 30000, "role": "presenter"},
        {"duration": 30000, "role": "Presenter"},
        {"duration": 90000, "role": "participant"},
    ]
    assert rep_duration_sec(rows, "ayse.kar@rexven.com") == 60


def test_session_start_millis_and_timezone_text() -> None:
    instant = datetime(2026, 10, 5, 16, 30, tzinfo=_TZ)
    millis = int(instant.timestamp() * 1000)
    parsed = session_start({"startTimeMillisec": millis})
    assert parsed is not None
    assert parsed == instant

    text = session_start(
        {
            "startTime": "Jun 19, 2020 07:00 PM IST",
            "timezone": "Asia/Calcutta",
        }
    )
    assert text == datetime(2020, 6, 19, 16, 30, tzinfo=_TZ)


def test_extract_session_and_participant_shapes() -> None:
    assert extract_session_rows({"session": [{"meetingKey": 1}]}) == [
        {"meetingKey": 1}
    ]
    assert extract_session_rows({"session": {"meetingKey": 2}}) == [
        {"meetingKey": 2}
    ]
    assert extract_participants({"participants": [{"email": "a@b.co"}]}) == [
        {"email": "a@b.co"}
    ]
    assert extract_participants({}) == []


def test_sales_email_is_normalized() -> None:
    sales = {"ayse.kar@rexven.com": "rep-1"}
    assert sales_rep_for_email("Ayse.Kar@rexven.com", sales) == "rep-1"
    assert sales_rep_for_email("mentor@rexven.com", sales) is None
    assert sales_rep_for_email(None, sales) is None


def test_match_closest_booking_within_slack() -> None:
    sessions = [
        SessionSpan("m1", "rep-1", _at(10, 50), 2400),
        SessionSpan("m2", "rep-1", _at(15, 0), 900),
    ]
    bookings = [
        BookingSlot("b-early", "rep-1", _at(10, 0)),
        BookingSlot("b-late", "rep-1", _at(11, 0)),
        BookingSlot("b-other", "rep-2", _at(10, 50)),
    ]
    matched = match_durations(sessions, bookings, MATCH_SLACK)
    by_ref = {item.source_ref: item for item in matched}
    assert set(by_ref) == {"b-late"}
    assert by_ref["b-late"].meeting_key == "m1"
    assert by_ref["b-late"].duration_sec == 2400
    assert "b-other" not in by_ref
    assert "b-early" not in by_ref


def test_match_skips_far_session_and_assigns_one_to_one() -> None:
    sessions = [
        SessionSpan("near", "rep-1", _at(10, 10), 1000),
        SessionSpan("far", "rep-1", _at(14, 0), 1000),
    ]
    bookings = [BookingSlot("only", "rep-1", _at(10, 0))]
    matched = match_durations(sessions, bookings, timedelta(minutes=90))
    assert len(matched) == 1
    assert matched[0].meeting_key == "near"
    assert matched[0].source_ref == "only"


def test_bookings_keeps_meeting_duration_on_rebuild() -> None:
    meta = {"randevu_durumu": "katildi", "duration": "30 mins"}
    existing = {
        "actual_duration_sec": 2400,
        "meeting_key": "99",
        "duration": "60 mins",
    }
    merged = merge_preserved_meta(meta, existing)
    assert merged["actual_duration_sec"] == 2400
    assert merged["meeting_key"] == "99"
    assert merged["duration"] == "30 mins"
    assert merged["randevu_durumu"] == "katildi"
    assert merge_preserved_meta(meta, None) == meta


def test_efficiency_sql_uses_only_actual_seconds() -> None:
    sql = _attended_meet_minutes_sql("e")
    assert "actual_duration_sec" in sql
    assert "meta->>'duration'" not in sql
    assert "%" not in sql
    src = inspect.getsource(activity_rank_between)
    query = src.split('sql = f"""', 1)[1].split('"""', 1)[0]
    assert query.count("%s") == 4
    assert "_attended_meet_minutes_sql" in src
    assert "FILTER (WHERE {attended})" in src
    attended = _attended_meeting_sql("e")
    assert "katildi" in attended
    assert "%" not in attended


def test_help_says_session_duration() -> None:
    from app.panel import COL_HELP, HELP_SURE_SIRA

    text = COL_HELP["gerçekleşen toplantı süresi"]
    assert "Zoho Meeting" in text
    assert "Planlanan aralık değil" in text
    assert "planlanan süre" not in text.casefold()
    assert "Zoho Meeting" in HELP_SURE_SIRA
    assert "Planlanan aralık" in HELP_SURE_SIRA
    hour = COL_HELP["toplantı süresi"]
    assert "giriş ile çıkış" in hour
    assert "meta.duration" not in hour


def test_fetch_does_not_raise_when_meeting_api_missing(monkeypatch) -> None:
    def _missing(since: datetime | None, lookback: timedelta | None) -> list:
        raise MeetingRequestError("ZOHO_MEETING_ZSOID yok")

    monkeypatch.setattr(
        "pusula.ingest.zoho_meeting._collect_matches", _missing
    )
    ingester = MeetingDurationIngester()
    assert list(ingester.fetch(None)) == []
    assert ingester.fetch_truncated is True
