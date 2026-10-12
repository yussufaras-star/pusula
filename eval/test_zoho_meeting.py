"""Zoho Meeting katılım özeti ve doluluk sorgusu (ağ / veritabanı yok)."""

from datetime import datetime, timezone

from datetime import timedelta

from pusula.ingest.zoho_meeting import busy_interval, summarize
from pusula.occupancy import _presence_sql

SESSION = {
    "meetingKey": 1044471286,
    "topic": "Ayşe with Müşteri",
    "presenterEmail": "Ayse.Kar@rexven.com",
    "startTimeMillisec": 1791805200000,
    "duration": 1800000,
}


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_presenter_presence_and_customer_time() -> None:
    join = datetime(2026, 10, 12, 11, 41, tzinfo=timezone.utc)
    leave = datetime(2026, 10, 12, 12, 23, tzinfo=timezone.utc)
    parts = [
        {"role": "presenter", "email": "ayse.kar@rexven.com", "joinTime": ms(join),
         "leaveTime": ms(leave), "duration": 2520000},
        {"role": "participant", "email": "Müşteri", "joinTime": ms(join),
         "leaveTime": ms(leave), "duration": 2465136},
    ]
    row = summarize(SESSION, parts)
    assert row["presenter_email"] == "ayse.kar@rexven.com"
    assert row["presenter_join"] == join and row["presenter_leave"] == leave
    assert row["customer_sec"] == 2465.1
    assert row["planned_min"] == 30.0
    assert row["participants"] == 2


def test_rejoining_presenter_spans_first_join_to_last_leave() -> None:
    a = datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc)
    b = datetime(2026, 10, 12, 11, 10, tzinfo=timezone.utc)
    c = datetime(2026, 10, 12, 11, 12, tzinfo=timezone.utc)
    d = datetime(2026, 10, 12, 11, 30, tzinfo=timezone.utc)
    parts = [
        {"role": "presenter", "email": "x@rexven.com", "joinTime": ms(a), "leaveTime": ms(b)},
        {"role": "presenter", "email": "x@rexven.com", "joinTime": ms(c), "leaveTime": ms(d)},
    ]
    row = summarize(SESSION, parts)
    assert (row["presenter_join"], row["presenter_leave"]) == (a, d)


def test_ongoing_presence_has_no_leave() -> None:
    a = datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc)
    row = summarize(SESSION, [{"role": "presenter", "joinTime": ms(a), "leaveTime": 0}])
    assert row["presenter_join"] == a and row["presenter_leave"] is None


def test_no_participants_means_no_presence() -> None:
    row = summarize(SESSION, [])
    assert row["presenter_join"] is None and row["customer_sec"] == 0


def test_presence_query_reuses_rep_and_weekday_filters() -> None:
    extra = (
        " AND e.rep_id = ANY(%s)"
        " AND extract(isodow FROM e.occurred_at AT TIME ZONE 'Europe/Istanbul')::int = ANY(%s)"
    )
    sql = _presence_sql(extra)
    assert "ms.rep_id = ANY(%s)" in sql
    assert "ms.presenter_join AT TIME ZONE" in sql
    assert "e." not in sql.replace("table", "")


T0 = datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=5)


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_customer_meeting_counts_together_time_plus_wait() -> None:
    # Temsilci 0. dk girdi, müşteri 15'te geldi, 40'ta çıktı, temsilci 76'ya kadar kaldı.
    busy = busy_interval(m(0), m(76), m(15), m(40), 1500, NOW)
    assert busy == (m(5), m(40))


def test_short_wait_is_fully_busy() -> None:
    busy = busy_interval(m(0), m(30), m(3), m(28), 1500, NOW)
    assert busy == (m(0), m(28))


def test_no_show_counts_only_ten_minutes() -> None:
    assert busy_interval(m(0), m(45), None, None, 0, NOW) == (m(0), m(10))
    assert busy_interval(m(0), m(4), None, None, 0, NOW) == (m(0), m(4))


def test_customer_after_presenter_left_is_no_show() -> None:
    assert busy_interval(m(0), m(20), m(30), m(40), 600, NOW) == (m(0), m(10))


def test_ongoing_meeting_runs_until_now() -> None:
    now = m(25)
    assert busy_interval(m(0), None, m(5), None, 600, now) == (m(0), m(25))


def test_customer_interval_is_stored() -> None:
    parts = [
        {"role": "presenter", "joinTime": ms(m(0)), "leaveTime": ms(m(40))},
        {"role": "participant", "joinTime": ms(m(5)), "leaveTime": ms(m(35)), "duration": 1800000},
    ]
    row = summarize(SESSION, parts)
    assert (row["customer_join"], row["customer_leave"]) == (m(5), m(35))
