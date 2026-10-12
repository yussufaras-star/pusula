"""Authentication and public-route checks for the web API."""

from fastapi.testclient import TestClient

from pusula.panel_auth import AuthUser
from pusula.web import cache
from pusula.web.app import app, scope_live
from pusula.web.auth import encode_session
from pusula.web.reports import selected_rep

SECRET = "test-session-secret-that-is-longer-than-32-characters"


def _client_as(user: AuthUser, monkeypatch) -> TestClient:
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    client = TestClient(app)
    client.cookies.set("pusula_session", encode_session(user))
    return client


def test_dashboard_requires_signed_login_cookie(monkeypatch) -> None:
    monkeypatch.setenv(
        "PUSULA_PASSWORDS_JSON",
        '{"yusuf.aras@rexven.com":"test-only-password"}',
    )
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/live").status_code == 401
    login = client.post(
        "/api/login",
        json={"email": "yusuf.aras@rexven.com", "password": "test-only-password"},
    )

    assert login.status_code == 200
    assert login.cookies.get("pusula_session")
    assert "httponly" in login.headers["set-cookie"].lower()
    assert client.get("/api/me").json()["role"] == "admin"


def test_login_error_is_generic(monkeypatch) -> None:
    monkeypatch.setenv(
        "PUSULA_PASSWORDS_JSON",
        '{"yusuf.aras@rexven.com":"test-only-password"}',
    )
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    client = TestClient(app)

    wrong_password = client.post(
        "/api/login",
        json={"email": "yusuf.aras@rexven.com", "password": "wrong"},
    )
    unknown_user = client.post(
        "/api/login",
        json={"email": "unknown@example.com", "password": "wrong"},
    )

    assert wrong_password.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_tampered_session_is_rejected(monkeypatch) -> None:
    user = AuthUser(email="rep@example.com", role="rep", rep_id="r1", full_name="R")
    client = _client_as(user, monkeypatch)
    token = client.cookies.get("pusula_session")
    client.cookies.set("pusula_session", token[:-2] + "xx")

    assert client.get("/api/me").status_code == 401


def test_cron_endpoints_reject_missing_or_wrong_authorization(monkeypatch) -> None:
    monkeypatch.setenv("CRON_SECRET", "test-cron-secret")
    client = TestClient(app)

    for path in ("/api/cron/hourly-nudges", "/api/cron/live-ingest"):
        assert client.get(path).status_code == 401
        wrong = client.get(path, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401


def test_representative_cannot_choose_another_rep_or_open_admin_routes(
    monkeypatch,
) -> None:
    user = AuthUser(email="rep@example.com", role="rep", rep_id="rep-1", full_name="Rep")
    client = _client_as(user, monkeypatch)

    assert selected_rep(user, "another-rep") == "rep-1"
    assert client.get("/api/profiles").status_code == 403
    assert client.get("/api/reps").status_code == 403


def test_representative_sees_only_own_live_card() -> None:
    payload = {
        "team": {"today": {"arama": 10}},
        "reps": [
            {"rep_id": "rep-1", "idle_min": 3},
            {"rep_id": "rep-2", "idle_min": 40},
        ],
    }
    rep = AuthUser(email="a@x", role="rep", rep_id="rep-1", full_name="A")
    admin = AuthUser(email="b@x", role="admin", rep_id=None, full_name=None)

    assert [c["rep_id"] for c in scope_live(payload, rep)["reps"]] == ["rep-1"]
    assert len(scope_live(payload, admin)["reps"]) == 2


def test_cache_expires_and_scopes_by_key() -> None:
    cache.clear()
    cache.put(("reports", "admin", "a@x"), {"private": 1}, 60)

    assert cache.get(("reports", "admin", "a@x")) == {"private": 1}
    assert cache.get(("reports", "rep", "a@x")) is None
    cache.put(("short",), 1, -1)
    assert cache.get(("short",)) is None


def test_manual_ingest_is_admin_only(monkeypatch) -> None:
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    assert TestClient(app).post("/api/ingest").status_code == 401

    rep = AuthUser(email="rep@example.com", role="rep", rep_id="rep-1", full_name="Rep")
    assert _client_as(rep, monkeypatch).post("/api/ingest").status_code == 403


def test_only_admin_marks_leave(monkeypatch) -> None:
    from pusula.panel_data import Rep
    from pusula.web import app as web_app

    calls: list = []
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.setattr(web_app, "load_reps", lambda: [Rep("r1", "Ayşe")])
    monkeypatch.setattr(web_app, "set_leave", lambda *a, **k: calls.append((a, k)))
    body = {"rep_id": "r1", "day": "2026-10-12", "on": True}

    rep = AuthUser(email="ayse@x.com", role="rep", rep_id="r1", full_name="Ayşe")
    assert _client_as(rep, monkeypatch).post("/api/leaves", json=body).status_code == 403

    admin = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)
    client = _client_as(admin, monkeypatch)
    assert client.post("/api/leaves", json=body).status_code == 200
    assert calls and calls[0][1]["by"] == "yusuf.aras@rexven.com"
    assert client.post("/api/leaves", json={**body, "rep_id": "r9"}).status_code == 404
