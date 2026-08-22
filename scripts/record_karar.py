"""Cliq karar butonu kaydı — Zoho'ya yazmaz.

Cliq fonksiyonu pusula_karar bu scripti çağırır. payload.karar dolu
nudge, ertesi gün karar_bekleyen listesine girmez.

Kullanım:
    python scripts/record_karar.py --lead-id ID --thread-id TID \\
        --rep-id RID --karar randevu
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Json

from pusula.config import get_org_id

_KARAR_CHOICES = ("randevu", "kaybetti", "tekrar_ara")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Karar bekleyen lead için Cliq kararını kaydet."
    )
    parser.add_argument("--lead-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--rep-id", required=True)
    parser.add_argument("--karar", required=True, choices=list(_KARAR_CHOICES))
    args = parser.parse_args()

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL_POOLED") or os.environ.get(
        "DATABASE_URL"
    )
    if not database_url:
        print("DATABASE_URL ortam değişkeni tanımlı değil")
        return 1

    org_id = get_org_id()
    with psycopg.connect(database_url, prepare_threshold=None) as conn:
        cur = conn.execute(
            """
            INSERT INTO nudges (
                org_id, rep_id, nudge_type, thread_id, lead_id,
                commitment_id, sent_at, channel, payload
            )
            VALUES (
                %s, %s, 'karar_bekleyen', %s, %s,
                NULL, now(), 'cliq', %s
            )
            """,
            (
                org_id,
                args.rep_id,
                args.thread_id,
                args.lead_id,
                Json({"karar": args.karar, "source": "cliq_button"}),
            ),
        )
        conn.commit()
        n = int(cur.rowcount)
    print(f"yazilan={n} karar={args.karar} lead={args.lead_id}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
