"""Zoho Deals paket/aşama/tutar → deal_packages (aylık prim hedefi için).

deals tablosu günde bir ve paketsiz gelir; prim ilerlemesi saatlik ve paket
bazlı olmalı. Ayrı tablo: Paket (lookup adı), Type (Yeni/Mevcut Müşteri),
Stage (görünen ad, ör. "Kapandı Kazanıldı"), Amount, Owner, Closing_Date.
İlk çalıştırma tüm deal'ler, sonra Modified_Time ile değişenler; iptal/iade
aşamaya taşındığında satır güncellenir ve sayımdan düşer.
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

SOURCE = "zoho_deal_packages"
FIELDS = ("id", "Paket", "Type", "Stage", "Amount", "Owner", "Closing_Date", "Created_Time", "Modified_Time")
OVERLAP = timedelta(minutes=5)
BATCH = 500

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deal_packages (
    org_id        text NOT NULL DEFAULT 'rexven',
    deal_id       text NOT NULL,
    package       text,
    deal_type     text,
    stage         text,
    amount        numeric,
    owner_rep_id  text,
    closing_date  date,
    created_at    timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, deal_id)
);
CREATE INDEX IF NOT EXISTS idx_deal_packages_close ON deal_packages (org_id, closing_date);
"""

Row = tuple[str, str | None, str | None, str | None, float | None, str | None, date | None, datetime | None]


def _name(lookup: Any) -> str | None:
    if isinstance(lookup, dict):
        value = lookup.get("name")
        return str(value).strip() if value else None
    return str(lookup).strip() if lookup else None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def row_from_record(record: dict[str, Any]) -> Row | None:
    rid = record.get("id")
    if rid is None:
        return None
    owner = record.get("Owner")
    amount = record.get("Amount")
    return (
        str(rid),
        _name(record.get("Paket")),
        _name(record.get("Type")),
        _name(record.get("Stage")),
        float(amount) if isinstance(amount, (int, float)) else None,
        str(owner["id"]) if isinstance(owner, dict) and owner.get("id") else None,
        _date(record.get("Closing_Date")),
        parse_zoho_time(record.get("Created_Time")),
    )


def _write(rows: list[Row]) -> int:
    if not rows:
        return 0
    org_id = get_org_id()
    with client.transaction() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO deal_packages (org_id, deal_id, package, deal_type, stage, amount,
                                           owner_rep_id, closing_date, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (org_id, deal_id) DO UPDATE
                SET package = EXCLUDED.package, deal_type = EXCLUDED.deal_type,
                    stage = EXCLUDED.stage, amount = EXCLUDED.amount,
                    owner_rep_id = EXCLUDED.owner_rep_id,
                    closing_date = EXCLUDED.closing_date,
                    created_at = EXCLUDED.created_at, updated_at = now()
                """,
                [(org_id, *row) for row in rows],
            )
    return len(rows)


def sync_deal_packages(now: datetime) -> dict[str, int]:
    with client.transaction() as conn:
        conn.execute(SCHEMA_SQL)
    state = client.get_sync_state(SOURCE)
    cursor = state.last_synced_at if state and state.last_synced_at else None
    since = cursor - OVERLAP if cursor else None
    fetched = written = 0
    newest = cursor
    batch: list[Row] = []
    for record in get_records("Deals", FIELDS, modified_since=since):
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
