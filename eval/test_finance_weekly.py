"""Otomatik haftalık bilanço: hangi hafta, hangi şirket, tekrar çalışınca ne olur (DB yok)."""

from datetime import date

import pytest

from pusula.db.models import FinAccount, FinCompany, FinSnapshot
from pusula.finance import weekly


@pytest.mark.parametrize(
    ("today", "week"),
    [
        (date(2026, 11, 9), date(2026, 11, 2)),  # pazartesi: dün biten hafta
        (date(2026, 11, 4), date(2026, 10, 26)),  # çarşamba: son biten hafta
        (date(2026, 11, 8), date(2026, 10, 26)),  # pazar: bugün biten hafta henüz kapanmadı
    ],
)
def test_last_closed_week(today, week) -> None:
    assert weekly.last_closed_week(today) == week


@pytest.fixture
def fake(monkeypatch):
    companies = [
        FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", sort=1),
        FinCompany(id="gmbh", name="Rexven GmbH", country="DE", base_currency="EUR", sort=2),
        FinCompany(id="tr", name="Rexven TR", country="TR", base_currency="TRY", sort=3),
    ]
    accounts = [
        FinAccount(id=1, company_id="llc", name="Mercury", institution="Mercury", currency="USD"),
        FinAccount(id=2, company_id="tr", name="Kuveyt", institution="Kuveyt", currency="TRY"),
        FinAccount(id=3, company_id="gmbh", name="Kapalı", institution="X", currency="EUR", active=False),
    ]
    state = {"snapshots": [FinSnapshot(id=7, company_id="llc", week_date=date(2026, 10, 26))], "calls": []}
    monkeypatch.setattr(weekly.store, "load_companies", lambda conn: companies)
    monkeypatch.setattr(weekly.store, "load_accounts", lambda conn: accounts)
    monkeypatch.setattr(weekly.store, "load_snapshots", lambda conn: state["snapshots"])

    def create_week(conn, week, today, ids):
        state["calls"].append((week, today, list(ids)))
        return [100 + i for i, _ in enumerate(ids)]

    monkeypatch.setattr(weekly, "create_week", create_week)
    return state


def test_closed_week_skips_existing_and_accountless(fake) -> None:
    report = weekly.create_closed_week(object(), date(2026, 11, 4))
    # LLC'nin bu haftası var; GmbH'nin aktif hesabı yok
    assert fake["calls"] == [(date(2026, 10, 26), date(2026, 11, 4), ["tr"])]
    assert report == {"week_date": date(2026, 10, 26), "created": ["tr"], "ids": [100], "skipped": ["llc"]}


def test_closed_week_is_noop_when_all_done(fake) -> None:
    fake["snapshots"].append(FinSnapshot(id=8, company_id="tr", week_date=date(2026, 10, 26)))
    report = weekly.create_closed_week(object(), date(2026, 11, 5))
    assert fake["calls"] == [] and report["created"] == []


def test_explicit_week(fake) -> None:
    weekly.create_closed_week(object(), date(2026, 11, 4), date(2026, 11, 2))
    assert fake["calls"][0][0] == date(2026, 11, 2) and fake["calls"][0][2] == ["llc", "tr"]
