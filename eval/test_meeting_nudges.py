"""Randevu dürtmeleri: pencere, oda durumu ve mesaj (veritabanısız)."""

from datetime import datetime, timedelta

from pusula.meeting_nudges import Appointment, due, format_message, to_appointments
from pusula.metrics import TZ

START = datetime(2026, 10, 15, 15, 0, tzinfo=TZ)


def appt(**kw) -> Appointment:
    base = dict(
        booking_id="#RE-1", rep_id="r1", start=START, customer="Ahmet Yılmaz",
        phone="+905551112233", status="upcoming", meeting_key="m1",
        join_link="https://meet.zoho.com/x",
    )
    base.update(kw)
    return Appointment(**base)


def at(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def test_call_reminder_about_an_hour_before() -> None:
    assert due(appt(), at(-60), {}) == "ara"
    assert due(appt(), at(-21), {}) == "ara"
    assert due(appt(), at(-70), {}) is None


def test_meeting_reminder_twenty_minutes_before() -> None:
    assert due(appt(), at(-20), {}) == "hatirlat"
    assert due(appt(), at(-1), {}) == "hatirlat"


def test_room_alert_only_when_zoho_says_delayed() -> None:
    assert due(appt(), at(5), {"m1": "delay"}) == "oda"
    assert due(appt(), at(5), {"m1": "started"}) is None
    assert due(appt(), at(5), {}) is None
    assert due(appt(), at(1), {"m1": "delay"}) is None
    assert due(appt(), at(35), {"m1": "delay"}) is None


def test_closed_appointments_are_skipped() -> None:
    for status in ("cancel", "completed", "no_show"):
        assert due(appt(status=status), at(-60), {}) is None


def test_messages() -> None:
    text = format_message("ara", appt(), at(-60))
    assert text.startswith("Saat 15:00'te toplantıya gelecek Ahmet Yılmaz isimli müşterini aramayı unutma.")
    assert "+905551112233" in text
    assert format_message("hatirlat", appt(), at(-20)).startswith("20 dk sonra (15:00) Ahmet Yılmaz")
    assert "7 dk önce başladı" in format_message("oda", appt(), at(7))


def test_bookings_rows_map_to_sales_reps_only() -> None:
    rows = [
        {
            "booking_id": "#RE-9", "staff_name": "Serkan Şahin", "customer_name": "makbule  gökdere",
            "customer_contact_no": "+905524875344", "iso_start_time": "2026-10-15T09:30:00+00:00",
            "status": "upcoming",
            "meeting_info": "{'meeting_key': '1027128515', 'join_link': 'https://meet.zoho.com/lrog'}",
        },
        {"booking_id": "#RE-10", "staff_name": "Mentor X", "iso_start_time": "2026-10-15T09:30:00+00:00"},
    ]
    out = to_appointments(rows, {"serkan sahin": "r1"})
    assert len(out) == 1
    a = out[0]
    assert (a.rep_id, a.customer, a.meeting_key) == ("r1", "makbule gökdere", "1027128515")
    assert a.start.astimezone(TZ).hour == 12
