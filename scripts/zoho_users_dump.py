"""Zoho aktif kullanıcılarını çekip ekrana basar ve reps tablosuna yazar.

Üç iş yapar:
1. Zoho'dan tüm aktif kullanıcıları çeker; id, full_name, email,
   role.name ve profile.name alanlarını tablo olarak ekrana basar.
2. Kullanıcıları reps tablosuna upsert eder. rep_id çakışmasında
   full_name, email, zoho_role, zoho_profile ve updated_at güncellenir;
   category ve active DEĞİŞMEZ — bunlar elle yönetilen alanlardır
   (bkz. schema.sql'deki reps açıklaması).
3. /crm/v7/settings/roles çıktısını ayrı bir başlıkta listeler
   (id, name, reporting_to).

Kullanım: python scripts/zoho_users_dump.py
.env dosyası otomatik yüklenir; DATABASE_URL gerekir.
"""

import os
import sys
from pathlib import Path
from typing import Any

# Script doğrudan çalıştırıldığında pusula paketinin bulunabilmesi için
# repo kökünü path'e ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from pusula.zoho import ZohoAuthError, ZohoCrmError
from pusula.zoho.crm import _request

# category ve active bilinçli olarak güncellenmez: elle yönetilen alanlar.
_UPSERT_QUERY = """
    INSERT INTO reps (rep_id, full_name, email, zoho_role, zoho_profile)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (rep_id) DO UPDATE SET
        full_name = EXCLUDED.full_name,
        email = EXCLUDED.email,
        zoho_role = EXCLUDED.zoho_role,
        zoho_profile = EXCLUDED.zoho_profile,
        updated_at = now()
"""


def fetch_active_users() -> list[dict[str, Any]]:
    """Zoho'dan aktif kullanıcıları çeker.

    Users yanıtının şeması modül kayıtlarından farklı olduğu için
    (data yerine users anahtarı) doğrudan _request kullanılır.
    """
    response = _request("GET", "/crm/v7/users", params={"type": "ActiveUsers"})
    if response.status_code == 204:
        return []
    return response.json().get("users", [])


def fetch_roles() -> list[dict[str, Any]]:
    """Rol tanımlarını çeker."""
    response = _request("GET", "/crm/v7/settings/roles")
    if response.status_code == 204:
        return []
    return response.json().get("roles", [])


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Basit hizalanmış metin tablosu basar."""
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def upsert_reps(users: list[dict[str, Any]]) -> None:
    """Kullanıcıları reps tablosuna upsert eder (tek transaction)."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL ortam değişkeni tanımlı değil")
    with psycopg.connect(database_url) as conn:
        for user in users:
            conn.execute(
                _UPSERT_QUERY,
                (
                    user.get("id"),
                    user.get("full_name"),
                    user.get("email"),
                    (user.get("role") or {}).get("name"),
                    (user.get("profile") or {}).get("name"),
                ),
            )


def main() -> int:
    load_dotenv()

    # 1. Aktif kullanıcıları çek ve tablo olarak bas.
    try:
        users = fetch_active_users()
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"aktif kullanıcılar çekilemedi: {exc}")
        return 1
    print(f"aktif kullanıcılar ({len(users)} kişi)")
    print_table(
        ["id", "full_name", "email", "role", "profile"],
        [
            [
                str(user.get("id") or ""),
                str(user.get("full_name") or ""),
                str(user.get("email") or ""),
                str((user.get("role") or {}).get("name") or ""),
                str((user.get("profile") or {}).get("name") or ""),
            ]
            for user in users
        ],
    )

    # 2. reps tablosuna upsert. category ve active'e dokunulmaz.
    try:
        upsert_reps(users)
    except (RuntimeError, psycopg.Error) as exc:
        print(f"reps upsert başarısız: {exc}")
        return 1
    print(f"reps upsert: OK ({len(users)} kayıt işlendi)")

    # 3. Roller.
    try:
        roles = fetch_roles()
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"roller çekilemedi: {exc}")
        return 1
    print(f"\nroller ({len(roles)} rol)")
    print_table(
        ["id", "name", "reporting_to"],
        [
            [
                str(role.get("id") or ""),
                str(role.get("name") or ""),
                str((role.get("reporting_to") or {}).get("name") or ""),
            ]
            for role in roles
        ],
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
