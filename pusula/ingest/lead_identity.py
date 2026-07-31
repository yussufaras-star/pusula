"""Lead kimlik zenginleştirme yardımcısı.

Calls ingest sonrası görülen zoho_lead_id'ler için Leads modülünden
telefon/e-posta çeker, resolve_thread ile bağlar ve threads.state'e
Lead_Source / Lead_Status yazar. Ingester değildir.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from psycopg.types.json import Json

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_email, normalize_phone, resolve_thread
from pusula.zoho.crm import coql

logger = logging.getLogger(__name__)

# Canlı Leads inspect ile doğrulandı. Mobile etiketi "Phone-2".
_LEAD_FIELDS = [
    "id",
    "Phone",
    "Mobile",
    "Email",
    "Secondary_Email",
    "Lead_Source",
    "Lead_Status",
    "Owner",
    "Full_Name",
]
_BATCH_SIZE = 100


def sync_lead_identities(lead_ids: set[str]) -> dict[str, int]:
    """Lead id'ler için telefon/e-posta kimliklerini batch çeker ve bağlar.

    Dönüş: processed, phones_added, emails_added, errors.
    Geçersiz telefon/e-posta sessizce atlanır.
    """
    stats = {"processed": 0, "phones_added": 0, "emails_added": 0, "errors": 0}
    cleaned = {lid.strip() for lid in lead_ids if lid and str(lid).strip()}
    if not cleaned:
        return stats

    for chunk in _chunks(sorted(cleaned), _BATCH_SIZE):
        try:
            records = list(_fetch_leads(chunk))
        except Exception:
            logger.exception("lead kimlik COQL batch başarısız (%s id)", len(chunk))
            stats["errors"] += len(chunk)
            continue

        by_id = {str(r.get("id")): r for r in records if r.get("id")}
        for lead_id in chunk:
            record = by_id.get(lead_id)
            if record is None:
                stats["errors"] += 1
                continue
            try:
                phones, emails = _sync_one_lead(lead_id, record)
                stats["processed"] += 1
                stats["phones_added"] += phones
                stats["emails_added"] += emails
            except Exception:
                logger.exception("lead kimlik senkronu başarısız (lead_id=%s)", lead_id)
                stats["errors"] += 1

    return stats


def _fetch_leads(lead_ids: Sequence[str]) -> Iterable[dict[str, Any]]:
    """COQL ile id in (...) batch çeker."""
    ids_sql = ", ".join(lead_ids)
    fields = ", ".join(_LEAD_FIELDS)
    query = f"select {fields} from Leads where id in ({ids_sql})"
    return coql(query)


def _sync_one_lead(lead_id: str, record: dict[str, Any]) -> tuple[int, int]:
    """Tek lead: resolve_thread + ek Mobile/Secondary_Email + state."""
    phone = _as_str(record.get("Phone"))
    mobile = _as_str(record.get("Mobile"))
    email = _as_str(record.get("Email"))
    secondary_email = _as_str(record.get("Secondary_Email"))

    phones_added = 0
    emails_added = 0

    # Ana kimlikler: Phone-1 + Email + zoho_lead.
    before_phone = _normalized_phone(phone)
    before_email = _normalized_email(email)
    phone_was_new = before_phone is not None and not _identity_exists("phone", before_phone)
    email_was_new = before_email is not None and not _identity_exists("email", before_email)

    thread_id = resolve_thread(
        zoho_lead_id=lead_id,
        phone=phone,
        email=email,
    )
    if phone_was_new:
        phones_added += 1
    if email_was_new:
        emails_added += 1

    # Ek kimlikler aynı thread'e (zoho_lead ile birleşir).
    if mobile:
        mobile_norm = _normalized_phone(mobile)
        if mobile_norm is not None:
            was_new = not _identity_exists("phone", mobile_norm)
            resolve_thread(zoho_lead_id=lead_id, phone=mobile)
            if was_new:
                phones_added += 1

    if secondary_email:
        sec_norm = _normalized_email(secondary_email)
        if sec_norm is not None:
            was_new = not _identity_exists("email", sec_norm)
            resolve_thread(zoho_lead_id=lead_id, email=secondary_email)
            if was_new:
                emails_added += 1

    _write_lead_state(
        thread_id,
        lead_source=_as_str(record.get("Lead_Source")),
        lead_status=_as_str(record.get("Lead_Status")),
    )
    return phones_added, emails_added


def _write_lead_state(
    thread_id: str, lead_source: str | None, lead_status: str | None
) -> None:
    """Lead_Source / Lead_Status'u threads.state jsonb'ye yazar (merge)."""
    patch: dict[str, Any] = {}
    if lead_source is not None:
        patch["lead_source"] = lead_source
    if lead_status is not None:
        patch["lead_status"] = lead_status
    if not patch:
        return
    query = """
        UPDATE threads SET
            state = coalesce(state, '{}'::jsonb) || %s::jsonb
        WHERE org_id = %s AND thread_id = %s
    """
    with client.transaction() as conn:
        conn.execute(query, (Json(patch), get_org_id(), thread_id))


def _identity_exists(id_type: str, id_value: str) -> bool:
    with client.transaction() as conn:
        return client.find_identity_thread_id(conn, id_type, id_value) is not None


def _normalized_phone(raw: str | None) -> str | None:
    if raw is None:
        return None
    return normalize_phone(raw)


def _normalized_email(raw: str | None) -> str | None:
    if raw is None:
        return None
    return normalize_email(raw)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _chunks(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]
