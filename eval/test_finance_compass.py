"""Maaş ve prim pusulası: zam kuralları, kıdem, paket, kişiye özel karar, geçmiş, API."""

from contextlib import nullcontext
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pusula.db.models import FinFxRate
from pusula.finance import compass, payroll
from pusula.finance.compass import CompPlan, PersonPlan, PlanParams, compass_view, history_view
from pusula.finance.fx import FxTable
from pusula.finance.payroll import FinPayroll
from pusula.panel_auth import AuthUser
from pusula.web import finance as web_finance
from pusula.web.app import app
from pusula.web.auth import encode_session

SECRET = "test-session-secret-that-is-longer-than-32-characters"
ADMIN = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
TODAY = date(2026, 11, 20)
# TL bir yılda %25 değer kaybetti: 1/40 → 1/50
FX = FxTable(
    [
        FinFxRate(date=date(2025, 11, 1), currency="TRY", usd_rate=Decimal("0.025"), source="ecb"),
        FinFxRate(date=date(2026, 11, 20), currency="TRY", usd_rate=Decimal("0.02"), source="ecb"),
        FinFxRate(date=date(2026, 11, 20), currency="EUR", usd_rate=Decimal("1.1"), source="ecb"),
    ]
)
ALL = ["llc", "gmbh", "tr"]


def _line(**kw) -> FinPayroll:
    base = {"person": "A", "unit": "Mentor", "company_id": "llc", "kind": "sozlesme", "currency": "TRY", "amount": Decimal("100000")}
    return FinPayroll(**{**base, **kw})


def _view(lines, plan=CompPlan(), history=()):
    return compass_view(lines, list(history), plan, FX, ALL, TODAY)


def _person(view, name="A"):
    return next(p for p in view["people"] if p["person"] == name)


def test_tr_base_plus_seniority_and_proration() -> None:
    view = _view(
        [
            _line(person="Eski", start_date=date(2022, 3, 1)),
            _line(person="Orta", start_date=date(2024, 8, 5)),
            _line(person="Yeni", start_date=date(2026, 7, 9)),
        ]
    )
    assert view["derived"]["kur_artis"] == pytest.approx(0.25)
    assert _person(view, "Eski")["rate"] == pytest.approx(0.33)  # 4 yıl → +3 puan (tavan)
    assert _person(view, "Orta")["rate"] == pytest.approx(0.31)  # 2 yıl → +1 puan
    yeni = _person(view, "Yeni")
    assert yeni["rate"] == pytest.approx(0.15)  # Temmuz girişi: 6/12
    assert any("6/12" in r for r in yeni["reasons"])
    assert _person(view, "Eski")["kidem"] == "4 yıl 8 ay"


def test_germany_uses_german_base() -> None:
    view = _view([_line(company_id="gmbh", kind="bordro", currency="EUR", amount=Decimal("4100"), basis="brut", start_date=date(2025, 11, 3))])
    p = _person(view)
    assert p["rate"] == pytest.approx(0.04)
    assert p["cost27_usd"] > p["cost_usd"]


def test_fx_paid_tr_resident_keeps_tl_purchasing_power() -> None:
    view = _view([_line(currency="EUR", amount=Decimal("4000"))])
    assert _person(view)["rate"] == pytest.approx(1.30 / 1.25 - 1, abs=1e-4)


def test_minimum_wage_plus_usd_package() -> None:
    lines = [
        _line(c_level=True, company_id="tr", kind="bordro", amount=Decimal("28075.50"), meal=Decimal("7260")),
        _line(c_level=True, currency="USD", amount=Decimal("4000")),
    ]
    p = _person(_view(lines))
    asg, usd_line = p["lines"]
    assert asg["asgari"] and asg["rate"] == pytest.approx(0.30)
    fx_rule = 1.30 / 1.25 - 1
    assert p["rate"] == pytest.approx(fx_rule, abs=1e-4)  # paket döviz cinsinden büyür
    assert p["kidem_bonus"] == 0  # C-Level kıdem payı almaz
    # C-Level kararı paketi geçersiz kılar
    plan = CompPlan(params=PlanParams(c_level_usd=0.0))
    assert _person(_view(lines, plan))["rate"] == pytest.approx(0.0, abs=1e-4)


def test_person_decision_overrides_rule() -> None:
    plan = CompPlan(people={"A": PersonPlan(pct=0, note="zam yok"), "B": PersonPlan(adj=0.044, note="eşitlik")})
    view = _view([_line(), _line(person="B")], plan)
    a, b = _person(view, "A"), _person(view, "B")
    assert a["rate"] == 0 and a["reasons"][0].startswith("Karar: %0")
    assert b["rate"] == pytest.approx(1.30 * 1.044 - 1)
    assert view["plan_people"]["A"]["pct"] == 0


