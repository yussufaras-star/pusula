"""Zoho CRM işlem akışı → crm_actions: temsilcinin son CRM işlemi.

Not, görev ve lead/deal/contact kayıtlarının oluşturma ve son değiştirme
anlarını, işlemi yapan kullanıcıyla (Created_By / Modified_By) yazar.
Owner kullanılmaz: kayıt sahibi işlemi yapan kişi değildir.

Yalnız aktif satış temsilcilerinin işlemleri tutulur; otomasyon ve diğer
kullanıcılar atlanır. Zoho kayıt başına yalnız SON değişikliği verir; ara
değişiklikler görünmez. "Son CRM işlemi" için bu yeterlidir, işlem sayısı
için alt sınırdır.

Çekim get_records + If-Modified-Since; modül başına imleç sync_state'te
('zoho_crm_actions:<Modül>'). Yazım idempotenttir.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.models import SyncState
from pusula.ingest.base import to_istanbul
from pusula.zoho.crm import get_records

logger = logging.getLogger(__name__)

SOURCE_PREFIX = "zoho_crm_actions:"
MODULES: tuple[str, ...] = ("Notes", "Tasks", "Leads", "Deals", "Contacts")
_FIELDS = ("id", "Created_Time", "Created_By", "Modified_Time", "Modified_By")
# İlk çalıştırmada veya imleç çok eskiyse geri bakış.
FIRST_LOOKBACK = timedelta(hours=12)
# If-Modified-Since saniye hassasiyeti ve Zoho gecikmesi için örtüşme.
OVERLAP = timedelta(minutes=2)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crm_actions (
    org_id        text NOT NULL DEFAULT 'rexven',
    module        text NOT NULL,
    record_id     text NOT NULL,
    action        text NOT NULL CHECK (action IN ('created', 'modified')),
    acted_at      timestamptz NOT NULL,
    actor_rep_id  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, module, record_id, action, acted_at)
);
CREATE INDEX IF NOT EXISTS idx_crm_actions_actor_time
    ON crm_actions (org_id, actor_rep_id, acted_at);
"""


@dataclass(frozen=True)
class CrmAction:
    module: str
    record_id: str
    action: str
    acted_at: datetime
    actor_rep_id: str


def parse_zoho_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return to_istanbul(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _user_id(lookup: Any) -> str | None:
    if isinstance(lookup, dict) and lookup.get("id"):
        return str(lookup["id"])
    return None


def actions_from_record(
    module: str,
    record: dict[str, Any],
    *,
    since: datetime,
    sales_reps: set[str],
) -> list[CrmAction]:
    """Bir kayıttan, pencere içindeki satış temsilcisi işlemleri."""
    record_id = record.get("id")
    if not record_id:
        return []
    out: list[CrmAction] = []
    created_at = parse_zoho_time(record.get("Created_Time"))
    modified_at = parse_zoho_time(record.get("Modified_Time"))
    creator = _user_id(record.get("Created_By"))
    modifier = _user_id(record.get("Modified_By"))
    if created_at and created_at >= since and creator in sales_reps:
        out.append(CrmAction(module, str(record_id), "created", created_at, creator))
    if (
        modified_at
        and modified_at >= since
        and modifier in sales_reps
        and modified_at != created_at
    ):
        out.append(CrmAction(module, str(record_id), "modified", modified_at, modifier))
    return out


def ensure_schema() -> None:
    with client.transaction() as conn:
        conn.execute(SCHEMA_SQL)


def _sales_reps() -> set[str]:
    with client.transaction() as conn:
        rows = conn.execute(
            "SELECT rep_id FROM reps WHERE org_id = %s AND category = 'sales' AND active",
            (get_org_id(),),
        ).fetchall()
    return {str(row[0]) for row in rows}


def _write(actions: Iterable[CrmAction]) -> int:
    rows = [
        (get_org_id(), a.module, a.record_id, a.action, a.acted_at, a.actor_rep_id)
        for a in actions
    ]
    if not rows:
        return 0
    with client.transaction() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO crm_actions
                    (org_id, module, record_id, action, acted_at, actor_rep_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                rows,
            )
    return len(rows)


def _fetch(module: str, since: datetime) -> Iterator[dict[str, Any]]:
    yield from get_records(module, _FIELDS, modified_since=since)


def sync_module(module: str, now: datetime, sales_reps: set[str]) -> dict[str, int]:
    source = f"{SOURCE_PREFIX}{module}"
    state = client.get_sync_state(source)
    cursor = state.last_synced_at if state and state.last_synced_at else None
    floor = now - FIRST_LOOKBACK
    since = max(cursor - OVERLAP, floor) if cursor else floor
    fetched = 0
    actions: list[CrmAction] = []
    newest = cursor
    for record in _fetch(module, since):
        fetched += 1
        actions.extend(
            actions_from_record(module, record, since=since, sales_reps=sales_reps)
        )
        modified = parse_zoho_time(record.get("Modified_Time"))
        if modified and (newest is None or modified > newest):
            newest = modified
    written = _write(actions)
    client.set_sync_state(
        SyncState(source_name=source, last_synced_at=newest or since, last_cursor=module)
    )
    return {"fetched": fetched, "written": written}


def sync_crm_actions(
    now: datetime, modules: Iterable[str] = MODULES
) -> dict[str, dict[str, int]]:
    """Her modül ayrı; biri hata verirse diğerleri sürer, imleci ilerlemez."""
    ensure_schema()
    reps = _sales_reps()
    out: dict[str, dict[str, int]] = {}
    for module in modules:
        try:
            out[module] = sync_module(module, now, reps)
        except Exception:
            logger.exception("crm_actions %s senkronu başarısız", module)
            out[module] = {"fetched": 0, "written": 0, "failed": 1}
    return out
