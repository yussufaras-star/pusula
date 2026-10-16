"""Ekstre önizleme / içe aktarma uçları (DB katmanı sahte)."""

import base64
from contextlib import nullcontext
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pusula.db.models import FinAccount
from pusula.finance.store import ImportResult
from pusula.panel_auth import AuthUser
from pusula.web import finance as web_finance
from pusula.web.app import app
from pusula.web.auth import encode_session

SECRET = "test-session-secret-that-is-longer-than-32-characters"
ADMIN = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
CSV = "Tarih;Açıklama;Tutar;Bakiye\n01.10.2026;EFT GELEN;1.000,00;5.000,00\n02.10.2026;Kargo;-250,00;4.750,00\n"


def _b64(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return base64.b64encode(data).decode("ascii")


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.delenv("PUSULA_FINANCE_EMAILS", raising=False)
    monkeypatch.setattr(web_finance, "connect", lambda: nullcontext(object()))
    accounts = {
        1: FinAccount(id=1, company_id="tr", name="Garanti", institution="Garanti", currency="TRY", provider="csv"),
        2: FinAccount(
            id=2, company_id="llc", name="Mercury", institution="Mercury", currency="USD",
            provider="mercury", credential_key="LLC",
        ),
    }
    monkeypatch.setattr(web_finance.store, "load_account", lambda conn, i: accounts.get(i))
    monkeypatch.setattr(web_finance.store, "load_companies", lambda conn: [])
    monkeypatch.setattr(web_finance.store, "apply_own_transfers", lambda conn, since: 0)
    monkeypatch.setattr(web_finance.store, "apply_category_rules", lambda conn, since=None: 0)
    client = TestClient(app)
    client.cookies.set("pusula_session", encode_session(ADMIN))
    return client


def test_preview_guesses_mapping_and_counts_duplicates(client, monkeypatch) -> None:
    seen: list = []

    def existing(conn, account_id, ids):
        seen.append(ids)
        return {ids[0]}

    monkeypatch.setattr(web_finance.store, "existing_external_ids", existing)
    body = {"filename": "e.csv", "content_b64": _b64(CSV)}
    data = client.post("/api/finance/accounts/1/statement/preview", json=body).json()
    assert data["header"] == ["Tarih", "Açıklama", "Tutar", "Bakiye"]
    assert data["mapping"] == {"date": 0, "description": 1, "amount": 2, "debit": None, "credit": None, "balance": 3}
    assert data["count"] == 2 and data["duplicates"] == 1
    assert data["last_balance"] == 4750.0
    assert data["sample"][1] == {"booked_at": "2026-10-02", "description": "Kargo", "amount": -250.0, "balance": 4750.0}
    assert len(seen[0]) == 2


def test_import_uses_user_mapping_and_returns_counts(client, monkeypatch) -> None:
    calls: list = []

    def fake_import(conn, account, parsed, companies, update_balance, now):
        calls.append((account.id, [t.amount for t in parsed.txs], update_balance))
        return ImportResult(inserted=1, duplicates=1, balance=None)

    monkeypatch.setattr(web_finance.store, "import_statement", fake_import)
    body = {
        "filename": "e.csv",
        "content_b64": _b64(CSV),
        "mapping": {"date": 0, "description": 1, "amount": 2},
        "invert": True,
        "update_balance": False,
    }
    data = client.post("/api/finance/accounts/1/statement/import", json=body).json()
    assert data == {"inserted": 1, "duplicates": 1, "skipped": 0, "balance": None}
    assert calls == [(1, [Decimal("-1000.00"), Decimal("250.00")], False)]


def test_api_account_rejects_statement(client) -> None:
    body = {"filename": "e.csv", "content_b64": _b64(CSV)}
    response = client.post("/api/finance/accounts/2/statement/preview", json=body)
    assert response.status_code == 400
    assert client.post("/api/finance/accounts/9/statement/preview", json=body).status_code == 404


@pytest.mark.parametrize(
    ("content", "mapping", "message"),
    [
        ("%%%", None, "okunamadı"),
        (_b64(b"\xd0\xcf\x11\xe0" + b"\x00" * 64), None, "XLSX veya CSV"),
        (_b64(CSV), {"date": 0, "amount": 9}, "uyuşmuyor"),
    ],
)
def test_bad_files_get_short_message(client, content, mapping, message) -> None:
    body = {"filename": "e.xls", "content_b64": content, "mapping": mapping}
    response = client.post("/api/finance/accounts/1/statement/preview", json=body)
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_import_without_mapping_is_refused(client) -> None:
    body = {"filename": "e.csv", "content_b64": _b64("Açıklama;Tutar\nx;1\n")}
    response = client.post("/api/finance/accounts/1/statement/import", json=body)
    assert response.status_code == 400