def test_seniority_flag_same_unit_only() -> None:
    view = _view(
        [
            _line(person="Kıdemli", start_date=date(2022, 5, 2), amount=Decimal("70000")),
            _line(person="Yeni", start_date=date(2025, 6, 7), amount=Decimal("84000")),
            _line(person="Başka", unit="Yazılım", start_date=date(2026, 1, 1), amount=Decimal("150000")),
        ]
    )
    assert _person(view, "Kıdemli")["flags"] == ["Daha kıdemsiz Yeni (1 yıl 5 ay) %20 daha fazla alıyor"]
    assert "flags" not in _person(view, "Yeni")


def test_company_rows_and_totals_agree() -> None:
    view = _view(
        [
            _line(company_id="tr", kind="bordro", amount=Decimal("28075.50")),
            _line(currency="USD", amount=Decimal("2000")),
            _line(person="B", company_id="gmbh", kind="bordro", currency="EUR", amount=Decimal("4000")),
        ]
    )
    by = {c["company_id"]: c for c in view["companies"]}
    assert by["llc"]["headcount"] == 1 and by["tr"]["headcount"] == 1 and by["gmbh"]["headcount"] == 1
    assert sum(c["cost_usd"] for c in view["companies"]) == pytest.approx(view["totals"]["cost_usd"], abs=0.05)
    assert sum(c["cost27_usd"] for c in view["companies"]) == pytest.approx(view["totals"]["cost27_usd"], abs=0.05)
    assert _view([_line()], history=()) and compass_view([_line()], [], CompPlan(), FX, ["gmbh"], TODAY)["people"] == []


def test_history_quarters_prim_and_2026_raise() -> None:
    rows = [
        {"month": date(2025, m, 1), "person": None, "kind": k, "currency": "USD", "amount": v}
        for m in (10, 11, 12)
        for k, v in (("kar", 50000.0), ("donation", 3000.0))
    ] + [
        {"month": date(2025, 12, 1), "person": "A", "kind": "havuz_prim", "currency": "USD", "amount": 300.0},
        {"month": date(2026, 1, 1), "person": "A", "kind": "satis_prim", "currency": "TRY", "amount": 10000.0},
        {"month": date(2025, 12, 1), "person": "A", "kind": "paket", "currency": "TRY", "amount": 50000.0},
        {"month": date(2026, 1, 1), "person": "A", "kind": "paket", "currency": "TRY", "amount": 70000.0},
    ]
    h = history_view(rows, FX)
    q4 = h["quarters"][0]
    assert (q4["quarter"], q4["kar_usd"], q4["prim_usd"], q4["donation_usd"]) == ("Q4 2025", 150000, 300, 9000)
    assert h["quarters"][1]["satis_prim_usd"] == pytest.approx(250)  # Ocak 2026 kuru 1/40
    assert h["raise_2026"]["A"]["rate"] == pytest.approx(0.4)
    assert h["prim_2026"]["A"] == pytest.approx(250)
    view = _view([_line()], history=rows)
    assert view["history"]["q4_2025"]["prim_usd"] == 300
    assert _person(view)["raise_2026"]["rate"] == pytest.approx(0.4)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.delenv("PUSULA_FINANCE_EMAILS", raising=False)
    monkeypatch.setattr(web_finance, "connect", lambda: nullcontext(object()))
    monkeypatch.setattr(compass, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(payroll, "ensure_schema", lambda conn: None)
    saved: list = []
    monkeypatch.setattr(compass, "save_plan", lambda conn, plan: saved.append(plan))
    monkeypatch.setattr(web_finance.store, "load_companies", lambda conn: [])
    monkeypatch.setattr(web_finance, "_scope", lambda company, companies: ALL)
    monkeypatch.setattr(payroll, "load_payroll", lambda conn: [_line()])
    monkeypatch.setattr(compass, "load_history", lambda conn: [])
    monkeypatch.setattr(compass, "load_plan", lambda conn: CompPlan())
    monkeypatch.setattr(web_finance, "load_fx", lambda conn, since=None: FX)
    c = TestClient(app)
    c.cookies.set("pusula_session", encode_session(ADMIN))
    c.saved = saved
    return c


def test_api_view_and_plan_validation(client) -> None:
    body = client.get("/api/finance/compass").json()
    assert body["year"] == 2027 and body["people"][0]["person"] == "A"
    ok = {"params": {"tr": 0.3, "de": 0.04, "kidem_step": 0.01, "kidem_cap": 3}, "people": {"A": {"pct": 0, "note": "x"}}}
    assert client.put("/api/finance/compass/plan", json=ok).json() == {"ok": True}
    assert client.saved[0].people["A"].pct == 0
    assert client.put("/api/finance/compass/plan", json={"params": {"tr": 5}}).status_code == 422
