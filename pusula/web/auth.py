"""İmzalı oturum çerezi, giriş ve yetki bağımlılıkları."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from pusula.panel_auth import (
    ADMIN_EMAIL,
    FINANCE_EMAILS_ENV,
    AuthUser,
    can_view_finance,
    parse_finance_emails,
)

logger = logging.getLogger(__name__)

COOKIE_NAME = "pusula_session"
SESSION_SECONDS = 12 * 60 * 60
# "Temsilci gibi gör" oturumu kısa tutulur.
VIEW_SECONDS = 2 * 60 * 60
# Görüntüleme sırasında yalnız bu değişiklik uçları açık (çıkış, geri dönüş, sinyal yok sayılır).
VIEW_WRITE_OK = ("/api/logout", "/api/view-as/stop", "/api/activity")


def _session_secret() -> bytes:
    value = os.environ.get("PUSULA_SESSION_SECRET", "")
    if len(value) < 32:
        raise HTTPException(
            status_code=503,
            detail="PUSULA_SESSION_SECRET en az 32 karakter olmalı",
        )
    return value.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_session(user: AuthUser) -> str:
    payload = {
        "email": user.email,
        "role": user.role,
        "rep_id": user.rep_id,
        "name": user.full_name,
        "viewer": user.viewer,
        "exp": int(time.time()) + (VIEW_SECONDS if user.viewer else SESSION_SECONDS),
    }
    data = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_session_secret(), data.encode("ascii"), hashlib.sha256)
    return f"{data}.{_b64(signature.digest())}"


def decode_session(token: str | None) -> AuthUser | None:
    if not token or "." not in token:
        return None
    encoded_data, encoded_signature = token.split(".", 1)
    expected = hmac.new(
        _session_secret(),
        encoded_data.encode("ascii", errors="ignore"),
        hashlib.sha256,
    ).digest()
    try:
        supplied = _unb64(encoded_signature)
        payload = json.loads(_unb64(encoded_data))
    except (ValueError, json.JSONDecodeError):
        return None
    if not hmac.compare_digest(expected, supplied):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < int(time.time()):
        return None
    role = payload.get("role")
    email = payload.get("email")
    if role not in ("admin", "rep") or not isinstance(email, str):
        return None
    rep_id = payload.get("rep_id")
    if role == "rep" and not isinstance(rep_id, str):
        return None
    name = payload.get("name")
    viewer = payload.get("viewer")
    return AuthUser(
        email=email,
        role=role,
        rep_id=rep_id if isinstance(rep_id, str) else None,
        full_name=name if isinstance(name, str) else None,
        viewer=viewer if isinstance(viewer, str) and role == "rep" else None,
    )


# Çıkarılan / rolü değişen kullanıcının açık oturumu en geç bu kadar sürede kapanır.
ACCESS_TTL = 60.0
_access: dict[str, tuple[float, Any]] = {}


def _access_state(email: str) -> Any:
    """panel_passwords kaydı (yoksa None); kısa önbellekli. Veritabanı yoksa / hata olursa None."""
    hit = _access.get(email)
    if hit is not None and time.monotonic() - hit[0] < ACCESS_TTL:
        return hit[1]
    if not (os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")):
        return None
    try:
        from pusula import panel_passwords

        state = panel_passwords.lookup(email)
    except Exception:  # noqa: BLE001 — erişim kontrolü yüzünden tüm panel düşmesin
        logger.exception("erişim durumu okunamadı")
        return None
    _access[email] = (time.monotonic(), state)
    return state


def forget_access(email: str) -> None:
    _access.pop(email.strip().lower(), None)


def current_user(request: Request) -> AuthUser:
    user = decode_session(request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Oturum gerekli")
    if user.email != ADMIN_EMAIL:
        state = _access_state(user.email)
        if state is not None and (not state.active or state.role != user.role):
            raise HTTPException(status_code=401, detail="Oturum gerekli")
    if user.viewer is not None:
        if not viewer_is_admin(user.viewer):
            raise HTTPException(status_code=401, detail="Oturum gerekli")
        if request.method not in ("GET", "HEAD") and request.url.path not in VIEW_WRITE_OK:
            raise HTTPException(status_code=403, detail="Temsilci gibi görüntülerken değişiklik yapılamaz")
    return user


def viewer_is_admin(email: str) -> bool:
    """Görüntüleyen hâlâ yönetici mi (ana yönetici ya da panelde etkin yönetici)."""
    if email == ADMIN_EMAIL:
        return True
    state = _access_state(email)
    return state is not None and state.active and state.role == "admin"


def admin_user(user: AuthUser = Depends(current_user)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Yönetici yetkisi gerekli")
    return user


def finance_allowed(user: AuthUser) -> bool:
    return can_view_finance(user, parse_finance_emails(os.environ.get(FINANCE_EMAILS_ENV)))


def finance_user(user: AuthUser = Depends(current_user)) -> AuthUser:
    """Tüm /api/finance uçlarının sunucu tarafı kapısı."""
    if not finance_allowed(user):
        raise HTTPException(status_code=403, detail="Finans yetkisi gerekli")
    return user


def passwords() -> dict[str, str]:
    raw = os.environ.get("PUSULA_PASSWORDS_JSON")
    if not raw:
        raise HTTPException(
            status_code=503,
            detail="PUSULA_PASSWORDS_JSON ortam değişkeni yapılandırılmamış",
        )
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("PUSULA_PASSWORDS_JSON geçerli JSON değil")
        raise HTTPException(status_code=503, detail="Giriş yapılandırması geçersiz") from exc
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=503, detail="Giriş yapılandırması geçersiz")
    return {str(key): str(value) for key, value in values.items()}


def require_cron(authorization: str | None = Header(default=None)) -> None:
    """Zamanlanmış tetikleyiciler: 'Authorization: Bearer <CRON_SECRET>'."""
    expected = os.environ.get("CRON_SECRET")
    if not expected:
        raise HTTPException(status_code=503, detail="CRON_SECRET yapılandırılmamış")
    if not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="Cron yetkisi geçersiz")
