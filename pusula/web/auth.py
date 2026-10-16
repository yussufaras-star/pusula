"""İmzalı oturum çerezi, giriş ve yetki bağımlılıkları."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import Depends, Header, HTTPException, Request

from pusula.panel_auth import (
    FINANCE_EMAILS_ENV,
    AuthUser,
    can_view_finance,
    parse_finance_emails,
)

logger = logging.getLogger(__name__)

COOKIE_NAME = "pusula_session"
SESSION_SECONDS = 12 * 60 * 60


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
        "exp": int(time.time()) + SESSION_SECONDS,
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
    return AuthUser(
        email=email,
        role=role,
        rep_id=rep_id if isinstance(rep_id, str) else None,
        full_name=name if isinstance(name, str) else None,
    )


def current_user(request: Request) -> AuthUser:
    user = decode_session(request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Oturum gerekli")
    return user


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
