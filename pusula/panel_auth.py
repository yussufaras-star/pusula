"""Panel girisi: sifre st.secrets'te, oturum session_state'te.

Harici kimlik kutuphanesi yok. Yanlis sifre ile bilinmeyen
kullanici ayni mesaji gorur; e-posta varligi sizdirilmaz.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pusula.panel_data import Rep

ADMIN_EMAIL = "yusuf.aras@rexven.com"
LOGIN_ERROR = "kullanici adi veya sifre hatali"

AuthRole = Literal["admin", "rep"]


@dataclass(frozen=True)
class AuthUser:
    email: str
    role: AuthRole
    rep_id: str | None
    full_name: str | None


def normalize_login_email(raw: str) -> str:
    return raw.strip().lower()


def password_matches(given: str, expected: str) -> bool:
    given_b = given.encode("utf-8")
    expected_b = expected.encode("utf-8")
    if len(given_b) != len(expected_b):
        hmac.compare_digest(given_b, given_b)
        return False
    return hmac.compare_digest(given_b, expected_b)


def lookup_secret_password(passwords: Mapping[str, Any], email: str) -> str | None:
    key = normalize_login_email(email)
    if not key:
        return None
    for stored_key, stored in passwords.items():
        if normalize_login_email(str(stored_key)) != key:
            continue
        if stored is None:
            return None
        text = str(stored)
        if not text or text == "...":
            return None
        return text
    return None


def resolve_user(
    email: str,
    password: str,
    passwords: Mapping[str, Any],
    rep: Rep | None,
) -> AuthUser | None:
    """Sifre uymazsa veya temsilci bulunamazsa None.

    Once sifre kontrol edilir; temsilci sorgusu ancak sifre dogruysa
    anlam tasir. Cagiran, sifre yanlissa rep'i None verebilir.
    """
    stored = lookup_secret_password(passwords, email)
    if stored is None or not password_matches(password, stored):
        return None
    normalized = normalize_login_email(email)
    if normalized == ADMIN_EMAIL:
        return AuthUser(
            email=normalized,
            role="admin",
            rep_id=None,
            full_name=None,
        )
    if rep is None:
        return None
    return AuthUser(
        email=normalized,
        role="rep",
        rep_id=rep.rep_id,
        full_name=rep.full_name,
    )
