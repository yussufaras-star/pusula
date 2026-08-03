"""Mevcut zoho_lead kimlikleri için leads tablosunu backfill eder.

identities'teki tum zoho_lead id'lerini alir, sync_lead_identities ile
status / assigned_at / owner / source yazar.

Kullanım:
    python scripts/backfill_leads.py

.env otomatik yüklenir; DATABASE_URL ve Zoho kimlik bilgileri gerekir.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.config import get_org_id
from pusula.db import client
from pusula.ingest.lead_identity import sync_lead_identities

_CHUNK = 500


def main() -> int:
    load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL ortam degiskeni tanimli degil")
        return 1

    org_id = get_org_id()
    with client.transaction() as conn:
        rows = conn.execute(
            """
            SELECT id_value FROM identities
            WHERE org_id = %s AND id_type = 'zoho_lead'
            ORDER BY id_value
            """,
            (org_id,),
        ).fetchall()
    lead_ids = [str(r[0]) for r in rows]
    print(f"backfill: {len(lead_ids)} zoho_lead (org={org_id})")
    if not lead_ids:
        return 0

    totals = {
        "processed": 0,
        "phones_added": 0,
        "emails_added": 0,
        "leads_written": 0,
        "errors": 0,
    }
    for i in range(0, len(lead_ids), _CHUNK):
        chunk = set(lead_ids[i : i + _CHUNK])
        stats = sync_lead_identities(chunk)
        for key in totals:
            totals[key] += stats.get(key, 0)
        print(
            f"  chunk {i // _CHUNK + 1}: "
            f"islenen={stats.get('processed', 0)} "
            f"yazilan={stats.get('leads_written', 0)} "
            f"hata={stats.get('errors', 0)}"
        )

    print(
        f"toplam: islenen={totals['processed']} "
        f"yazilan={totals['leads_written']} "
        f"hata={totals['errors']} "
        f"+telefon={totals['phones_added']} "
        f"+eposta={totals['emails_added']}"
    )

    with client.transaction() as conn:
        counts = conn.execute(
            """
            SELECT count(*), count(assigned_at), count(status), count(full_name)
            FROM leads WHERE org_id = %s
            """,
            (org_id,),
        ).fetchone()
    print(
        f"leads dogrulama: count(*)={counts[0]} "
        f"assigned_at={counts[1]} status={counts[2]} "
        f"full_name={counts[3]}"
    )
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
