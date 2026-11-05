"""Panel kullanıcıları: yönetici ekler / çıkarır, ilk şifreyi verir; kişi şifresini değiştirir.

Rol: "rep" (satış temsilcisi, yalnız kendi verisi) ya da "admin" (her şey; finans
ayrı izinle). Satış temsilcileri listede kendiliğinden görünür; başka bir Zoho
kullanıcısı yalnız yönetici olarak eklenir. Çıkarılan kişinin (active=false)
girişi ve açık oturumu kapanır, Vercel'deki eski şifresi de geçmez.

Şifre açık saklanmaz: PBKDF2-SHA256 (tuz + 310.000 tur). Panelde şifresi olan
kişi için PUSULA_PASSWORDS_JSON'daki eski şifre artık geçmez. Ana yönetici
(ADMIN_EMAIL) Vercel'de kalır; bu tabloya yazılmaz, çıkarılamaz.
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
ROLES = ("rep", "admin")
# Karışmayan harf ve rakamlar (0/o, 1/l/ı yok): Cliq'ten okununca hatasız yazılsın.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS panel_passwords (
    org_id        text NOT NULL DEFAULT 'rexven',
    email         text NOT NULL,
    password_hash text,
    must_change   boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    text,
    PRIMARY KEY (org_id, email)
);
ALTER TABLE panel_passwords ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE panel_passwords ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'rep';
ALTER TABLE panel_passwords ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
"""


@dataclass(frozen=True)
class Stored:
    password_hash: str | None
    must_change: bool
    updated_at: datetime | None
    role: str = "rep"
    active: bool = True


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
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
            """
            SELECT password_hash, must_change, updated_at,
                   to_jsonb(p)->>'role', (to_jsonb(p)->>'active')::boolean
            FROM panel_passwords p WHERE org_id = %s AND email = %s
            """,
            (get_org_id(), _norm(email)),
        ).fetchone()
    if not row:
        return None
    return Stored(row[0], bool(row[1]), row[2], row[3] or "rep", True if row[4] is None else bool(row[4]))


def person(email: str) -> dict[str, Any] | None:
    """Zoho kullanıcısı (reps tablosu, her kategori)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT rep_id, full_name, category FROM reps WHERE org_id = %s AND lower(trim(email)) = %s AND active",
            (get_org_id(), _norm(email)),
        ).fetchone()
    return {"rep_id": str(row[0]), "full_name": row[1], "category": row[2]} if row else None


def set_password(email: str, password: str, *, must_change: bool, by: str) -> None:
    """Şifreyi yazar; rol ve erişim durumu değişmez."""
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


def grant(email: str, role: str, password: str, *, by: str) -> None:
    """Erişim verir (ya da geri açar) ve ilk şifreyi yazar."""
    if role not in ROLES:
        raise ValueError(role)
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        conn.execute(
            """
            INSERT INTO panel_passwords (org_id, email, password_hash, must_change, role, active, updated_at, updated_by)
            VALUES (%s, %s, %s, true, %s, true, now(), %s)
            ON CONFLICT (org_id, email) DO UPDATE SET
                password_hash = EXCLUDED.password_hash, must_change = true, role = EXCLUDED.role,
                active = true, updated_at = now(), updated_by = EXCLUDED.updated_by
            """,
            (get_org_id(), _norm(email), hash_password(password), role, by),
        )
        conn.commit()


def revoke(email: str, *, by: str) -> None:
    """Erişimi kapatır; şifre silinir (geri eklenirse yeni şifre verilir)."""
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        conn.execute(
            """
            INSERT INTO panel_passwords (org_id, email, password_hash, must_change, active, updated_at, updated_by)
            VALUES (%s, %s, NULL, false, false, now(), %s)
            ON CONFLICT (org_id, email) DO UPDATE SET
                password_hash = NULL, must_change = false, active = false,
                updated_at = now(), updated_by = EXCLUDED.updated_by
            """,
            (get_org_id(), _norm(email), by),
        )
        conn.commit()


def list_users(admin_email: str) -> dict[str, list[dict[str, Any]]]:
    """Kullanıcılar (satış temsilcileri + paneldeki kayıtlar) ve eklenebilecek Zoho kullanıcıları."""
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        conn.commit()
        rows = conn.execute(
            """
            SELECT lower(trim(r.email)), r.rep_id, r.full_name, r.category,
                   p.password_hash IS NOT NULL, p.updated_at, p.must_change, p.role, p.active
            FROM reps r
            LEFT JOIN panel_passwords p ON p.org_id = r.org_id AND p.email = lower(trim(r.email))
            WHERE r.org_id = %s AND r.active AND r.email IS NOT NULL AND trim(r.email) <> ''
            ORDER BY r.full_name
            """,
            (get_org_id(),),
        ).fetchall()
    users: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for email, rep_id, name, category, has_hash, updated_at, must_change, role, active in rows:
        if email in seen:
            continue
        seen.add(email)
        base = {"email": email, "rep_id": str(rep_id), "full_name": name, "category": category}
        if email == admin_email:
            users.append({**base, "role": "admin", "active": True, "ana": True, "panel": False,
                          "updated_at": None, "must_change": False})
        elif category == "sales" or (role is not None and active):
            users.append({**base, "role": role or "rep", "active": True if active is None else bool(active),
                          "ana": False, "panel": bool(has_hash), "updated_at": updated_at,
                          "must_change": bool(must_change)})
        else:
            # Satış dışı Zoho kullanıcısı: yalnız yönetici olarak eklenebilir (çıkarılmışsa yeniden).
            candidates.append(base)
    users.sort(key=lambda u: (not u["ana"], u["role"] != "admin", not u["active"], u["full_name"] or ""))
    return {"users": users, "candidates": candidates}
