"""Finans erişimi: yalnız yönetici, liste tanımlıysa yalnız listedeki yönetici."""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from pusula.panel_auth import AuthUser, can_view_finance, parse_finance_emails
from pusula.web.app import app
from pusula.web.auth import encode_session, finance_user

SECRET = "test-session-secret-that-is-longer-than-32-characters"
ADMIN = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
OTHER_ADMIN = AuthUser(email="cfo@rexven.com", role="admin", rep_id=None, full_name=None)
REP = AuthUser(email="rep@rexven.com", role="rep", rep_id="r1", full_name="Rep")

SCHEMA = Path(__file__).resolve().parents[1] / "pusula" / "db" / "schema.sql"


def _client_as(user: AuthUser, monkeypatch) -> TestClient:
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    client = TestClient(app)
    client.cookies.set("pusula_session", encode_session(user))
    return client


def _finance_routes() -> list[APIRoute]:
    return [
        r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/finance")
    ]


def _calls(dependant) -> set:
    found = {dependant.call}
    for sub in dependant.dependencies:
        found |= _calls(sub)
    return found


def test_parse_finance_emails() -> None:
    assert parse_finance_emails(None) is None
    assert parse_finance_emails("") is None
    assert parse_finance_emails(" , ") is None
    assert parse_finance_emails(" A@Rexven.com, b@rexven.com ,") == frozenset(
        {"a@rexven.com", "b@rexven.com"}
    )


def test_rep_never_sees_finance() -> None:
    assert not can_view_finance(REP, None)
    assert not can_view_finance(REP, frozenset({"rep@rexven.com"}))


def test_admin_list_restricts_admins() -> None:
    assert can_view_finance(ADMIN, None)
    allowed = frozenset({"yusuf.aras@rexven.com"})
    assert can_view_finance(ADMIN, allowed)
    assert not can_view_finance(OTHER_ADMIN, allowed)


def test_every_finance_route_is_gated_on_server() -> None:
    routes = _finance_routes()
    assert routes
    for route in routes:
        assert finance_user in _calls(route.dependant), route.path


@pytest.mark.parametrize("user", [REP, OTHER_ADMIN])
def test_forbidden_users_get_403_everywhere(user: AuthUser, monkeypatch) -> None:
    monkeypatch.setenv("PUSULA_FINANCE_EMAILS", "yusuf.aras@rexven.com")
    client = _client_as(user, monkeypatch)
    for route in _finance_routes():
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods:
            response = client.request(method, path)
            assert response.status_code == 403, (method, path)


def test_anonymous_gets_401(monkeypatch) -> None:
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    assert TestClient(app).get("/api/finance/companies").status_code == 401


def test_allowed_admin_passes_gate(monkeypatch) -> None:
    # Kapıdan geçer; veritabanı yoksa 503 döner (403 değil).
    monkeypatch.setenv("PUSULA_FINANCE_EMAILS", "yusuf.aras@rexven.com")
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _client_as(ADMIN, monkeypatch)
    assert client.get("/api/finance/companies").status_code == 503


def test_me_reports_finance_flag(monkeypatch) -> None:
    monkeypatch.setenv("PUSULA_FINANCE_EMAILS", "yusuf.aras@rexven.com")
    assert _client_as(ADMIN, monkeypatch).get("/api/me").json()["finance"] is True
    assert _client_as(OTHER_ADMIN, monkeypatch).get("/api/me").json()["finance"] is False
    assert _client_as(REP, monkeypatch).get("/api/me").json()["finance"] is False


def test_schema_has_finance_tables_and_lock_guards() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    for table in (
        "fin_companies",
        "fin_accounts",
        "fin_categories",
        "fin_transactions",
        "fin_fx_rates",
        "fin_planned_items",
        "fin_snapshots",
        "fin_snapshot_lines",
        "fin_decisions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql
    assert "UNIQUE (account_id, external_id)" in sql
    assert "CREATE TRIGGER fin_snapshot_lines_guard" in sql
    assert "CREATE TRIGGER fin_snapshots_guard" in sql


def test_schema_stores_no_bank_credentials() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    finance = sql[sql.index("CREATE TABLE IF NOT EXISTS fin_companies") :]
    columns = re.findall(r"^\s{4}([a-z_]+)\s+(?:text|bytea|jsonb)", finance, re.M)
    for column in columns:
        assert not re.search(r"token|secret|password|api_key", column), column
