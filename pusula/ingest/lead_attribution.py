"""Zoho Leads reklam kaynağı alanları → lead_attribution.

UTM_Source (meta, google, instagram…), UTM_Campaign ve UTM_Content
(reklam içeriği). Lead_Source zaten leads.source'ta. Ayrı tablo: leads
şemasına dokunmaz; okuyan sorgular tablo yoksa kaynak kırılımını boş geçer.

İlk çalıştırma tüm lead'leri çeker (imleç yok), sonra Modified_Time ile
yalnız değişenler. İmleç sync_state 'zoho_lead_attribution'.

Created_Time → zoho_created_at (lead oluşma zamanı; Raporlar'da lead ↔ ciro
korelasyonu). Kişiye dönüştürülen lead'ler de çekilir (converted=both), yoksa
satışa dönen lead'ler sayımdan düşer. Sütun ilk eklendiğinde imleç yok
sayılır ve tüm lead'ler bir kez yeniden çekilir.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.models import SyncState
from pusula.ingest.crm_actions import parse_zoho_time
from pusula.zoho.crm import get_records

logger = logging.getLogger(__name__)

SOURCE = "zoho_lead_attribution"
FIELDS = ("id", "UTM_Source", "UTM_Campaign", "UTM_Content", "Created_Time", "Modified_Time")
OVERLAP = timedelta(minutes=5)
BATCH = 500

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lead_attribution (
    org_id        text NOT NULL DEFAULT 'rexven',
    lead_id       text NOT NULL,
    utm_source    text,
    utm_campaign  text,
    utm_content   text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, lead_id)
);
"""
CREATED_COLUMN_SQL = """
ALTER TABLE lead_attribution ADD COLUMN IF NOT EXISTS zoho_created_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_lead_attribution_created ON lead_attribution (org_id, zoho_created_at);
"""

Row = tuple[str, str | None, str | None, str | None, datetime | None]


def clean(value: Any) -> str | None:
    """Boşluk ve büyük/küçük harf farkı aynı kaynağı bölmesin (Meta / meta)."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def row_from_record(record: dict[str, Any]) -> Row | None:
    rid = record.get("id")
    if rid is None:
        return None
    return (
        str(rid),
        clean(record.get("UTM_Source")),
        clean(record.get("UTM_Campaign")),
        clean(record.get("UTM_Content")),
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
                INSERT INTO lead_attribution
                    (org_id, lead_id, utm_source, utm_campaign, utm_content, zoho_created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (org_id, lead_id) DO UPDATE
                SET utm_source = EXCLUDED.utm_source,
                    utm_campaign = EXCLUDED.utm_campaign,
                    utm_content = EXCLUDED.utm_content,
                    zoho_created_at = coalesce(EXCLUDED.zoho_created_at, lead_attribution.zoho_created_at),
                    updated_at = now()
                """,
                [(org_id, *row) for row in rows],
            )
    return len(rows)


def _fetch(since: datetime | None) -> Iterator[dict[str, Any]]:
    yield from get_records("Leads", FIELDS, modified_since=since, extra_params={"converted": "both"})


def sync_lead_attribution(now: datetime) -> dict[str, int]:
    with client.transaction() as conn:
        conn.execute(SCHEMA_SQL)
        had_created = conn.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'lead_attribution' AND column_name = 'zoho_created_at'
            """
        ).fetchone()
        conn.execute(CREATED_COLUMN_SQL)
    state = client.get_sync_state(SOURCE)
    cursor = state.last_synced_at if had_created and state and state.last_synced_at else None
    since = cursor - OVERLAP if cursor else None
    fetched = written = 0
    newest = cursor
    batch: list[Row] = []
    for record in _fetch(since):
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
