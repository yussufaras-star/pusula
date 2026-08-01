"""Lead kimlik zenginleştirme yardımcısı.

Calls ingest sonrası görülen zoho_lead_id'ler için Leads modülünden
telefon/e-posta çeker, resolve_thread ile bağlar ve threads.state'e
Lead_Source / Lead_Status yazar. Ingester değildir.

Kimlik varlık kontrolü N+1 yapmaz: sync başında tüm aday
(id_type, id_value) çiftleri tek sorguda identities'ten çekilir.
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

IdentityMap = dict[tuple[str, str], str]  # (id_type, id_value) -> thread_id


def sync_lead_identities(lead_ids: set[str]) -> dict[str, int]:
    """Lead id'ler için telefon/e-posta kimliklerini batch çeker ve bağlar.

    Dönüş: processed, phones_added, emails_added, errors.
    Geçersiz telefon/e-posta sessizce atlanır.
    """
    stats = {"processed": 0, "phones_added": 0, "emails_added": 0, "errors": 0}
    cleaned = {lid.strip() for lid in lead_ids if lid and str(lid).strip()}
    # resolve_thread blocklist cache ister; ingest dışı çağrıda da yükle.
    with client.transaction() as conn:
        client.load_blocklist(conn)
    if not cleaned:
        return stats

    # 1) Tüm lead kayıtlarını çek.
    records_by_id: dict[str, dict[str, Any]] = {}
    failed_ids: set[str] = set()
    for chunk in _chunks(sorted(cleaned), _BATCH_SIZE):
        try:
            for record in _fetch_leads(chunk):
                rid = record.get("id")
                if rid is not None:
                    records_by_id[str(rid)] = record
        except Exception:
            logger.exception("lead kimlik COQL batch başarısız (%s id)", len(chunk))
            stats["errors"] += len(chunk)
            failed_ids.update(chunk)

    # 2) Aday kimlik çiftlerini topla, tek sorguda mevcutları yükle.
    candidate_pairs: list[tuple[str, str]] = []
    for lead_id, record in records_by_id.items():
        candidate_pairs.extend(_candidate_pairs(lead_id, record))
    identity_map = _load_identity_map(candidate_pairs)

    # 3) Lead başına sync; yeni kimlikler identity_map'e eklenir.
    for lead_id in cleaned:
        if lead_id in failed_ids:
            continue
        record = records_by_id.get(lead_id)
        if record is None:
            stats["errors"] += 1
            continue
        try:
            phones, emails = _sync_one_lead(lead_id, record, identity_map)
            stats["processed"] += 1
            stats["phones_added"] += phones
            stats["emails_added"] += emails
        except Exception:
            logger.exception("lead kimlik senkronu başarısız (lead_id=%s)", lead_id)
            stats["errors"] += 1

    return stats


def _candidate_pairs(
    lead_id: str, record: dict[str, Any]
) -> list[tuple[str, str]]:
    """Bu lead için normalize edilmiş (id_type, id_value) adayları."""
    pairs: list[tuple[str, str]] = [("zoho_lead", lead_id)]
    for raw in (_as_str(record.get("Phone")), _as_str(record.get("Mobile"))):
        normalized = _normalized_phone(raw)
        if normalized is not None:
            pairs.append(("phone", normalized))
    for raw in (_as_str(record.get("Email")), _as_str(record.get("Secondary_Email"))):
        normalized = _normalized_email(raw)
        if normalized is not None:
            pairs.append(("email", normalized))
    return pairs


def _load_identity_map(pairs: Sequence[tuple[str, str]]) -> IdentityMap:
    """Verilen çiftleri identities'ten tek sorguda çeker."""
    unique = list({(t, v) for t, v in pairs})
    if not unique:
        return {}
    types = [t for t, _ in unique]
    values = [v for _, v in unique]
    query = """
        SELECT i.id_type, i.id_value, i.thread_id
        FROM identities i
        INNER JOIN unnest(%s::text[], %s::text[]) AS w(id_type, id_value)
            ON i.id_type = w.id_type AND i.id_value = w.id_value
        WHERE i.org_id = %s
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (types, values, get_org_id())).fetchall()
    return {(str(row[0]), str(row[1])): str(row[2]) for row in rows}


def _fetch_leads(lead_ids: Sequence[str]) -> Iterable[dict[str, Any]]:
    """COQL ile id in (...) batch çeker."""
    ids_sql = ", ".join(lead_ids)
    fields = ", ".join(_LEAD_FIELDS)
    query = f"select {fields} from Leads where id in ({ids_sql})"
    return coql(query)


def _sync_one_lead(
    lead_id: str, record: dict[str, Any], identity_map: IdentityMap
) -> tuple[int, int]:
    """Tek lead: resolve_thread + ek Mobile/Secondary_Email + state."""
    phone = _as_str(record.get("Phone"))
    mobile = _as_str(record.get("Mobile"))
    email = _as_str(record.get("Email"))
    secondary_email = _as_str(record.get("Secondary_Email"))

    phones_added = 0
    emails_added = 0

    before_phone = _normalized_phone(phone)
    before_email = _normalized_email(email)
    phone_was_new = before_phone is not None and ("phone", before_phone) not in identity_map
    email_was_new = before_email is not None and ("email", before_email) not in identity_map

    thread_id = resolve_thread(
        zoho_lead_id=lead_id,
        phone=phone,
        email=email,
    )
    _remember(identity_map, "zoho_lead", lead_id, thread_id)
    if before_phone is not None:
        _remember(identity_map, "phone", before_phone, thread_id)
        if phone_was_new:
            phones_added += 1
    if before_email is not None:
        _remember(identity_map, "email", before_email, thread_id)
        if email_was_new:
            emails_added += 1

    if mobile:
        mobile_norm = _normalized_phone(mobile)
        if mobile_norm is not None:
            was_new = ("phone", mobile_norm) not in identity_map
            thread_id = resolve_thread(zoho_lead_id=lead_id, phone=mobile)
            _remember(identity_map, "phone", mobile_norm, thread_id)
            _remember(identity_map, "zoho_lead", lead_id, thread_id)
            if was_new:
                phones_added += 1

    if secondary_email:
        sec_norm = _normalized_email(secondary_email)
        if sec_norm is not None:
            was_new = ("email", sec_norm) not in identity_map
            thread_id = resolve_thread(zoho_lead_id=lead_id, email=secondary_email)
            _remember(identity_map, "email", sec_norm, thread_id)
            _remember(identity_map, "zoho_lead", lead_id, thread_id)
            if was_new:
                emails_added += 1

    _write_lead_state(
        thread_id,
        lead_source=_as_str(record.get("Lead_Source")),
        lead_status=_as_str(record.get("Lead_Status")),
    )
    return phones_added, emails_added


def _remember(
    identity_map: IdentityMap, id_type: str, id_value: str, thread_id: str | None
) -> None:
    if thread_id is None:
        return
    identity_map[(id_type, id_value)] = thread_id


def _write_lead_state(
    thread_id: str | None, lead_source: str | None, lead_status: str | None
) -> None:
    """Lead_Source / Lead_Status'u threads.state jsonb'ye yazar (merge)."""
    if thread_id is None:
        return
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
