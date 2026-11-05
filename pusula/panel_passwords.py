"""Temsilci şifreleri: yönetici panelden ilk şifreyi verir, temsilci kendisi değiştirir.

Şifre açık saklanmaz: PBKDF2-SHA256 (tuz + 310.000 tur). Panelde şifresi olan
temsilci için PUSULA_PASSWORDS_JSON'daki eski şifre artık geçmez. Yönetici
şifresi Vercel'de (PUSULA_PASSWORDS_JSON) kalır; bu tabloya yazılmaz.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pusula.config import get_org_id
from pusula.panel_data import connect

ITERATIONS = 310_000
MIN_LENGTH = 8
# Karışmayan harf ve rakamlar (0/o, 1/l/ı yok): Cliq'ten okununca hatasız yazılsın.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS panel_passwords (
    org_id        text NOT NULL DEFAULT 'rexven',
    email         text NOT NULL,
    password_hash text NOT NULL,
    must_change   boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    text,
    PRIMARY KEY (org_id, email)
);
"""


@dataclass(frozen=True)
class Stored:
    password_hash: str
    must_change: bool
    updated_at: datetime


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def generate_password() -> str:
    """İlk şifre: "abcd-efgh-jk" biçiminde 10 karakter."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def validate_new(password: str) -> str | None:
    """Uygun değilse Türkçe neden; uygunsa None."""
    if len(password) < MIN_LENGTH:
        return f"Şifre en az {MIN_LENGTH} karakter olmalı"
    if password.strip() != password:
        return "Şifre boşlukla başlayıp bitemez"
    return None


def _norm(email: str) -> str:
    return email.strip().lower()


def lookup(email: str) -> Stored | None:
    with connect() as conn:
        if conn.execute("SELECT to_regclass('panel_passwords')").fetchone()[0] is None:
            return None
        row = conn.execute(
            "SELECT password_hash, must_change, updated_at FROM panel_passwords WHERE org_id = %s AND email = %s",
            (get_org_id(), _norm(email)),
        ).fetchone()
    return Stored(str(row[0]), bool(row[1]), row[2]) if row else None


def set_password(email: str, password: str, *, must_change: bool, by: str) -> None:
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        conn.execute(
            """
            INSERT INTO panel_passwords (org_id, email, password_hash, must_change, updated_at, updated_by)
            VALUES (%s, %s, %s, %s, now(), %s)
            ON CONFLICT (org_id, email) DO UPDATE SET
                password_hash = EXCLUDED.password_hash, must_change = EXCLUDED.must_change,
                updated_at = now(), updated_by = EXCLUDED.updated_by
            """,
            (get_org_id(), _norm(email), hash_password(password), must_change, by),
        )
        conn.commit()


def list_reps() -> list[dict[str, Any]]:
    """Aktif satış temsilcileri ve paneldeki şifre durumu."""
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        conn.commit()
        rows = conn.execute(
            """
            SELECT r.rep_id, r.full_name, lower(trim(r.email)), p.updated_at, p.must_change, p.updated_by
            FROM reps r
            LEFT JOIN panel_passwords p ON p.org_id = r.org_id AND p.email = lower(trim(r.email))
            WHERE r.org_id = %s AND r.category = 'sales' AND r.active AND r.email IS NOT NULL
            ORDER BY r.full_name
            """,
            (get_org_id(),),
        ).fetchall()
    return [
        {"rep_id": str(rep_id), "full_name": name, "email": email, "updated_at": updated_at,
         "must_change": must_change, "updated_by": updated_by}
        for rep_id, name, email, updated_at, must_change, updated_by in rows
    ]
