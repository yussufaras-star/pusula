"""Zoho kullanıcıları ile public.reps karşılaştırması. Yalnız okuma.

Yazma yok: INSERT, UPDATE, DELETE çalışmaz. category ve
category_override okunur, değiştirilmez.

Kullanım: python scripts/reps_discover.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from pusula.config import get_org_id
from pusula.zoho import ZohoAuthError, ZohoCrmError
from pusula.zoho.crm import _request

# Zoho type parametresi -> durum etiketi. Aktif en son yazılır, çakışmada kazanır.
_USER_TYPES: tuple[tuple[str, str], ...] = (
    ("DeletedUsers", "silinmis"),
    ("DeactiveUsers", "pasif"),
    ("ActiveUsers", "aktif"),
)
_PAGE_CAP = 20


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "/")


def _role_name(user: dict[str, Any]) -> str:
    role = user.get("role") or {}
    if isinstance(role, dict):
        return str(role.get("name") or "")
    return ""


def _profile_name(user: dict[str, Any]) -> str:
    profile = user.get("profile") or {}
    if isinstance(profile, dict):
        return str(profile.get("name") or "")
    return ""


def fetch_users(user_type: str) -> list[dict[str, Any]]:
    """Bir Zoho kullanıcı tipinin tüm sayfalarını çeker."""
    page = 1
    found: list[dict[str, Any]] = []
    while page <= _PAGE_CAP:
        response = _request(
            "GET",
            "/crm/v7/users",
            params={"type": user_type, "page": page, "per_page": 200},
        )
        if response.status_code == 204:
            return found
        body = response.json()
        found.extend(body.get("users") or [])
        info = body.get("info") or {}
        if not info.get("more_records"):
            return found
        page += 1
    raise ZohoCrmError(f"{user_type} sayfa sınırı aşıldı ({_PAGE_CAP})")


def load_reps(database_url: str, org_id: str) -> list[dict[str, Any]]:
    """reps satırları. Oturum salt okunur."""
    sql = """
        SELECT rep_id, full_name, email, active, category,
               category_override, zoho_role, zoho_profile, created_at
        FROM public.reps
        WHERE org_id = %s
        ORDER BY full_name
    """
    with psycopg.connect(database_url) as conn:
        conn.read_only = True
        rows = conn.execute(sql, (org_id,)).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "rep_id": str(row[0]),
                "full_name": str(row[1] or ""),
                "email": str(row[2] or ""),
                "active": bool(row[3]),
                "category": str(row[4] or ""),
                "category_override": "" if row[5] is None else str(row[5]),
                "zoho_role": str(row[6] or ""),
                "zoho_profile": str(row[7] or ""),
                "created_at": row[8],
            }
        )
    return out


def _print_section(title: str, headers: list[str], rows: list[list[str]]) -> None:
    print(title)
    print(" | ".join(headers))
    if not rows:
        print("(yok)")
        return
    for row in rows:
        print(" | ".join(row))


def main() -> int:
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("hata: DATABASE_URL yok")
        return 1

    by_id: dict[str, dict[str, Any]] = {}
    try:
        for user_type, label in _USER_TYPES:
            users = fetch_users(user_type)
            print(f"zoho {user_type}: {len(users)}")
            for user in users:
                user_id = str(user.get("id") or "")
                if not user_id:
                    continue
                by_id[user_id] = {
                    "id": user_id,
                    "full_name": str(user.get("full_name") or ""),
                    "email": str(user.get("email") or ""),
                    "status": label,
                    "zoho_status": str(user.get("status") or ""),
                    "role": _role_name(user),
                    "profile": _profile_name(user),
                }
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"zoho kullanicilar cekilemedi: {exc}")
        return 1

    try:
        reps = load_reps(database_url, get_org_id())
    except psycopg.Error as exc:
        print(f"reps okunamadi: {exc}")
        return 1

    rep_ids = {row["rep_id"] for row in reps}
    print(f"reps kayit: {len(reps)}")
    print(f"zoho benzersiz kullanici: {len(by_id)}")
    print("yazma: yok")

    missing = [
        user for user in by_id.values() if user["id"] not in rep_ids
    ]
    missing.sort(key=lambda user: (user["status"], user["full_name"].casefold()))
    _print_section(
        f"ZOHO'DA OLUP REPS'TE OLMAYANLAR ({len(missing)})",
        ["id", "full_name", "email", "durum", "zoho_status", "rol", "profil"],
        [
            [
                _cell(user["id"]),
                _cell(user["full_name"]),
                _cell(user["email"]),
                _cell(user["status"]),
                _cell(user["zoho_status"]),
                _cell(user["role"]),
                _cell(user["profile"]),
            ]
            for user in missing
        ],
    )

    stale: list[list[str]] = []
    for rep in reps:
        zoho = by_id.get(rep["rep_id"])
        if zoho is None:
            durum = "zoho'da yok"
            role = ""
            profile = ""
        elif zoho["status"] == "aktif":
            continue
        else:
            durum = zoho["status"]
            role = zoho["role"]
            profile = zoho["profile"]
        stale.append(
            [
                _cell(rep["rep_id"]),
                _cell(rep["full_name"]),
                _cell(rep["email"]),
                _cell(rep["active"]),
                _cell(rep["category"]),
                _cell(durum),
                _cell(role),
                _cell(profile),
            ]
        )
    _print_section(
        f"REPS'TE OLUP ZOHO'DA PASIF VEYA SILINMIS ({len(stale)})",
        [
            "rep_id",
            "full_name",
            "email",
            "reps_active",
            "category",
            "zoho_durum",
            "rol",
            "profil",
        ],
        stale,
    )

    print("BEYTULLAH ARAS")
    needle = "beytullah"
    zoho_hits = [
        user
        for user in by_id.values()
        if needle in user["full_name"].casefold() or needle in user["email"].casefold()
    ]
    rep_hits = [
        rep
        for rep in reps
        if needle in rep["full_name"].casefold() or needle in rep["email"].casefold()
    ]
    if not zoho_hits:
        print("zoho: yok")
    for user in zoho_hits:
        print(
            "zoho: var"
            f" | id={_cell(user['id'])}"
            f" | full_name={_cell(user['full_name'])}"
            f" | email={_cell(user['email'])}"
            f" | durum={_cell(user['status'])}"
            f" | zoho_status={_cell(user['zoho_status'])}"
            f" | rol={_cell(user['role'])}"
            f" | profil={_cell(user['profile'])}"
        )
    if not rep_hits:
        print("reps: yok")
    for rep in rep_hits:
        print(
            "reps: var"
            f" | rep_id={_cell(rep['rep_id'])}"
            f" | full_name={_cell(rep['full_name'])}"
            f" | email={_cell(rep['email'])}"
            f" | active={_cell(rep['active'])}"
            f" | category={_cell(rep['category'])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
