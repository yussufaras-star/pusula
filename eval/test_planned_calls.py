"""Planlanan çağrı: gerçekleştiyse süreye girer, ileri tarih girmez."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pusula.temas import (
    cevirme_mi,
    is_cevirme_sql,
    is_planned_call,
    is_planned_sql,
    is_temas_sql,
    temas_mi,
)

_TZ = ZoneInfo("Europe/Istanbul")


def _event(when: datetime, **meta: object) -> dict:
    return {"occurred_at": when, "meta": meta}


def test_past_planned_call_with_duration_counts() -> None:
    past = datetime.now(_TZ) - timedelta(hours=2)
    event = _event(
        past,
        scheduled=True,
        call_status="scheduled",
        call_duration_sec=963,
    )
    assert is_planned_call(event) is False
    assert temas_mi(event) is True
    assert cevirme_mi(event) is True


def test_future_plan_stays_out_even_with_duration() -> None:
    future = datetime.now(_TZ) + timedelta(days=1)
    event = _event(
        future,
        scheduled="true",
        call_status="scheduled",
        call_duration_sec=1800,
    )
    assert is_planned_call(event) is True
    assert temas_mi(event) is False
    assert cevirme_mi(event) is False


def test_past_plan_without_duration_stays_out() -> None:
    past = datetime.now(_TZ) - timedelta(hours=3)
    event = _event(past, scheduled=True, call_status="overdue", call_duration_sec=0)
    assert is_planned_call(event) is True
    assert temas_mi(event) is False
    assert cevirme_mi(event) is False


def test_past_plan_marked_unanswered_stays_out() -> None:
    past = datetime.now(_TZ) - timedelta(minutes=40)
    event = _event(
        past,
        scheduled=True,
        call_status="scheduled",
        call_duration_sec=12,
        call_result="Yanıt yok/Meşgul",
    )
    assert is_planned_call(event) is True
    assert temas_mi(event) is False
    assert cevirme_mi(event) is False


def test_connected_call_is_unchanged() -> None:
    past = datetime.now(_TZ) - timedelta(minutes=10)
    event = _event(past, call_status="connected", call_duration_sec=75)
    assert is_planned_call(event) is False
    assert temas_mi(event) is True
    assert cevirme_mi(event) is True


def test_planned_sql_keeps_future_out_and_has_no_placeholder() -> None:
    planned = is_planned_sql("e")
    talk = is_temas_sql("e")
    dial = is_cevirme_sql("e")
    for frag in (planned, talk, dial):
        assert "%" not in frag
    assert "occurred_at <= now()" in planned
    assert "call_duration_sec" in planned
    assert "scheduled" in planned
    assert "connected" in dial
    assert "Yanıt yok/Meşgul" in planned
