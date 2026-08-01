"""Lead kimlik zenginleştirme yardımcısı.

Calls ingest sonrası görülen zoho_lead_id'ler için Leads modülünden
telefon/e-posta çeker, thread kimliğine bağlar ve threads.state'e
Lead_Source / Lead_Status yazar. Ingester değildir.

Kimlik çözümleme N+1 yapmaz: tüm aday çiftler tek sorguda okunur,
yeni thread/identity yazımları executemany ile batch edilir.

Leads telefon alanları (inspect_zoho_module --module Leads, 2026-08):
Phone, Mobile. Phone_2 yok.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import uuid4

from psycopg.types.json import Json

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_email, normalize_phone
from pusula.zoho.crm import coql

logger = logging.getLogger(__name__)

# inspect_zoho_module.py --module Leads ile doğrulandı.
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
    with client.transaction() as conn:
        client.load_blocklist(conn)
    if not cleaned:
        return stats

    # 1) Tüm lead kayıtlarını COQL batch ile çek.
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

    # 2) Aday kimlikleri topla; mevcut eşleşmeleri tek sorguda yükle.
    lead_pairs: dict[str, list[tuple[str, str]]] = {}
    all_pairs: list[tuple[str, str]] = []
    for lead_id, record in records_by_id.items():
        pairs = _active_pairs(lead_id, record)
        lead_pairs[lead_id] = pairs
        all_pairs.extend(pairs)
    identity_map = _load_identity_map(all_pairs)

    # 3) Bellekte çöz; yazılacakları biriktir (resolve_thread / N+1 yok).
    threads_to_create: list[str] = []
    identities_to_upsert: list[tuple[str, str, str]] = []  # thread, type, value
    state_patches: list[tuple[str, dict[str, Any]]] = []  # thread_id, patch
    merge_jobs: list[tuple[str, list[str], dict[tuple[str, str], str]]] = []

    matched_thread_ids: set[str] = set()
    for pairs in lead_pairs.values():
        for pair in pairs:
            tid = identity_map.get(pair)
            if tid is not None:
                matched_thread_ids.add(tid)
    thread_created_at = _load_thread_created_at(matched_thread_ids)

    for lead_id in cleaned:
        if lead_id in failed_ids:
            continue
        record = records_by_id.get(lead_id)
        if record is None:
            stats["errors"] += 1
            continue
        pairs = lead_pairs.get(lead_id) or _active_pairs(lead_id, record)
        try:
            phones, emails, thread_id = _plan_one_lead(
                pairs=pairs,
                identity_map=identity_map,
                thread_created_at=thread_created_at,
                threads_to_create=threads_to_create,
                identities_to_upsert=identities_to_upsert,
                merge_jobs=merge_jobs,
            )
            patch: dict[str, Any] = {}
            lead_source = _as_str(record.get("Lead_Source"))
            lead_status = _as_str(record.get("Lead_Status"))
            if lead_source is not None:
                patch["lead_source"] = lead_source
            if lead_status is not None:
                patch["lead_status"] = lead_status
            if patch and thread_id is not None:
                state_patches.append((thread_id, patch))
            stats["processed"] += 1
            stats["phones_added"] += phones
            stats["emails_added"] += emails
        except Exception:
            logger.exception("lead kimlik senkronu başarısız (lead_id=%s)", lead_id)
            stats["errors"] += 1

    # 4) Tek transaction: merge → thread → identity → state.
    if (
        threads_to_create
        or identities_to_upsert
        or state_patches
        or merge_jobs
    ):
        with client.transaction() as conn:
            for winner, losers, match_pairs in merge_jobs:
                for loser in losers:
                    reason = ", ".join(
                        f"{id_type}={id_value}"
                        for (id_type, id_value), tid in match_pairs.items()
                        if tid == loser
                    )
                    client.reassign_thread_rows(conn, winner, loser)
                    client.record_thread_merge(conn, winner, loser, reason)
                    client.delete_thread(conn, loser)
            if threads_to_create:
                org_id = get_org_id()
                conn.executemany(
                    "INSERT INTO threads (org_id, thread_id) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING",
                    [(org_id, tid) for tid in threads_to_create],
                )
            if identities_to_upsert:
                org_id = get_org_id()
                conn.executemany(
                    """
                    INSERT INTO identities (org_id, thread_id, id_type, id_value)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (org_id, id_type, id_value) DO UPDATE SET
                        thread_id = EXCLUDED.thread_id,
                        last_seen_at = now()
                    """,
                    [
                        (org_id, thread_id, id_type, id_value)
                        for thread_id, id_type, id_value in identities_to_upsert
                    ],
                )
            for thread_id, patch in state_patches:
                conn.execute(
                    """
                    UPDATE threads SET
                        state = coalesce(state, '{}'::jsonb) || %s::jsonb
                    WHERE org_id = %s AND thread_id = %s
                    """,
                    (Json(patch), get_org_id(), thread_id),
                )

    return stats


def _active_pairs(lead_id: str, record: dict[str, Any]) -> list[tuple[str, str]]:
    """Normalize + blocklist sonrası (id_type, id_value) listesi."""
    pairs: list[tuple[str, str]] = [("zoho_lead", lead_id)]
    for raw in (_as_str(record.get("Phone")), _as_str(record.get("Mobile"))):
        normalized = _normalized_phone(raw)
        if normalized is not None:
            pairs.append(("phone", normalized))
    for raw in (_as_str(record.get("Email")), _as_str(record.get("Secondary_Email"))):
        normalized = _normalized_email(raw)
        if normalized is not None:
            pairs.append(("email", normalized))
    return [
        (id_type, id_value)
        for id_type, id_value in pairs
        if not client.is_identifier_blocked(id_type, id_value)
    ]


def _plan_one_lead(
    *,
    pairs: list[tuple[str, str]],
    identity_map: IdentityMap,
    thread_created_at: dict[str, Any],
    threads_to_create: list[str],
    identities_to_upsert: list[tuple[str, str, str]],
    merge_jobs: list[tuple[str, list[str], dict[tuple[str, str], str]]],
) -> tuple[int, int, str | None]:
    """Tek lead için thread çözümü planlar; yazma listelerine ekler."""
    if not pairs:
        return 0, 0, None

    matches: dict[tuple[str, str], str] = {}
    for pair in pairs:
        found = identity_map.get(pair)
        if found is not None:
            matches[pair] = found
    matched_ids = sorted(set(matches.values()))

    phones_added = 0
    emails_added = 0

    if not matched_ids:
        thread_id = uuid4().hex
        threads_to_create.append(thread_id)
        thread_created_at[thread_id] = None
    elif len(matched_ids) == 1:
        thread_id = matched_ids[0]
    else:
        # En eski created_at kazanır (resolve_thread ile aynı kural).
        thread_id = min(
            matched_ids,
            key=lambda tid: (thread_created_at.get(tid) is None, thread_created_at.get(tid), tid),
        )
        losers = [tid for tid in matched_ids if tid != thread_id]
        merge_jobs.append((thread_id, losers, dict(matches)))
        # Kaybeden thread kimliklerini kazananla güncelle (map).
        for pair, tid in list(identity_map.items()):
            if tid in losers:
                identity_map[pair] = thread_id

    for id_type, id_value in pairs:
        was_new = (id_type, id_value) not in identity_map
        prev = identity_map.get((id_type, id_value))
        if was_new or prev != thread_id:
            identities_to_upsert.append((thread_id, id_type, id_value))
        if was_new:
            if id_type == "phone":
                phones_added += 1
            elif id_type == "email":
                emails_added += 1
        identity_map[(id_type, id_value)] = thread_id

    return phones_added, emails_added, thread_id


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


def _load_thread_created_at(thread_ids: set[str]) -> dict[str, Any]:
    """Merge için created_at; tek sorgu."""
    if not thread_ids:
        return {}
    query = """
        SELECT thread_id, created_at FROM threads
        WHERE org_id = %s AND thread_id = ANY(%s)
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(), list(thread_ids))).fetchall()
    return {str(row[0]): row[1] for row in rows}


def _fetch_leads(lead_ids: Sequence[str]) -> Iterable[dict[str, Any]]:
    """COQL ile id in (...) batch çeker."""
    ids_sql = ", ".join(lead_ids)
    fields = ", ".join(_LEAD_FIELDS)
    query = f"select {fields} from Leads where id in ({ids_sql})"
    return coql(query)


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
