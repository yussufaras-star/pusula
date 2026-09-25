"""Beytullah Aras'ı satış temsilcisi olarak reps tablosuna yazar.

Yalnız Zoho'daki aktif kullanıcı:
  6847331000057509001  beytullah.aras@rexven.com
  rol Müşteri Temsilcisi, profil Standard.

Silinmiş ikinci kayda (beytulla.aras@rexven.com) dokunmaz.
Başka reps satırının category, category_override veya active
alanını değiştirmez.

Kullanım: python scripts/add_beytullah_rep.py
.env dosyası otomatik yüklenir; DATABASE_URL gerekir.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.config import get_org_id
from pusula.panel_data import connect

# Zoho ActiveUsers kaydı. Silinmiş destek hesabı bu id değil.
REP_ID = "6847331000057509001"
FULL_NAME = "Beytullah Aras"
EMAIL = "beytullah.aras@rexven.com"
ZOHO_ROLE = "Müşteri Temsilcisi"
ZOHO_PROFILE = "Standard"

_UPSERT = """
    INSERT INTO reps (
        org_id, rep_id, full_name, email, zoho_role, zoho_profile,
        category, category_override, active
    )
    VALUES (
        %(org_id)s, %(rep_id)s, %(full_name)s, %(email)s,
        %(zoho_role)s, %(zoho_profile)s, 'sales', 'sales', true
    )
    ON CONFLICT (org_id, rep_id) DO UPDATE SET
        full_name = EXCLUDED.full_name,
        email = EXCLUDED.email,
        zoho_role = EXCLUDED.zoho_role,
        zoho_profile = EXCLUDED.zoho_profile,
        category = 'sales',
        category_override = 'sales',
        active = true,
        updated_at = now()
"""

_COUNT = """
    SELECT count(*)::int AS toplam,
           count(*) FILTER (WHERE active)::int AS aktif,
           max(created_at) AS en_son
    FROM reps
    WHERE org_id = %s
"""

_BEYTULLAH = """
    SELECT rep_id, full_name, email, active, category, category_override
    FROM reps
    WHERE org_id = %s AND full_name ILIKE %s
    ORDER BY rep_id
"""


def main() -> int:
    load_dotenv()
    org_id = get_org_id()
    with connect() as conn:
        conn.execute(
            _UPSERT,
            {
                "org_id": org_id,
                "rep_id": REP_ID,
                "full_name": FULL_NAME,
                "email": EMAIL,
                "zoho_role": ZOHO_ROLE,
                "zoho_profile": ZOHO_PROFILE,
            },
        )
        toplam, aktif, en_son = conn.execute(_COUNT, (org_id,)).fetchone()
        rows = conn.execute(_BEYTULLAH, (org_id, "%Beytullah%")).fetchall()
    print(f"toplam={toplam} aktif={aktif} en_son={en_son}")
    if not rows:
        print("Beytullah: yok")
        return 1
    for rep_id, full_name, email, active, category, override in rows:
        print(
            f"rep_id={rep_id} full_name={full_name} email={email} "
            f"active={active} category={category} "
            f"category_override={override}"
        )
    matched = [row for row in rows if str(row[0]) == REP_ID]
    if len(matched) != 1:
        print("HATA: aktif Beytullah kaydi yok")
        return 1
    row = matched[0]
    if row[3] is not True or row[4] != "sales" or row[5] != "sales":
        print("HATA: Beytullah sales degil")
        return 1
    print("beytullah: sales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
