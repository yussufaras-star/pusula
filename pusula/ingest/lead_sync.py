"""Zoho Leads ile leads tablosunu uzlaştırır: sahip, statü, kaldırılanlar.

leads satırları yalnız aramada görülen lead'ler için yazılıyordu
(lead_identity); sonradan başka temsilciye verilen, statüsü değişen,
silinen ya da kişiye dönüştürülen lead eski haliyle kalıyordu (portföyde
temsilcinin üzerinde "dokunulmamış" görünüyordu).

Her çalıştırma Zoho'daki tüm lead'lerin id/Owner/Lead_Status'unu çeker:
- farklı sahip/statü → güncellenir (statü değişimi status_changed_at'i
  'gercek' olarak yeniler),
- Zoho'da artık olmayan (silinmiş veya dönüştürülmüş) → zoho_removed_at
  doldurulur; satır silinmez (geçmiş raporlar ve kişi zinciri bozulmasın).
  Geri gelen (çöp kutusundan geri alınan) lead'in işareti kalkar.
Çekim yarım kalırsa (hata) kaldırma işaretlenmez.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.zoho.crm import get_records

logger = logging.getLogger(__name__)

FIELDS = ("id", "Owner", "Lead_Status")
# Zoho'dan beklenmedik az kayıt gelirse (izin/filtre sorunu) toplu
# "kaldırıldı" işaretlemesi yapılmaz.
MIN_FETCHED = 1000

LeadState = tuple[str, str | None, str | None]  # lead_id, owner_rep_id, status


def ensure_columns(conn: Any) -> None:
    conn.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS zoho_removed_at timestamptz")


def state_from_record(record: dict[str, Any]) -> LeadState | None:
    rid = record.get("id")
    if rid is None:
        return None
    owner = record.get("Owner")
    owner_id = str(owner["id"]) if isinstance(owner, dict) and owner.get("id") else None
    status = record.get("Lead_Status")
    status = status.strip() or None if isinstance(status, str) else None
    return str(rid), owner_id, status


def _fetch() -> Iterator[dict[str, Any]]:
    yield from get_records("Leads", FIELDS)


def _apply(states: Iterable[LeadState]) -> dict[str, int]:
    org_id = get_org_id()
    with client.transaction() as conn:
        ensure_columns(conn)
        conn.execute(
            """
            CREATE TEMP TABLE zoho_leads_now (
                lead_id text PRIMARY KEY, owner_rep_id text, status text
            ) ON COMMIT DROP
            """
        )
        with conn.cursor() as cur:
            with cur.copy("COPY zoho_leads_now (lead_id, owner_rep_id, status) FROM STDIN") as copy:
                for row in states:
                    copy.write_row(row)
        updated = conn.execute(
            """
            UPDATE leads l SET
                owner_rep_id = coalesce(z.owner_rep_id, l.owner_rep_id),
                status = coalesce(z.status, l.status),
                zoho_removed_at = NULL,
                status_changed_at = CASE WHEN z.status IS NOT NULL
                    AND l.status IS DISTINCT FROM z.status THEN now() ELSE l.status_changed_at END,
                status_changed_source = CASE WHEN z.status IS NOT NULL
                    AND l.status IS DISTINCT FROM z.status THEN 'gercek' ELSE l.status_changed_source END
            FROM zoho_leads_now z
            WHERE l.org_id = %s AND l.lead_id = z.lead_id
              AND (l.zoho_removed_at IS NOT NULL
                   OR (z.owner_rep_id IS NOT NULL AND l.owner_rep_id IS DISTINCT FROM z.owner_rep_id)
                   OR (z.status IS NOT NULL AND l.status IS DISTINCT FROM z.status))
            """,
            (org_id,),
        ).rowcount
        removed = conn.execute(
            """
            UPDATE leads l SET zoho_removed_at = now()
            WHERE l.org_id = %s AND l.zoho_removed_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM zoho_leads_now z WHERE z.lead_id = l.lead_id)
            """,
            (org_id,),
        ).rowcount
    return {"updated": int(updated), "removed": int(removed)}


def sync_leads() -> dict[str, int]:
    states: dict[str, LeadState] = {}
    for record in _fetch():
        state = state_from_record(record)
        if state:
            states[state[0]] = state
    if len(states) < MIN_FETCHED:
        logger.warning("Zoho'dan yalnız %s lead geldi; uzlaştırma atlandı", len(states))
        return {"fetched": len(states), "updated": 0, "removed": 0}
    return {"fetched": len(states), **_apply(states.values())}
