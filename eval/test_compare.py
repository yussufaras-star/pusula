"""Karşılaştırma ekranı: hizalama, ortalama ve seri kırılımı (veritabanısız)."""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from pusula.compare import (
    AVG,
    TEAM,
    Side,
    build_side,
    load_side,
    series_group,
    side_cut,
    work_days,
)
from pusula.metrics import TZ, Span
from pusula.panel_auth import AuthUser
from pusula.panel_data import Rep
from pusula.web import app as web_app
from pusula.web.auth import encode_session

MONDAY = date(2026, 10, 12)
LAST_MONDAY = date(2026, 10, 5)
SECRET = "test-session-secret-that-is-longer-than-32-characters"


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def row(bucket: str | None = None, **values: float) -> dict:
    base = {"rep_id": None, "bucket": bucket, "arama": 0, "giden_temas": 0, "donus": 0}
    base.update(values)
    base.setdefault("ulasilan", base["giden_temas"] + base["donus"])
    return base


def test_running_week_aligns_last_week_to_same_moment() -> None:
    this_week = Side(MONDAY, MONDAY + (date(2026, 10, 17) - MONDAY), TEAM)
    last_week = Side(LAST_MONDAY, date(2026, 10, 10), TEAM)
    now = at(MONDAY, 13, 30)

    assert side_cut(this_week, now, other=last_week) == now
    assert side_cut(last_week, now, other=this_week) == at(LAST_MONDAY, 13, 30)
    assert side_cut(last_week, now) == Span.days(LAST_MONDAY, date(2026, 10, 10)).end


def test_two_past_periods_are_not_clipped() -> None:
    a = Side(LAST_MONDAY, LAST_MONDAY, TEAM)
    b = Side(date(2026, 9, 28), date(2026, 9, 28), TEAM)
    now = at(MONDAY, 10)
    assert side_cut(a, now, other=b) == Span.day(LAST_MONDAY).end
    assert side_cut(b, now, other=a) == Span.day(date(2026, 9, 28)).end


def test_work_days_count_running_day_by_elapsed_shift() -> None:
    week = Span.days(LAST_MONDAY, date(2026, 10, 11))
    assert work_days(week, week.end) == 6
    # Pazartesi 09:00-13:30, 9 saatlik vardiyanın yarısı.
    assert work_days(Span.day(MONDAY), at(MONDAY, 13, 30)) == 0.5


def test_average_subject_divides_counts_but_keeps_rates() -> None:
    side = Side(LAST_MONDAY, LAST_MONDAY, AVG)
    total = [row(arama=100, giden_temas=40, lead_payda=80, lead_pay=20, randevu=10)]
    out = build_side(
        side,
        Span.day(LAST_MONDAY).end,
        n_reps=4,
        total_rows=total,
        series_rows=[],
        label="Temsilci ortalaması",
    )
    assert out["kpis"]["arama"] == 25
    assert out["kpis"]["randevu"] == 2.5
    assert out["kpis"]["ulasma_orani"] == 25.0
    assert out["kpis"]["mesai_dk"] == pytest.approx(465)


def test_team_subject_scales_shift_minutes_by_headcount() -> None:
    side = Side(LAST_MONDAY, LAST_MONDAY, TEAM)
    out = build_side(
        side,
        Span.day(LAST_MONDAY).end,
        n_reps=4,
        total_rows=[row(arama=100)],
        series_rows=[],
        label="Ekip toplamı",
    )
    assert out["kpis"]["mesai_dk"] == pytest.approx(4 * 465)
    assert out["n_reps"] == 4


def test_single_day_series_is_hourly_with_empty_hours_filled() -> None:
    side = Side(MONDAY, MONDAY, "r1")
    out = build_side(
        side,
        at(MONDAY, 11, 30),
        n_reps=4,
        total_rows=[row(arama=12)],
        series_rows=[row("9", arama=7), row("11", arama=5)],
        label="Ayşe",
    )
    assert out["series_group"] == "hour"
    assert [p["bucket"] for p in out["series"]] == [9, 10, 11]
    assert [p["kpis"]["arama"] for p in out["series"]] == [7, 0, 5]
    assert [p["partial"] for p in out["series"]] == [False, False, True]


def test_multi_day_series_skips_sundays() -> None:
    side = Side(date(2026, 10, 9), MONDAY, TEAM)
    out = build_side(
        side,
        Span.day(MONDAY).end,
        n_reps=2,
        total_rows=[],
        series_rows=[row("2026-10-09", arama=3)],
        label="Ekip toplamı",
    )
    assert out["series_group"] == "day"
    assert [p["bucket"] for p in out["series"]] == ["2026-10-09", "2026-10-10", "2026-10-12"]


def test_day_and_week_share_daily_series() -> None:
    day = Side(MONDAY, MONDAY, TEAM)
    week = Side(LAST_MONDAY, date(2026, 10, 10), TEAM)
    assert series_group(day, Side(LAST_MONDAY, LAST_MONDAY, TEAM)) == "hour"
    assert series_group(day, week) == "day"
    out = build_side(
        day,
        at(MONDAY, 11),
        n_reps=2,
        total_rows=[],
        series_rows=[row("2026-10-12", arama=4)],
        label="Ekip toplamı",
        group="day",
    )
    assert [p["bucket"] for p in out["series"]] == ["2026-10-12"]
    assert out["series"][0]["partial"] is True


def test_load_side_filters_query_to_subject() -> None:
    calls: list[dict] = []

    def fake_query(span, **kwargs):
        calls.append(kwargs)
        return []

    side = Side(LAST_MONDAY, LAST_MONDAY, "r2")
    load_side(side, Span.day(LAST_MONDAY).end, rep_ids=["r1", "r2"], label="x", query=fake_query)
    assert [c["rep_ids"] for c in calls] == [["r2"], ["r2"]]
    assert [c["group"] for c in calls] == ["team", "hour"]


def _client(monkeypatch, user: AuthUser) -> TestClient:
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.setattr(web_app, "load_reps", lambda: [Rep("r1", "Ayşe"), Rep("r2", "Mehmet")])
    monkeypatch.setattr(web_app, "load_side", lambda side, cut, **kw: {"label": kw["label"]})
    web_app.cache.clear()
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(user))
    return client


QUERY = "/api/compare?a_from=2026-10-05&a_to=2026-10-05&b_from=2026-09-28&b_to=2026-09-28"


def test_rep_cannot_compare_another_rep(monkeypatch) -> None:
    rep = AuthUser(email="ayse@x.com", role="rep", rep_id="r1", full_name="Ayşe")
    client = _client(monkeypatch, rep)
    assert client.get(f"{QUERY}&a_subject=r1&b_subject=avg").status_code == 200
    assert client.get(f"{QUERY}&a_subject=r1&b_subject=r2").status_code == 403


def test_admin_compares_two_reps_by_name(monkeypatch) -> None:
    admin = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
    client = _client(monkeypatch, admin)
    body = client.get(f"{QUERY}&a_subject=r1&b_subject=r2").json()
    assert (body["a"]["label"], body["b"]["label"]) == ("Ayşe", "Mehmet")
    assert client.get(f"{QUERY}&a_subject=r9").status_code == 404


def test_reversed_range_is_rejected(monkeypatch) -> None:
    admin = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
    client = _client(monkeypatch, admin)
    bad = "/api/compare?a_from=2026-10-05&a_to=2026-10-01&b_from=2026-09-28&b_to=2026-09-28"
    assert client.get(bad).status_code == 400
