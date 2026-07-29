"""Zoho bağlantısını doğrulayan smoke script.

Dört kontrol yapar ve sonucu ekrana basar:
1. Access token alınabiliyor mu (token yazdırılmaz, sadece OK denir).
2. COQL ile Leads modülünden 5 kayıt: id + Full_Name.
3. Users modülünden aktif kullanıcılar: id + full_name + rol + profil.
4. Roller (/crm/v7/settings/roles): id + name + reporting_to.

Kullanım: python scripts/zoho_smoke.py
.env dosyası otomatik yüklenir.
"""

import sys
from itertools import islice
from pathlib import Path

# Script doğrudan çalıştırıldığında pusula paketinin bulunabilmesi için
# repo kökünü path'e ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.zoho import ZohoAuthError, ZohoCrmError, coql, get_access_token
from pusula.zoho.crm import _request


def main() -> int:
    load_dotenv()

    # 1. Token kontrolü. Token asla yazdırılmaz.
    try:
        get_access_token()
    except ZohoAuthError as exc:
        print(f"access token: HATA — {exc}")
        return 1
    print("access token: OK")

    # 2. COQL ile Leads'ten 5 kayıt.
    try:
        leads = list(
            islice(coql("select id, Full_Name from Leads where id is not null"), 5)
        )
    except ZohoCrmError as exc:
        print(f"coql Leads: HATA — {exc}")
        return 1
    print(f"coql Leads: OK ({len(leads)} kayıt)")
    for lead in leads:
        print(f"  {lead.get('id')}  {lead.get('Full_Name')}")

    # 3. Aktif kullanıcılar. Users yanıtının şeması farklı olduğu için
    # (data yerine users anahtarı) doğrudan _request kullanılır.
    try:
        response = _request("GET", "/crm/v7/users", params={"type": "ActiveUsers"})
        users = response.json().get("users", []) if response.status_code != 204 else []
    except ZohoCrmError as exc:
        print(f"aktif kullanıcılar: HATA — {exc}")
        return 1
    print(f"aktif kullanıcılar: OK ({len(users)} kişi)")
    for user in users:
        role = (user.get("role") or {}).get("name")
        profile = (user.get("profile") or {}).get("name")
        print(f"  {user.get('id')}  {user.get('full_name')}  rol={role}  profil={profile}")

    # 4. Roller. Yanıt şeması: {"roles": [...]}; reporting_to null olabilir.
    try:
        response = _request("GET", "/crm/v7/settings/roles")
        roles = response.json().get("roles", []) if response.status_code != 204 else []
    except ZohoCrmError as exc:
        print(f"roller: HATA — {exc}")
        return 1
    print(f"roller: OK ({len(roles)} rol)")
    for role in roles:
        reporting_to = (role.get("reporting_to") or {}).get("name")
        print(f"  {role.get('id')}  {role.get('name')}  reporting_to={reporting_to}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
