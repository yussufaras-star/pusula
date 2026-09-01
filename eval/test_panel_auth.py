"""panel_auth: yanlis sifre sizdirmaz, yonetici ve temsilci ayrilir."""

from pusula.panel_auth import LOGIN_ERROR, resolve_user
from pusula.panel_data import Rep

_PASSWORDS = {
    "ali@rexven.com": "secret",
    "yusuf.aras@rexven.com": "secret",
}


def test_wrong_password_returns_none() -> None:
    rep = Rep("1", "Ali")
    assert resolve_user("ali@rexven.com", "yanlis", _PASSWORDS, rep) is None


def test_unknown_user_same_as_wrong_password() -> None:
    assert resolve_user("ghost@rexven.com", "secret", _PASSWORDS, None) is None


def test_rep_login_locks_own_id() -> None:
    rep = Rep("1", "Ali")
    user = resolve_user("Ali@Rexven.com", "secret", _PASSWORDS, rep)
    assert user is not None
    assert user.role == "rep"
    assert user.rep_id == "1"
    assert user.full_name == "Ali"


def test_correct_password_without_rep_is_rejected() -> None:
    assert resolve_user("ali@rexven.com", "secret", _PASSWORDS, None) is None


def test_admin_login_without_rep() -> None:
    user = resolve_user(
        "yusuf.aras@rexven.com",
        "secret",
        _PASSWORDS,
        None,
    )
    assert user is not None
    assert user.role == "admin"
    assert user.rep_id is None


def test_login_error_does_not_name_the_user() -> None:
    assert "ali@" not in LOGIN_ERROR
    assert LOGIN_ERROR == "kullanici adi veya sifre hatali"
