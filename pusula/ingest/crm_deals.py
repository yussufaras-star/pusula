"""Zoho Deals senkronu (satış döngüsü).

Deal → Contact → Lead zinciriyle cycle_start_at = lead.assigned_at
(Zoho Created_Time). Nisan 2026 toplu taşıma lead'lerinde
cycle_start_reliable = false.

Kullanım (scripts/ingest_sales_cycle.py üzerinden).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.ingest.base import to_istanbul
from pusula.zoho.crm import coql

logger = logging.getLogger(__name__)

# zoho_schema_check --module Deals ile doğrulandı.
# Stage picklist: Kapandı Kazanıldı, Ödeme Aşamasında,
# Muhasebe Onayı, İptaller-İadeler.
_DEAL_FIELDS = [
    "id",
    "Deal_Name",
    "Stage",
    "Amount",
    "Closing_Date",
    "Created_Time",
    "Modified_Time",
    "Contact_Name",
    "Lead_Source",
    "Owner",
]

# Nisan 2026 CRM taşıma penceresi (lead Zoho Created_Time).
_MIGRATION_START = date(2026, 4, 1)
_MIGRATION_END = date(2026, 5, 1)


def sync_deals(*, since: datetime, dry_run: bool = False) -> dict[str, int]:
    """Deals'i since ile sync eder.

    Pencere: Created_Time >= since OR Modified_Time >= since.
    Sadece Created_Time kullanılırsa Ağustos'ta güncellenen eski
    deal'ler kaçmaz; yeni oluşturulanlar da Created ile gelir.
    """
    stats = {
        "fetched": 0,
        "written": 0,
        "with_thread": 0,
        "with_cycle": 0,
        "chain_broken": 0,
        "unreliable_cycle": 0,
        "no_thread": 0,
        "errors": 0,
        "new": 0,
        "already": 0,
        "amount_empty": 0,
    }
    org_id = get_org_id()
    since_local = to_istanbul(since)
    since_str = _format_zoho_dt(since_local)
    # COQL OR ile hem yeni oluşturulan hem güncellenen deal'ler.
    query = (
        "select " + ", ".join(_DEAL_FIELDS) + " from Deals "
        f"where Created_Time >= '{since_str}' "
        f"or Modified_Time >= '{since_str}' "
        "order by Created_Time asc"
    )

    records: list[dict[str, Any]] = []
    try:
        for record in coql(query):
            stats["fetched"] += 1
            records.append(record)
    except Exception:
        logger.exception("Deals COQL başarısız")
        stats["errors"] += 1
        return stats

    if dry_run:
        extra = _dry_run_counts(records)
        stats.update(extra)
        stats["written"] = 0
        return stats

    with client.transaction() as conn:
        contact_ids = []
        for record in records:
            cid = _lookup_id(record.get("Contact_Name"))
            if cid:
                contact_ids.append(cid)
        contact_map = _load_contacts(conn, contact_ids)
        lead_ids = {
            row["lead_id"]
            for row in contact_map.values()
            if row.get("lead_id")
        }
        lead_starts = _load_lead_starts(conn, list(lead_ids))

        upserts: list[tuple[Any, ...]] = []
        for record in records:
            try:
                deal_id = _as_str(record.get("id"))
                if deal_id is None:
                    stats["errors"] += 1
                    continue
                contact_id = _lookup_id(record.get("Contact_Name"))
                contact = contact_map.get(contact_id or "")
                lead_id = contact.get("lead_id") if contact else None
                thread_id = contact.get("thread_id") if contact else None

                # Contact tablosunda yoksa identities üzerinden dene.
                if contact_id and (thread_id is None or lead_id is None):
                    tid, lid = _resolve_from_identity(conn, contact_id)
                    thread_id = thread_id or tid
                    lead_id = lead_id or lid

                cycle_start_at = None
                if lead_id:
                    cycle_start_at = lead_starts.get(lead_id)
                    if cycle_start_at is None:
                        # leads satırı yoksa zincir kopuk sayılır.
                        pass

                if thread_id:
                    stats["with_thread"] += 1
                else:
                    stats["no_thread"] += 1

                if cycle_start_at is None:
                    stats["chain_broken"] += 1
                    reliable = False
                else:
                    stats["with_cycle"] += 1
                    reliable = not _is_migration_lead(cycle_start_at)
                    if not reliable:
                        stats["unreliable_cycle"] += 1

                owner = record.get("Owner")
                owner_rep_id = None
                if isinstance(owner, dict) and owner.get("id"):
                    owner_rep_id = str(owner["id"])

                upserts.append(
                    (
                        org_id,
                        deal_id,
                        contact_id,
                        lead_id,
                        thread_id,
                        _as_str(record.get("Stage")),
                        _parse_amount(record.get("Amount")),
                        _parse_dt(record.get("Created_Time")),
                        _parse_date_as_dt(record.get("Closing_Date")),
                        owner_rep_id,
                        _as_str(record.get("Lead_Source")),
                        cycle_start_at,
                        reliable,
                    )
                )
            except Exception:
                logger.exception("deal kayıt işlenemedi")
                stats["errors"] += 1

        if upserts:
            existing = _existing_deal_ids([str(row[1]) for row in upserts], conn)
            stats["already"] = len(existing)
            stats["new"] = sum(
                1 for row in upserts if str(row[1]) not in existing
            )
            stats["amount_empty"] = sum(1 for row in upserts if row[6] is None)
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO deals (
                        org_id, deal_id, contact_id, lead_id, thread_id,
                        stage, amount, created_at, closed_at,
                        owner_rep_id, source, cycle_start_at,
                        cycle_start_reliable
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (org_id, deal_id) DO UPDATE SET
                        contact_id = COALESCE(
                            EXCLUDED.contact_id, deals.contact_id
                        ),
                        lead_id = COALESCE(EXCLUDED.lead_id, deals.lead_id),
                        thread_id = COALESCE(EXCLUDED.thread_id, deals.thread_id),
                        stage = EXCLUDED.stage,
                        amount = EXCLUDED.amount,
                        created_at = EXCLUDED.created_at,
                        closed_at = EXCLUDED.closed_at,
                        owner_rep_id = EXCLUDED.owner_rep_id,
                        source = EXCLUDED.source,
                        cycle_start_at = COALESCE(
                            EXCLUDED.cycle_start_at, deals.cycle_start_at
                        ),
                        cycle_start_reliable = EXCLUDED.cycle_start_reliable
                            OR deals.cycle_start_reliable
                    """,
                    upserts,
                )
                rowcount = cur.rowcount
            stats["written"] = (
                int(rowcount) if rowcount is not None and rowcount >= 0
                else len(upserts)
            )

    return stats


