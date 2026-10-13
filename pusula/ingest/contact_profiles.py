"""Zoho Contacts müşteri profili → contact_profiles (Re-Co yenileme analizi).

Son 1 yıllık satış ve toplam satış: müşterinin kendi mağazasındaki satış
adedi. Abonelik başlangıç/bitiş, paket, müşteri durumu, sahip ve portföy
yöneticisi. İlk çalıştırma tüm contact'lar, sonra Modified_Time ile değişenler.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.models import SyncState
from pusula.ingest.crm_actions import parse_zoho_time
from pusula.zoho.crm import get_records

logger = logging.getLogger(__name__)

SOURCE = "zoho_contact_profiles"
FIELDS = (
    "id", "Full_Name", "Owner", "Paket", "Abonelik_Ba_lang", "Abonelik_Biti",
    "Son_1_Y_ll_k_Sat", "Total_Sat", "Customer_Status", "Portf_y_Y_neticisi", "Modified_Time",
)
OVERLAP = timedelta(minutes=5)
BATCH = 500

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contact_profiles (
    org_id           text NOT NULL DEFAULT 'rexven',
    contact_id       text NOT NULL,
    full_name        text,
    owner_rep_id     text,
    owner_name       text,
    paket            text,
    abonelik_bas     date,
    abonelik_bit     date,
    son1_satis       integer,
    toplam_satis     integer,
    customer_status  text,
    portfoy_yon      text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, contact_id)
);
"""

Row = tuple[Any, ...]


def _lookup(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, dict):
        return (str(value["id"]) if value.get("id") else None, value.get("name"))
    return None, None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def row_from_record(record: dict[str, Any]) -> Row | None:
    rid = record.get("id")
    if rid is None:
        return None
    owner_id, owner_name = _lookup(record.get("Owner"))
    return (
        str(rid),
        _text(record.get("Full_Name")),
        owner_id,
        owner_name,
        _text(record.get("Paket")),
        _date(record.get("Abonelik_Ba_lang")),
        _date(record.get("Abonelik_Biti")),
        _int(record.get("Son_1_Y_ll_k_Sat")),
        _int(record.get("Total_Sat")),
        _text(record.get("Customer_Status")),
        _text(record.get("Portf_y_Y_neticisi")),
    )


def _write(rows: list[Row]) -> int:
    if not rows:
        return 0
    org_id = get_org_id()
    with client.transaction() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO contact_profiles (org_id, contact_id, full_name, owner_rep_id, owner_name,
                    paket, abonelik_bas, abonelik_bit, son1_satis, toplam_satis, customer_status, portfoy_yon)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (org_id, contact_id) DO UPDATE SET
                    full_name = EXCLUDED.full_name, owner_rep_id = EXCLUDED.owner_rep_id,
                    owner_name = EXCLUDED.owner_name, paket = EXCLUDED.paket,
                    abonelik_bas = EXCLUDED.abonelik_bas, abonelik_bit = EXCLUDED.abonelik_bit,
                    son1_satis = EXCLUDED.son1_satis, toplam_satis = EXCLUDED.toplam_satis,
                    customer_status = EXCLUDED.customer_status, portfoy_yon = EXCLUDED.portfoy_yon,
                    updated_at = now()
                """,
                [(org_id, *row) for row in rows],
            )
    return len(rows)


def sync_contact_profiles(now: datetime) -> dict[str, int]:
    with client.transaction() as conn:
        conn.execute(SCHEMA_SQL)
    state = client.get_sync_state(SOURCE)
    cursor = state.last_synced_at if state and state.last_synced_at else None
    since = cursor - OVERLAP if cursor else None
    fetched = written = 0
    newest = cursor
    batch: list[Row] = []
    for record in get_records("Contacts", FIELDS, modified_since=since):
        fetched += 1
        row = row_from_record(record)
        if row:
            batch.append(row)
        modified = parse_zoho_time(record.get("Modified_Time"))
        if modified and (newest is None or modified > newest):
            newest = modified
        if len(batch) >= BATCH:
            written += _write(batch)
            batch = []
    written += _write(batch)
    client.set_sync_state(
        SyncState(source_name=SOURCE, last_synced_at=newest or now, last_cursor="ok")
    )
    return {"fetched": fetched, "written": written}
