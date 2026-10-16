"""Finans yazma uçları: doğrulama, beyaz liste, düzeltme kuralı, DB hata eşlemesi (DB sahte)."""

from contextlib import nullcontext
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

from pusula.db.models import FinAccount, FinTransaction
from pusula.panel_auth import AuthUser
from pusula.web import finance as web_finance
from pusula.web.app import app
from pusula.web.auth import encode_session

SECRET = "test-session-secret-that-is-longer-than-32-characters"
ADMIN = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)


class FakeStore:
    def __init__(self) -> None:
        self.updates: list = []
        self.inserts: list = []
        self.tx = FinTransaction(
            id=7, account_id=1, company_id="llc", booked_at=date(2026, 10, 1), amount=Decimal("-100"),
            currency="USD", external_id="x", source="api", category_id="ic_transfer", counterparty_company_id="tr",
        )
        self.account = FinAccount(
            id=1, company_id="llc", name="Mercury", institution="Mercury", currency="USD",
            provider="mercury", credential_key="LLC",
        ).model_dump()

    def load_transaction(self, conn, tx_id):
        return self.tx if tx_id == 7 else None

    def update_row(self, conn, table, row_id, values):
        self.updates.append((table, row_id, values))
        return True

    def insert_row(self, conn, table, values):
        self.inserts.append((table, values))
        return 42

    def load_row(self, conn, table, row_id):
        return self.account if (table, row_id) == ("fin_accounts", 1) else None


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.delenv("PUSULA_FINANCE_EMAILS", raising=False)
    monkeypatch.setattr(web_finance, "connect", lambda: nullcontext(object()))
    fake = FakeStore()
    for name in ("load_transaction", "update_row", "insert_row", "load_row"):
        monkeypatch.setattr(web_finance.store, name, getattr(fake, name))
    client = TestClient(app)
    client.cookies.set("pusula_session", encode_session(ADMIN))
    return client, fake


def test_date_range_rules() -> None:
    today = date(2026, 10, 16)
    assert web_finance.date_range(None, None, today) == (date(2026, 4, 17), today)
    assert web_finance.date_range("2026-01-01", "2026-12-31", today) == (date(2026, 1, 1), date(2026, 12, 31))
    for start, end in (("2026-13-01", None), ("2026-10-10", "2026-10-01"), ("2020-01-01", "2026-01-01")):
        with pytest.raises(HTTPException) as info:
            web_finance.date_range(start, end, today)
        assert info.value.status_code == 400


def test_clearing_intercompany_also_clears_ic_category_and_marks_edit(env) -> None:
    client, fake = env
    response = client.patch("/api/finance/transactions/7", json={"counterparty_company_id": None})
    assert response.status_code == 200
    table, row_id, values = fake.updates[0]
    assert (table, row_id) == ("fin_transactions", 7)
    assert values["counterparty_company_id"] is None and values["category_id"] is None
    assert values["user_edited_at"] is not None


def test_marking_intercompany_sets_category(env) -> None:
    client, fake = env
    fake.tx = fake.tx.model_copy(update={"category_id": None, "counterparty_company_id": None})
    client.patch("/api/finance/transactions/7", json={"counterparty_company_id": "gmbh"})
    assert fake.updates[0][2]["category_id"] == "ic_transfer"
    fake.updates.clear()
    # yalnız kategori değişince şirketler arası alanına dokunulmaz
    client.patch("/api/finance/transactions/7", json={"category_id": "maas"})
    assert set(fake.updates[0][2]) == {"category_id", "user_edited_at"}


def test_transaction_rules(env) -> None:
    client, _ = env
    assert client.patch("/api/finance/transactions/7", json={"counterparty_company_id": "llc"}).status_code == 400
    assert client.patch("/api/finance/transactions/7", json={"counterparty_company_id": "ltd"}).status_code == 422
    assert client.patch("/api/finance/transactions/99", json={"category_id": "maas"}).status_code == 404


