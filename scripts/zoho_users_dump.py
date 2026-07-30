"""Zoho aktif kullanıcılarını çekip ekrana basar ve reps tablosuna yazar.

Yaptıkları:
1. Zoho'dan tüm aktif kullanıcıları çeker; id, full_name, email,
   role.name ve profile.name alanlarını tablo olarak ekrana basar.
2. Kullanıcıları reps tablosuna upsert eder. category TÜRETİLMİŞ
   alandır, her sync'te yeniden hesaplanır:
     coalesce(category_override, role_category_map[zoho_role], 'other')
   Çakışmada full_name, email, zoho_role, zoho_profile, category ve
   updated_at güncellenir; category_override ve active'e ASLA
   dokunulmaz — elle yönetilen alanlardır.
3. /crm/v7/settings/roles çıktısını ayrı bir başlıkta listeler
   (id, name, reporting_to).
4. İki uyarı bölümü basar: haritada olmayan roller ('other' sayılır)
   ve bu sync'te category'si değişenler (eski -> yeni). Sessiz
   değişiklik olmaz.

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

from pusula.config import get_org_id
from pusula.zoho import ZohoAuthError, ZohoCrmError
from pusula.zoho.crm import _request

# category türetilmiş alandır: haritadan hesaplanır, override ezer.
# category_override ve active bilinçli olarak güncellenmez.
_UPSERT_QUERY = """
    INSERT INTO reps (org_id, rep_id, full_name, email, zoho_role, zoho_profile, category)
    VALUES (
        %(org_id)s, %(rep_id)s, %(full_name)s, %(email)s, %(zoho_role)s, %(zoho_profile)s,
        coalesce(
            (SELECT category FROM role_category_map
             WHERE org_id = %(org_id)s AND zoho_role = %(zoho_role)s),
            'other'
        )
    )
    ON CONFLICT (org_id, rep_id) DO UPDATE SET
        full_name = EXCLUDED.full_name,
        email = EXCLUDED.email,
        zoho_role = EXCLUDED.zoho_role,
        zoho_profile = EXCLUDED.zoho_profile,
        category = coalesce(reps.category_override, EXCLUDED.category),
        updated_at = now()
"""

# reps'te görülen ama haritada karşılığı olmayan roller (kişi sayısıyla).
_UNMAPPED_ROLES_QUERY = """
    SELECT r.zoho_role, count(*)
    FROM reps r
    LEFT JOIN role_category_map m
        ON m.org_id = r.org_id AND m.zoho_role = r.zoho_role
    WHERE r.org_id = %s AND r.zoho_role IS NOT NULL AND m.zoho_role IS NULL
    GROUP BY r.zoho_role
    ORDER BY count(*) DESC, r.zoho_role
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


def upsert_reps(
    users: list[dict[str, Any]],
) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, int]]]:
    """Kullanıcıları reps tablosuna upsert eder (tek transaction).

    category her upsert'te yeniden hesaplanır:
    coalesce(category_override, role_category_map lookup, 'other').

    Dönüş: (category_changes, unmapped_roles).
    - category_changes: bu sync'te category'si değişenler,
      (rep_id, full_name, eski, yeni).
    - unmapped_roles: haritada karşılığı olmayan roller, (zoho_role, kişi).
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL ortam değişkeni tanımlı değil")
    org_id = get_org_id()
    with psycopg.connect(database_url) as conn:
        before = dict(
            conn.execute(
                "SELECT rep_id, category FROM reps WHERE org_id = %s", (org_id,)
            ).fetchall()
        )
        for user in users:
            conn.execute(
                _UPSERT_QUERY,
                {
                    "org_id": org_id,
                    "rep_id": user.get("id"),
                    "full_name": user.get("full_name"),
                    "email": user.get("email"),
                    "zoho_role": (user.get("role") or {}).get("name"),
                    "zoho_profile": (user.get("profile") or {}).get("name"),
                },
            )
        after = conn.execute(
            "SELECT rep_id, full_name, category FROM reps"
            " WHERE org_id = %s ORDER BY full_name",
            (org_id,),
        ).fetchall()
        category_changes = [
            (rep_id, full_name, before[rep_id], category)
            for rep_id, full_name, category in after
            if rep_id in before and before[rep_id] != category
        ]
        unmapped_roles = conn.execute(_UNMAPPED_ROLES_QUERY, (org_id,)).fetchall()
    return category_changes, unmapped_roles


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

    # 2. reps tablosuna upsert. category haritadan hesaplanır;
    # category_override ve active'e dokunulmaz.
    try:
        category_changes, unmapped_roles = upsert_reps(users)
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

    # 4a. Haritada olmayan roller: 'other' sayıldılar, sessiz kalmasın.
    print("\nHARİTADA OLMAYAN ROLLER")
    if unmapped_roles:
        print_table(
            ["zoho_role", "kişi"],
            [[role, str(count)] for role, count in unmapped_roles],
        )
        print(
            "Bu rollerdeki kişiler 'other' olarak işaretlendi"
            " (category_override doluysa o geçerli)."
        )
    else:
        print("yok")

    # 4b. Bu sync'te kategorisi değişenler: eski -> yeni.
    print("\nKATEGORİ DEĞİŞENLER")
    if category_changes:
        for rep_id, full_name, old_category, new_category in category_changes:
            print(f"  {full_name} ({rep_id}): {old_category} -> {new_category}")
    else:
        print("yok")

    return 0


if __name__ == "__main__":
    sys.exit(main())
