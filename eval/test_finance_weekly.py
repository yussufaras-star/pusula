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


def test_us_tax_only_on_us_company(monkeypatch) -> None:
    """ABD vergisi satırı yalnız LLC bilançosuna; AŞ'ye girmez."""
    companies = [
        FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", sort=1),
        FinCompany(id="tr", name="Rexven TR", country="TR", base_currency="TRY", sort=3),
    ]
    seen = {}

    class Fx:
        def latest_rates(self, day=None):
            return {}

    for name, value in {
        "load_companies": lambda conn: companies,
        "load_snapshots": lambda conn: [],
        "load_accounts": lambda conn: [],
        "load_planned": lambda conn: [],
        "load_transactions": lambda conn, start=None, end=None: [],
        "load_wallets": lambda conn: [],
        "load_receivables": lambda conn: [],
        "load_tax_settings": lambda conn, c: None,
        "insert_snapshot": lambda conn, c, week, fx: {"llc": 1, "tr": 2}[c],
        "insert_line": lambda conn, sid, values: 0,
    }.items():
        monkeypatch.setattr(weekly.store, name, value)
    monkeypatch.setattr(weekly, "load_fx", lambda conn, since=None: Fx())
    monkeypatch.setattr(weekly, "intercompany_balances", lambda *a: [])
    monkeypatch.setattr(weekly, "tax_due", lambda *a: [("ABD vergisi", 1)])
    monkeypatch.setattr(
        weekly, "build_week_lines", lambda company_id, *a, tax_due, **k: seen.setdefault(company_id, list(tax_due)) and []
    )
    assert weekly.create_week(object(), date(2026, 10, 26), date(2026, 11, 4)) == [1, 2]
    assert seen == {"llc": [("ABD vergisi", 1)], "tr": []}


def test_receivables_on_latest_per_source() -> None:
    from decimal import Decimal

    from pusula.db.models import FinReceivable

    rows = [
        FinReceivable(company_id="tr", source="lidio", as_of=date(2026, 11, 1), amount=Decimal("100")),
        FinReceivable(company_id="tr", source="lidio", as_of=date(2026, 11, 4), amount=Decimal("200")),
        FinReceivable(company_id="tr", source="iyzico", as_of=date(2026, 10, 30), amount=Decimal("50")),
        FinReceivable(company_id="llc", source="lidio", as_of=date(2026, 11, 1), amount=Decimal("9")),
    ]
    got = {r.source: r.amount for r in weekly.receivables_on(rows, "tr", date(2026, 11, 1))}
    assert got == {"lidio": Decimal("100"), "iyzico": Decimal("50")}
    assert {r.amount for r in weekly.receivables_on(rows, "tr", None)} == {Decimal("200"), Decimal("50")}
