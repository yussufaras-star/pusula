"""Tek lead için kimlik zenginleştirme teşhisi.

Zoho'dan ham alanları, normalize edilmiş değerleri ve identities
tablosundaki satırları yan yana basar.

Kullanım:
    python scripts/verify_lead_enrichment.py --lead-id 6847331000049773012

.env otomatik yüklenir; DATABASE_URL ve Zoho kimlik bilgileri gerekir.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_email, normalize_phone
from pusula.ingest.lead_identity import _LEAD_FIELDS, _as_str, _fetch_leads
from pusula.zoho import ZohoAuthError, ZohoCrmError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tek lead için zenginleştirme teşhisi (Zoho + identities)."
    )
    parser.add_argument("--lead-id", required=True, help="Zoho Leads id")
    args = parser.parse_args()
    lead_id = args.lead_id.strip()
    if not lead_id:
        print("--lead-id boş")
        return 1

    load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL ortam değişkeni tanımlı değil")
        return 1

    print(f"lead_id: {lead_id}")
    print(f"COQL alanları: {', '.join(_LEAD_FIELDS)}")
    print()

    try:
        records = list(_fetch_leads([lead_id]))
    except (ZohoAuthError, ZohoCrmError, OSError, ValueError) as exc:
        print(f"Zoho çekimi başarısız: {exc}")
        return 1

    if not records:
        print("Zoho: kayıt dönmedi (id yok veya erişilemiyor)")
        return 1

    record = records[0]
    raw_phone = _as_str(record.get("Phone"))
    raw_mobile = _as_str(record.get("Mobile"))
    raw_email = _as_str(record.get("Email"))
    raw_secondary = _as_str(record.get("Secondary_Email"))

    norm_phone = normalize_phone(raw_phone) if raw_phone else None
    norm_mobile = normalize_phone(raw_mobile) if raw_mobile else None
    norm_email = normalize_email(raw_email) if raw_email else None
    norm_secondary = normalize_email(raw_secondary) if raw_secondary else None

    print("Zoho ham -> normalize")
    print(f"  Phone:           {raw_phone!r} -> {norm_phone!r}")
    print(f"  Mobile:          {raw_mobile!r} -> {norm_mobile!r}")
    print(f"  Email:           {raw_email!r} -> {norm_email!r}")
    print(f"  Secondary_Email: {raw_secondary!r} -> {norm_secondary!r}")
    print(f"  Lead_Source:     {_as_str(record.get('Lead_Source'))!r}")
    print(f"  Lead_Status:     {_as_str(record.get('Lead_Status'))!r}")
    print()

    with client.transaction() as conn:
        client.load_blocklist(conn)
        blocked = []
        for id_type, value in (
            ("phone", norm_phone),
            ("phone", norm_mobile),
            ("email", norm_email),
            ("email", norm_secondary),
            ("zoho_lead", lead_id),
        ):
            if value is None:
                continue
            if client.is_identifier_blocked(id_type, value):
                blocked.append((id_type, value))
        rows = conn.execute(
            """
            SELECT id_type, id_value, thread_id, first_seen_at, last_seen_at
            FROM identities
            WHERE org_id = %s
              AND (
                    (id_type = 'zoho_lead' AND id_value = %s)
                 OR (id_type = 'phone' AND id_value = ANY(%s))
                 OR (id_type = 'email' AND id_value = ANY(%s))
              )
            ORDER BY id_type, id_value
            """,
            (
                get_org_id(),
                lead_id,
                [v for v in (norm_phone, norm_mobile) if v],
                [v for v in (norm_email, norm_secondary) if v],
            ),
        ).fetchall()

    if blocked:
        print("blocklist'te:")
        for id_type, value in blocked:
            print(f"  {id_type}={value}")
        print()

    print(f"identities ({len(rows)} satir):")
    if not rows:
        print("  (yok)")
    for id_type, id_value, thread_id, first_seen, last_seen in rows:
        print(
            f"  {id_type}={id_value} thread={thread_id} "
            f"first={first_seen} last={last_seen}"
        )

    expected = [
        ("phone", norm_phone),
        ("phone", norm_mobile),
        ("email", norm_email),
        ("email", norm_secondary),
        ("zoho_lead", lead_id),
    ]
    present = {(r[0], r[1]) for r in rows}
    missing = [
        (t, v) for t, v in expected if v is not None and (t, v) not in present
    ]
    print()
    if missing:
        print("eksik (normalize edildi ama identities'te yok):")
        for id_type, value in missing:
            print(f"  {id_type}={value}")
    else:
        print("eksik yok: beklenen kimlikler identities'te mevcut.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
