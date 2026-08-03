"""Zoho Contacts senkronu (satış döngüsü zinciri için).

Tüm Contacts kayıtlarını çeker; phone/email + zoho_contact kimliklerini
thread'e bağlar; contacts tablosuna yazar. lead_id, aynı thread'deki
zoho_lead kimliğinden çözülür (Zoho Contact'ta Lead_Id alanı yok).

Kullanım (scripts/ingest_sales_cycle.py üzerinden).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import psycopg

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import (
    normalize_email,
    normalize_phone,
    resolve_thread_detailed,
)
from pusula.ingest.base import to_istanbul
from pusula.zoho.crm import coql

logger = logging.getLogger(__name__)

# zoho_schema_check --module Contacts ile doğrulandı (Mobile yok).
_CONTACT_FIELDS = [
    "id",
    "Created_Time",
    "Owner",
    "Phone",
    "Email",
    "Secondary_Email",
    "Full_Name",
    "Lead_Source",
]
_BATCH_SIZE = 25


def sync_contacts(*, dry_run: bool = False) -> dict[str, int]:
    """Tüm Contacts kayıtlarını sync eder. --since yok."""
    stats = {
        "fetched": 0,
        "written": 0,
        "with_thread": 0,
        "with_lead": 0,
        "errors": 0,
    }
    org_id = get_org_id()
    query = (
        "select " + ", ".join(_CONTACT_FIELDS) + " from Contacts "
        "where Created_Time is not null "
        "order by Created_Time asc"
    )
    parsed: list[dict[str, Any]] = []

    try:
        for record in coql(query):
            stats["fetched"] += 1
            try:
                contact_id = _as_str(record.get("id"))
                if contact_id is None:
                    stats["errors"] += 1
                    continue
                owner = record.get("Owner")
                owner_rep_id = (
                    str(owner["id"])
                    if isinstance(owner, dict) and owner.get("id")
                    else None
                )
                phone = None
                raw_phone = _as_str(record.get("Phone"))
                if raw_phone:
                    phone = normalize_phone(raw_phone)
                email = None
                for key in ("Email", "Secondary_Email"):
                    raw_email = _as_str(record.get(key))
                    if not raw_email:
                        continue
                    email = normalize_email(raw_email)
                    if email:
                        break
                parsed.append(
                    {
                        "contact_id": contact_id,
                        "created_at": _parse_dt(record.get("Created_Time")),
                        "owner_rep_id": owner_rep_id,
                        "phone": phone,
                        "email": email,
                    }
                )
            except Exception:
                logger.exception("contact kayıt işlenemedi")
                stats["errors"] += 1
    except Exception:
        logger.exception("Contacts COQL başarısız")
        stats["errors"] += 1
        return stats

    if dry_run:
        stats["written"] = len(parsed)
        return stats

    print(f"contacts: {len(parsed)} kayıt yazılacak", flush=True)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL yok")

    with psycopg.connect(database_url) as warm:
        client.load_blocklist(warm)

    # Her chunk kendi bağlantısı: pooler uzun session'ı düşürünce devam edebilsin.
    for i in range(0, len(parsed), _BATCH_SIZE):
        chunk = parsed[i : i + _BATCH_SIZE]
        try:
            with psycopg.connect(database_url) as conn:
                _write_contact_chunk_on_conn(conn, org_id, chunk, stats)
                conn.commit()
            print(
                f"contacts chunk {i}-{i + len(chunk)} "
                f"written={stats['written']} hata={stats['errors']}",
                flush=True,
            )
        except Exception:
            logger.exception(
                "contact chunk yazılamadı (%s-%s)", i, i + len(chunk)
            )
            stats["errors"] += len(chunk)

    return stats


def _write_contact_chunk_on_conn(
    conn: Any,
    org_id: str,
    chunk: list[dict[str, Any]],
    stats: dict[str, int],
) -> None:
    upserts: list[tuple[Any, ...]] = []
    for item in chunk:
        contact_id = item["contact_id"]
        try:
            thread_id, _created = resolve_thread_detailed(
                phone=item["phone"],
                email=item["email"],
                zoho_contact_id=contact_id,
                conn=conn,
            )
        except Exception:
            logger.exception("contact thread çözülemedi id=%s", contact_id)
            stats["errors"] += 1
            continue

        lead_id = None
        if thread_id:
            stats["with_thread"] += 1
            lead_id = _lead_for_thread(conn, org_id, thread_id)
            if lead_id:
                stats["with_lead"] += 1

        upserts.append(
            (
                org_id,
                contact_id,
                lead_id,
                thread_id,
                item["created_at"],
                item["owner_rep_id"],
            )
        )

    if not upserts:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO contacts (
                org_id, contact_id, lead_id, thread_id,
                created_at, owner_rep_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, contact_id) DO UPDATE SET
                lead_id = EXCLUDED.lead_id,
                thread_id = COALESCE(EXCLUDED.thread_id, contacts.thread_id),
                created_at = COALESCE(EXCLUDED.created_at, contacts.created_at),
                owner_rep_id = EXCLUDED.owner_rep_id
            """,
            upserts,
        )
    stats["written"] += len(upserts)


def _lead_for_thread(conn: Any, org_id: str, thread_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT id_value FROM identities
        WHERE org_id = %s AND thread_id = %s AND id_type = 'zoho_lead'
        ORDER BY id_value
        LIMIT 1
        """,
        (org_id, thread_id),
    ).fetchone()
    if row:
        return str(row[0])
    row = conn.execute(
        """
        SELECT lead_id FROM leads
        WHERE org_id = %s AND thread_id = %s
        ORDER BY assigned_at ASC NULLS LAST
        LIMIT 1
        """,
        (org_id, thread_id),
    ).fetchone()
    return str(row[0]) if row else None


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
