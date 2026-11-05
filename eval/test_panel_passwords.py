from fastapi.testclient import TestClient

from pusula import panel_passwords as pp
from pusula.panel_auth import AuthUser
from pusula.panel_data import Rep
from pusula.web import app as web_app
from pusula.web.auth import encode_session

SECRET = "test-session-secret-that-is-longer-than-32-characters"
EMAIL = "ayse.kar@rexven.com"


def test_hash_dogrulanir_ve_acik_saklanmaz():
    h = pp.hash_password("gizli-sifre-1")
    assert "gizli" not in h and h.startswith("pbkdf2_sha256$")
    assert pp.verify_password("gizli-sifre-1", h)
    assert not pp.verify_password("gizli-sifre-2", h)
    assert not pp.verify_password("x", "bozuk")


def test_uretilen_sifre_karismayan_karakterlerden():
    pw = pp.generate_password()
    assert len(pw) == 12 and pw.count("-") == 2
    assert not set(pw.replace("-", "")) & set("0o1liı")


def test_yeni_sifre_kurallari():
    assert pp.validate_new("kisa") is not None
    assert pp.validate_new(" bosluklu1 ") is not None
    assert pp.validate_new("yeterince-uzun") is None


def _setup(monkeypatch, stored_hash, must_change=True):
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("PUSULA_PASSWORDS_JSON", f'{{"{EMAIL}":"eski-vercel-sifresi"}}')
    state = {"hash": stored_hash, "must": must_change}
    monkeypatch.setattr(
        web_app.panel_passwords, "lookup",
        lambda e: pp.Stored(state["hash"], state["must"], None) if state["hash"] else None,
    )
    monkeypatch.setattr(web_app, "load_rep_by_email", lambda e: Rep("r1", "Ayşe Kar"))

    def fake_set(email, password, *, must_change, by):
        state["hash"], state["must"] = pp.hash_password(password), must_change

    monkeypatch.setattr(web_app.panel_passwords, "set_password", fake_set)
    return state


def test_panel_sifresi_varsa_vercel_sifresi_gecmez(monkeypatch):
    _setup(monkeypatch, pp.hash_password("panel-sifresi"))
    client = TestClient(web_app.app)
    assert client.post("/api/login", json={"email": EMAIL, "password": "eski-vercel-sifresi"}).status_code == 401
    ok = client.post("/api/login", json={"email": EMAIL, "password": "panel-sifresi"})
    assert ok.status_code == 200
    assert client.get("/api/me").json()["must_change"] is True


def test_temsilci_sifresini_degistirir(monkeypatch):
    state = _setup(monkeypatch, pp.hash_password("ilk-sifre-12"))
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser(EMAIL, "rep", "r1", "Ayşe Kar")))
    bad = client.post("/api/me/password", json={"current": "yanlis", "new": "yepyeni-sifre"})
    assert bad.status_code == 400
    ok = client.post("/api/me/password", json={"current": "ilk-sifre-12", "new": "yepyeni-sifre"})
    assert ok.status_code == 200 and state["must"] is False
    assert pp.verify_password("yepyeni-sifre", state["hash"])


def test_yonetici_sifresi_panelden_degismez(monkeypatch):
    _setup(monkeypatch, None)
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser("yusuf.aras@rexven.com", "admin", None, None)))
    assert client.post("/api/me/password", json={"current": "a", "new": "bbbbbbbbbb"}).status_code == 400


def test_sifre_uretmek_yalniz_yonetici(monkeypatch):
    _setup(monkeypatch, None)
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser(EMAIL, "rep", "r1", "Ayşe Kar")))
    assert client.post("/api/users/password", json={"rep_id": "r1"}).status_code == 403
