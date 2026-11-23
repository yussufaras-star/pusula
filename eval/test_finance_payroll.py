"""Personel maliyeti: TR / DE bordro hesabı, sözleşme, görünüm toplamları, API doğrulaması."""

from contextlib import nullcontext
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pusula.db.models import FinFxRate
from pusula.finance import payroll
from pusula.finance.fx import FxTable
from pusula.finance.payroll import FinPayroll, line_cost, payroll_view
from pusula.panel_auth import AuthUser
from pusula.web import finance as web_finance
from pusula.web.app import app
from pusula.web.auth import encode_session

SECRET = "test-session-secret-that-is-longer-than-32-characters"
ADMIN = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
FX = FxTable(
    [
        FinFxRate(date=date(2026, 11, 20), currency="TRY", usd_rate=Decimal("0.02"), source="ecb"),
        FinFxRate(date=date(2026, 11, 20), currency="EUR", usd_rate=Decimal("1.12"), source="ecb"),
    ]
)


def _line(**kw) -> FinPayroll:
    base = {"person": "A", "company_id": "tr", "kind": "bordro", "currency": "TRY", "amount": Decimal("28075.50")}
    return FinPayroll(**{**base, **kw})


def test_tr_minimum_wage_net_maps_to_gross() -> None:
    cost = line_cost(_line())
    assert cost.brut == pytest.approx(33030, abs=1)
    assert cost.net == pytest.approx(28075.5, abs=1)
    # %21,75 işveren payı + brüt/12 kıdem karşılığı
    assert cost.employer == pytest.approx(33030 * 0.2175, abs=1)
    assert cost.severance == pytest.approx(33030 / 12, abs=1)
    assert cost.total == pytest.approx(cost.brut + cost.employer + cost.severance, abs=0.01)


def test_tr_high_salary_hits_ceilings() -> None:
    cost = line_cost(_line(amount=Decimal("300000"), basis="brut"))
    assert cost.employer == pytest.approx(33030 * 7.5 * 0.2175, abs=1)  # SGK tavanı
    assert cost.severance == pytest.approx(73729.87 / 12, abs=1)  # kıdem tavanı
    assert cost.net < 300000 * 0.75


def test_meal_is_cost_but_not_salary() -> None:
    cost = line_cost(_line(meal=Decimal("7260")))
    assert cost.benefits == 7260
    assert cost.total == pytest.approx(cost.brut + cost.employer + cost.severance + 7260, abs=0.01)


def test_de_gross_and_net_agree() -> None:
    # €6.660 brüt ≈ €4.000 net (vergi sınıfı I)
    gross = line_cost(_line(company_id="gmbh", currency="EUR", amount=Decimal("6660"), basis="brut"))
    assert gross.net == pytest.approx(3991, abs=15)
    net = line_cost(_line(company_id="gmbh", currency="EUR", amount=Decimal("4000"), basis="net"))
    assert net.brut == pytest.approx(6677, abs=15)
    assert net.severance == 0
    assert 1.15 < net.total / net.brut < 1.25


def test_contract_costs_what_is_paid() -> None:
    cost = line_cost(_line(company_id="llc", kind="sozlesme", currency="USD", amount=Decimal("4000")))
    assert (cost.net, cost.brut, cost.employer, cost.total) == (4000, 4000, 0, 4000)


def test_bordro_only_for_tr_and_gmbh() -> None:
    with pytest.raises(ValueError):
        _line(company_id="llc")


def test_view_groups_people_and_excludes_c_level_from_per_head() -> None:
    lines = [
        _line(person="Kurucu", c_level=True, amount=Decimal("28075.50")),
        _line(person="Kurucu", c_level=True, company_id="llc", kind="sozlesme", currency="USD", amount=Decimal("4000")),
        _line(person="Mentor", company_id="llc", kind="sozlesme", amount=Decimal("100000")),
        _line(person="Eski", company_id="llc", kind="sozlesme", amount=Decimal("50000"), active=False),
    ]
    view = payroll_view(lines, FX, ["llc", "gmbh", "tr"])
    assert view["headcount"] == 2 and view["staff_headcount"] == 1
    kurucu = next(p for p in view["people"] if p["person"] == "Kurucu")
    assert len(kurucu["lines"]) == 2 and view["people"][0]["person"] == "Kurucu"
    assert view["per_head_monthly_usd"] == pytest.approx(2000)  # 100.000 TL × 0,02
    by_co = {c["company_id"]: c for c in view["companies"]}
    assert by_co["llc"]["headcount"] == 2 and by_co["tr"]["headcount"] == 1
    assert view["totals"]["total_usd"] == pytest.approx(sum(c["total_usd"] for c in view["companies"]), abs=0.05)
    assert view["burden_ratio"] > 1


def test_view_scope_and_missing_fx() -> None:
    fx = FxTable([])
    view = payroll_view([_line(company_id="gmbh", currency="EUR", amount=Decimal("4000"))], fx, ["gmbh"])
    assert "EUR" in fx.missing and view["totals"]["total_usd"] == 0
    assert payroll_view([_line()], FX, ["gmbh"])["headcount"] == 0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.delenv("PUSULA_FINANCE_EMAILS", raising=False)
    monkeypatch.setattr(web_finance, "connect", lambda: nullcontext(object()))
    monkeypatch.setattr(payroll, "ensure_schema", lambda conn: None)
    inserts: list = []
    monkeypatch.setattr(web_finance.store, "insert_row", lambda conn, table, values: inserts.append((table, values)) or 9)
    c = TestClient(app)
    c.cookies.set("pusula_session", encode_session(ADMIN))
    c.inserts = inserts
    return c


def test_create_validates_and_whitelists(client) -> None:
    ok = {"person": "Ayşe", "company_id": "tr", "kind": "bordro", "currency": "TRY", "amount": 32740, "meal": 7260}
    assert client.post("/api/finance/payroll", json=ok).json() == {"id": 9}
    table, values = client.inserts[0]
    assert table == "fin_payroll" and values["amount"] == Decimal("32740")
    assert client.post("/api/finance/payroll", json={**ok, "company_id": "llc"}).status_code == 400
    assert client.post("/api/finance/payroll", json={**ok, "salary": 1}).status_code == 400
    assert client.post("/api/finance/payroll", json={**ok, "amount": -1}).status_code == 400