def test_account_create_validates_like_db(env) -> None:
    client, fake = env
    api_without_key = {"company_id": "llc", "name": "Ops", "institution": "Mercury", "currency": "USD", "provider": "mercury"}
    response = client.post("/api/finance/accounts", json=api_without_key)
    assert response.status_code == 400 and "credential_key" in response.json()["detail"]
    assert client.post("/api/finance/accounts", json={**api_without_key, "sync_error": "x"}).status_code == 400
    ok = client.post(
        "/api/finance/accounts",
        json={"company_id": "tr", "name": "Garanti", "institution": "Garanti", "currency": "TRY",
              "provider": "csv", "current_balance": "1500.50"},
    )
    assert ok.json() == {"id": 42}
    table, values = fake.inserts[0]
    assert values["current_balance"] == Decimal("1500.50") and "balance_at" in values
    assert "sync_error" not in values


def test_api_account_balance_not_editable(env) -> None:
    client, fake = env
    assert client.patch("/api/finance/accounts/1", json={"current_balance": 5}).status_code == 400
    assert client.patch("/api/finance/accounts/1", json={"name": "Mercury Ops"}).status_code == 200
    assert fake.updates[-1] == ("fin_accounts", 1, {"name": "Mercury Ops"})
    assert client.patch("/api/finance/accounts/2", json={"name": "x"}).status_code == 404


def test_planned_and_decision_validation(env) -> None:
    client, fake = env
    bad = {"company_id": "tr", "direction": "out", "title": "Kira", "amount": 10, "currency": "TRY",
           "due_date": "2026-10-01", "recurrence": "monthly", "recur_until": "2026-09-01"}
    assert client.post("/api/finance/planned", json=bad).status_code == 400
    assert client.post("/api/finance/planned", json={**bad, "recur_until": None}).json() == {"id": 42}
    assert client.post("/api/finance/decisions", json={"title": "X", "status": "bitti"}).status_code == 400
    assert client.post("/api/finance/decisions", json={"title": "Stripe GmbH'ye", "impact_usd": 12000}).json() == {"id": 42}


def test_new_week_must_be_monday(env) -> None:
    client, _ = env
    assert client.post("/api/finance/snapshots", json={"week_date": "2026-10-14"}).status_code == 400


def test_db_errors_map_to_short_messages() -> None:
    cases = [
        (pg_errors.RaiseException(), 409),
        (pg_errors.UniqueViolation(), 409),
        (pg_errors.ForeignKeyViolation(), 400),
        (pg_errors.CheckViolation(), 400),
        (ValueError("Yazılamaz alan: x"), 400),
    ]
    for exc, status in cases:
        with pytest.raises(HTTPException) as info:
            with web_finance.db_errors():
                raise exc
        assert info.value.status_code == status


def test_add_line_takes_side_from_section(env, monkeypatch) -> None:
    from pusula.db.models import FinSnapshot

    client, _ = env
    inserted: list = []
    snap = FinSnapshot(id=5, company_id="tr", week_date=date(2026, 10, 12), fx={"TRY": Decimal("0.025")})
    monkeypatch.setattr(web_finance.store, "load_snapshot", lambda conn, i: snap if i == 5 else None)
    monkeypatch.setattr(web_finance.store, "insert_line", lambda conn, sid, values: inserted.append(values) or 9)
    body = {"section": "equity", "group_name": "Ödenmiş sermaye", "label": "Sermaye", "amount": "100000", "currency": "TRY"}
    assert client.post("/api/finance/snapshots/5/lines", json=body).json() == {"id": 9}
    assert inserted[0]["side"] == "liability" and inserted[0]["section"] == "equity" and inserted[0]["origin"] == "manual"
    # haftaya kilitlenmemiş kur ve bilinmeyen bölüm reddedilir
    assert client.post("/api/finance/snapshots/5/lines", json={**body, "currency": "EUR"}).status_code == 400
    assert client.post("/api/finance/snapshots/5/lines", json={**body, "section": "assets"}).status_code == 422
