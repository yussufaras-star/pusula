"""Finans senkronu: satır hazırlığı, hata yalıtımı, süre bütçesi, secret paketi, uçlar."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from pusula.db.models import FinAccount, FinCompany
from pusula.finance import sync
from pusula.finance.providers.base import AdapterResult, NormTx, ProviderError
from pusula.panel_auth import AuthUser
from pusula.web import finance as web_finance
from pusula.web.app import app
from pusula.web.auth import encode_session

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 10, 16, 8, 0, tzinfo=TZ)
D = Decimal
SECRET = "test-session-secret-that-is-longer-than-32-characters"
COMPANIES = [
    FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", match_patterns=["%rexven llc%"]),
    FinCompany(id="gmbh", name="Rexven GmbH", country="DE", base_currency="EUR", match_patterns=["%rexven gmbh%"]),
]


def _account(**kw) -> FinAccount:
    base = dict(
        id=1, company_id="llc", name="Mercury", institution="Mercury", currency="USD",
        provider="mercury", credential_key="LLC", provider_account_id="a",
    )
    base.update(kw)
    return FinAccount(**base)


def _tx(ext: str, amount: str, **kw) -> NormTx:
    return NormTx(external_id=ext, booked_at=date(2026, 10, 1), amount=D(amount), description=kw.pop("description", "x"), **kw)


def test_prepare_rows_filters_dedupes_and_classifies() -> None:
    result = AdapterResult(
        balance=D("1"),
        transactions=[
            _tx("a", "10.004"),
            _tx("a", "10"),  # aynı kimlik
            _tx("b", "5", currency="EUR"),  # başka para birimi
            _tx("c", "0.001"),  # yuvarlanınca sıfır
            _tx("d", "-300", description="Wire", counterparty="REXVEN GMBH"),
            _tx("e", "7", currency="usd"),
        ],
    )
    rows = sync.prepare_rows(_account(), result, COMPANIES)
    assert [(r.external_id, r.amount, r.counterparty_company_id, r.category_id) for r in rows] == [
        ("a", D("10.00"), None, None),
        ("d", D("-300.00"), "gmbh", "ic_transfer"),
        ("e", D("7.00"), None, None),
    ]


def test_account_since() -> None:
    assert sync.account_since(_account(), NOW) == NOW - timedelta(days=90)
    synced = _account(last_synced_at=NOW - timedelta(days=1))
    assert sync.account_since(synced, NOW) == NOW - timedelta(days=8)
    old = _account(last_synced_at=NOW - timedelta(days=900))
    assert sync.account_since(old, NOW) == NOW - timedelta(days=400)
    assert sync.account_since(synced, NOW, days=30) == NOW - timedelta(days=30)
    assert sync.account_since(synced, NOW, days=5000) == NOW - timedelta(days=400)


def test_one_failure_does_not_stop_others() -> None:
    accounts = [_account(id=1, name="A"), _account(id=2, name="B"), _account(id=3, name="C")]

    def sync_one(account: FinAccount) -> dict:
        if account.id == 2:
            raise ProviderError("Wise: HTTP 401 – Unauthorized")
        if account.id == 3:
            raise RuntimeError("postgres://user:pass@host bağlantı koptu")
        return {"inserted": 4}

    results = sync.run_accounts(accounts, sync_one, budget_sec=40, started=0, clock=lambda: 1)
    assert results[0] == {"account_id": 1, "name": "A", "ok": True, "inserted": 4}
    assert results[1]["error"] == "Wise: HTTP 401 – Unauthorized"
    # beklenmeyen hatada iç ayrıntı sızmaz
    assert results[2]["error"] == "Beklenmeyen hata (RuntimeError)"


def test_budget_skips_remaining_accounts() -> None:
    ticks = iter([0, 10, 50, 60])
    accounts = [_account(id=i, name=str(i)) for i in (1, 2, 3)]
    results = sync.run_accounts(accounts, lambda a: {}, budget_sec=40, started=0, clock=lambda: next(ticks))
    assert [r.get("skipped") for r in results] == [None, None, "sure_doldu"]


def test_provider_secrets_bundle_only_loads_provider_names() -> None:
    environ = {"STRIPE_KEY_LLC": "already"}
    raw = json.dumps(
        {
            "MERCURY_TOKEN_LLC": "m",
            "WISE_API_TOKEN": "w",
            "STRIPE_KEY_LLC": "new",
            "REVOLUT_PRIVATE_KEY_GMBH": "pem",
            "DATABASE_URL": "postgres://x",
            "CLIQ_WEBHOOK_URL": "https://x",
            "WISE_TOKEN_": "bad",
            "github_token": "g",
        }
    )
    loaded = sync.load_provider_secrets(raw, environ)
    assert loaded == ["MERCURY_TOKEN_LLC", "REVOLUT_PRIVATE_KEY_GMBH", "WISE_API_TOKEN"]
    assert environ == {
        "STRIPE_KEY_LLC": "already",
        "MERCURY_TOKEN_LLC": "m",
        "REVOLUT_PRIVATE_KEY_GMBH": "pem",
        "WISE_API_TOKEN": "w",
    }
    assert sync.load_provider_secrets(None, environ) == []
    assert sync.load_provider_secrets("not json", environ) == []


def _admin_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.delenv("PUSULA_FINANCE_EMAILS", raising=False)
    client = TestClient(app)
    admin = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
    client.cookies.set("pusula_session", encode_session(admin))
    return client


def test_manual_sync_has_min_gap(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(web_finance, "run_finance_sync", lambda **kw: calls.append(kw) or {"ok": 1})
    client = _admin_client(monkeypatch)
    assert client.post("/api/finance/sync").json() == {"ok": 1}
    assert client.post("/api/finance/sync", json={"account_id": 3}).status_code == 200
    assert calls == [
        {"account_id": None, "min_gap_sec": 60},
        {"account_id": 3, "min_gap_sec": 60},
    ]


def test_cron_sync_requires_secret(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(web_finance, "run_finance_sync", lambda **kw: calls.append(kw) or {"ok": 1})
    monkeypatch.setenv("CRON_SECRET", "cron-test")
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    client = TestClient(app)
    assert client.get("/api/cron/finance-sync").status_code == 401
    assert client.get("/api/cron/finance-sync", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.get("/api/cron/finance-sync", headers={"Authorization": "Bearer cron-test"})
    assert ok.status_code == 200 and calls == [{}]