def _dry_run_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    """Yazmadan: yeni / mevcut / amount boş. Amount = Zoho Amount."""
    ids: list[str] = []
    amount_empty = 0
    for record in records:
        deal_id = _as_str(record.get("id"))
        if deal_id:
            ids.append(deal_id)
        if _parse_amount(record.get("Amount")) is None:
            amount_empty += 1
    unique_ids = list(dict.fromkeys(ids))
    existing: set[str] = set()
    if unique_ids:
        with client.transaction() as conn:
            existing = _existing_deal_ids(unique_ids, conn)
    return {
        "already": len(existing),
        "new": sum(1 for did in unique_ids if did not in existing),
        "amount_empty": amount_empty,
    }


def _existing_deal_ids(deal_ids: list[str], conn: Any) -> set[str]:
    if not deal_ids:
        return set()
    rows = conn.execute(
        """
        SELECT deal_id FROM deals
        WHERE org_id = %s AND deal_id = ANY(%s)
        """,
        (get_org_id(), deal_ids),
    ).fetchall()
    return {str(row[0]) for row in rows if row and row[0]}


def _is_migration_lead(cycle_start: datetime) -> bool:
    """Nisan 2026 toplu taşıma Created_Time penceresi."""
    local = to_istanbul(cycle_start).date()
    return _MIGRATION_START <= local < _MIGRATION_END


def _load_contacts(
    conn: Any, contact_ids: list[str]
) -> dict[str, dict[str, str | None]]:
    if not contact_ids:
        return {}
    rows = conn.execute(
        """
        SELECT contact_id, lead_id, thread_id FROM contacts
        WHERE org_id = %s AND contact_id = ANY(%s)
        """,
        (get_org_id(), contact_ids),
    ).fetchall()
    return {
        str(cid): {
            "lead_id": str(lid) if lid else None,
            "thread_id": str(tid) if tid else None,
        }
        for cid, lid, tid in rows
    }


def _load_lead_starts(
    conn: Any, lead_ids: list[str]
) -> dict[str, datetime]:
    """lead_id → assigned_at (Zoho Created_Time)."""
    if not lead_ids:
        return {}
    rows = conn.execute(
        """
        SELECT lead_id, assigned_at FROM leads
        WHERE org_id = %s AND lead_id = ANY(%s) AND assigned_at IS NOT NULL
        """,
        (get_org_id(), lead_ids),
    ).fetchall()
    return {str(lid): to_istanbul(at) for lid, at in rows}


def _resolve_from_identity(
    conn: Any, contact_id: str
) -> tuple[str | None, str | None]:
    row = conn.execute(
        """
        SELECT thread_id FROM identities
        WHERE org_id = %s AND id_type = 'zoho_contact' AND id_value = %s
        LIMIT 1
        """,
        (get_org_id(), contact_id),
    ).fetchone()
    if not row or not row[0]:
        return None, None
    thread_id = str(row[0])
    lead = conn.execute(
        """
        SELECT id_value FROM identities
        WHERE org_id = %s AND thread_id = %s AND id_type = 'zoho_lead'
        ORDER BY id_value LIMIT 1
        """,
        (get_org_id(), thread_id),
    ).fetchone()
    if lead:
        return thread_id, str(lead[0])
    lead2 = conn.execute(
        """
        SELECT lead_id FROM leads
        WHERE org_id = %s AND thread_id = %s
        ORDER BY assigned_at ASC NULLS LAST LIMIT 1
        """,
        (get_org_id(), thread_id),
    ).fetchone()
    return thread_id, (str(lead2[0]) if lead2 else None)


def _lookup_id(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("id"):
        return str(value["id"])
    return None


def _parse_amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_istanbul(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return to_istanbul(datetime.fromisoformat(text))
    except ValueError:
        return None


def _parse_date_as_dt(value: Any) -> datetime | None:
    """Closing_Date (date) → gün başı Istanbul timestamptz."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_istanbul(value)
    text = str(value).strip()[:10]
    try:
        d = date.fromisoformat(text)
    except ValueError:
        return None
    return to_istanbul(datetime(d.year, d.month, d.day, 0, 0, 0))


def _format_zoho_dt(value: datetime) -> str:
    local = to_istanbul(value)
    offset = local.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return local.strftime("%Y-%m-%dT%H:%M:%S") + offset
