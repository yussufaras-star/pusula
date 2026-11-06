from fastapi.testclient import TestClient

from pusula import panel_passwords as pp
from pusula.panel_auth import AuthUser
from pusula.panel_data import Rep
from pusula.web import app as web_app
from pusula.web import auth as web_auth
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


def _setup(monkeypatch, stored_hash, must_change=True, role="rep", active=True):
    monkeypatch.setattr(web_auth, "_access", {})
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("PUSULA_PASSWORDS_JSON", f'{{"{EMAIL}":"eski-vercel-sifresi"}}')
    state = {"hash": stored_hash, "must": must_change, "role": role, "active": active, "row": stored_hash is not None or not active}
    monkeypatch.setattr(
        web_app.panel_passwords, "lookup",
        lambda e: pp.Stored(state["hash"], state["must"], None, state["role"], state["active"]) if state["row"] else None,
    )
    monkeypatch.setattr(web_app.panel_passwords, "person", lambda e: {"rep_id": "r9", "full_name": "Kişi", "category": "management"})
    monkeypatch.setattr(web_app, "load_rep_by_email", lambda e: Rep("r1", "Ayşe Kar"))

    def fake_set(email, password, *, must_change, by):
        state["hash"], state["must"], state["row"] = pp.hash_password(password), must_change, True

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


def test_cikarilan_kisi_vercel_sifresiyle_de_giremez(monkeypatch):
    _setup(monkeypatch, None, active=False)
    client = TestClient(web_app.app)
    assert client.post("/api/login", json={"email": EMAIL, "password": "eski-vercel-sifresi"}).status_code == 401


def test_cikarilan_kisinin_acik_oturumu_kapanir(monkeypatch):
    state = _setup(monkeypatch, pp.hash_password("panel-sifresi"))
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser(EMAIL, "rep", "r1", "Ayşe Kar")))
    assert client.get("/api/me").status_code == 200
    state["active"] = False
    web_auth.forget_access(EMAIL)
    assert client.get("/api/me").status_code == 401


def test_eklenen_yonetici_yonetici_olarak_girer_finansi_gormez(monkeypatch):
    monkeypatch.delenv("PUSULA_FINANCE_EMAILS", raising=False)
    _setup(monkeypatch, pp.hash_password("yonetici-sifre"), role="admin")
    client = TestClient(web_app.app)
    assert client.post("/api/login", json={"email": EMAIL, "password": "yonetici-sifre"}).status_code == 200
    me = client.get("/api/me").json()
    assert me["role"] == "admin" and me["finance"] is False


def test_satis_disina_temsilci_rolu_verilmez_ana_yonetici_degismez(monkeypatch):
    _setup(monkeypatch, None)
    monkeypatch.setattr(web_app.panel_passwords, "grant", lambda *a, **k: None)
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser("yusuf.aras@rexven.com", "admin", None, None)))
    assert client.post("/api/users", json={"email": "mentor@rexven.com", "role": "rep"}).status_code == 400
    assert client.post("/api/users", json={"email": "yusuf.aras@rexven.com", "role": "admin"}).status_code == 400
    assert client.post("/api/users/remove", json={"email": "yusuf.aras@rexven.com"}).status_code == 400
    ok = client.post("/api/users", json={"email": "mentor@rexven.com", "role": "admin"})
    assert ok.status_code == 200 and len(ok.json()["password"]) == 12


def test_yonetici_temsilci_gibi_gorur_degisiklik_yapamaz_geri_doner(monkeypatch):
    _setup(monkeypatch, None)
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser("yusuf.aras@rexven.com", "admin", None, None)))
    r = client.post("/api/view-as", json={"email": EMAIL})
    assert r.status_code == 200
    me = client.get("/api/me").json()
    assert me["role"] == "rep" and me["viewer"] == "yusuf.aras@rexven.com"
    assert me["own_password"] is False and me["must_change"] is False
    assert client.post("/api/me/password", json={"current": "a", "new": "bbbbbbbbbb"}).status_code == 403
    assert client.post("/api/activity", json={"page": "canli"}).json()["ok"] == "skip"
    assert client.post("/api/view-as/stop").status_code == 200
    me = client.get("/api/me").json()
    assert me["role"] == "admin" and me["viewer"] is None


def test_temsilci_baskasi_gibi_goremez(monkeypatch):
    _setup(monkeypatch, None)
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser(EMAIL, "rep", "r1", "Ayşe Kar")))
    assert client.post("/api/view-as", json={"email": "serkan.sahin@rexven.com"}).status_code == 403
    assert client.post("/api/view-as/stop").status_code == 400


def test_kullanim_sinyali_yazilir(monkeypatch):
    _setup(monkeypatch, None)
    beats = []
    monkeypatch.setattr(web_app.panel_usage, "heartbeat", lambda email, page: beats.append((email, page)))
    client = TestClient(web_app.app)
    client.cookies.set("pusula_session", encode_session(AuthUser(EMAIL, "rep", "r1", "Ayşe Kar")))
    assert client.post("/api/activity", json={"page": "gorusmeler"}).status_code == 200
    assert beats == [(EMAIL, "gorusmeler")]
    assert client.get("/api/usage").status_code == 403
