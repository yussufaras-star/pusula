"""Zoho CRM Calls → events ingester.

CRM'deki çağrı metadata'sını (temsilci notu, süre, sonuç, kimlik)
çeker; transkript üretmez. body = Description (temsilci notu),
body_quality = high.

NOT: FIELD_MAP'teki alan adları henüz canlı ortamda doğrulanmadı.
scripts/inspect_zoho_module.py --module Calls ile doğrulanıp buradan
tek yerden düzeltilir. Bu ortamda Zoho credential yok.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_phone
from pusula.db.models import Direction, Event
from pusula.ingest.base import Ingester, RawRecord, to_istanbul
from pusula.ingest.registry import register
from pusula.zoho.crm import _format_if_modified_since, coql

logger = logging.getLogger(__name__)

# Zoho Calls API alan adları. Doğrulama sonrası sadece burası değişir.
FIELD_MAP: dict[str, str] = {
    "id": "id",
    "modified_time": "Modified_Time",
    "call_start_time": "Call_Start_Time",
    "call_type": "Call_Type",
    "description": "Description",
    "owner": "Owner",
    "duration_sec": "Call_Duration_in_seconds",
    "call_result": "Call_Result",
    "subject": "Subject",
    "call_purpose": "Call_Purpose",
    "se_module": "$se_module",
    "who_id": "Who_Id",
    "what_id": "What_Id",
    # Outbound: Dialled_Number; inbound yedek: Caller_ID (to_event içinde).
    "phone": "Dialled_Number",
    "phone_alt": "Caller_ID",
}

# COQL $ önekini kabul etmez; REST yanıtındaki $se_module burada se_module.
_COQL_FIELD_ALIASES: dict[str, str] = {
    "$se_module": "se_module",
}


@register
class CrmCallsIngester(Ingester):
    """Zoho CRM Calls modülünden satış çağrılarını events'e yazar."""

    source_name = "zoho_crm_calls"
    channel = "call"

    def fetch(self, since: datetime | None) -> Iterator[RawRecord]:
        """Calls'tan Modified_Time > since delta; Call_Start_Time artan.

        Sayfalama coql içinde otomatik. since None ise tüm kayıtlar.
        """
        select_fields = []
        for api_name in FIELD_MAP.values():
            coql_name = _COQL_FIELD_ALIASES.get(api_name, api_name)
            if coql_name not in select_fields:
                select_fields.append(coql_name)

        query = f"select {', '.join(select_fields)} from Calls"
        if since is not None:
            since_str = _format_if_modified_since(since)
            query += (
                f" where {FIELD_MAP['modified_time']} > '{since_str}'"
            )
        query += f" order by {FIELD_MAP['call_start_time']} asc"

        for record in coql(query):
            call_id = record.get(FIELD_MAP["id"])
            start_raw = record.get(FIELD_MAP["call_start_time"])
            if not call_id or not start_raw:
                # source_ref / occurred_at olmadan RawRecord kurulamaz;
                # to_event de aynı koşulla atlar, burada da geç.
                logger.debug("Calls kaydı id veya Call_Start_Time eksik, atlandı")
                continue
            occurred_at = _parse_zoho_datetime(start_raw)
            if occurred_at is None:
                logger.debug("Calls %s: Call_Start_Time çözülemedi (%r)", call_id, start_raw)
                continue
            yield RawRecord(
                source_ref=str(call_id),
                occurred_at=occurred_at,
                payload=record,
            )

    def to_event(self, raw: RawRecord) -> Event | None:
        """Ham Calls kaydını Event'e çevirir; atlanacaksa None."""
        payload = raw.payload
        f = FIELD_MAP

        start_raw = payload.get(f["call_start_time"])
        if not start_raw:
            return None
        occurred_at = _parse_zoho_datetime(start_raw)
        if occurred_at is None:
            return None

        owner = payload.get(f["owner"]) or {}
        rep_id = owner.get("id") if isinstance(owner, dict) else None
        if not rep_id or not _is_sales_rep(str(rep_id)):
            return None

        duration_sec = _parse_duration_sec(payload.get(f["duration_sec"]))
        call_result = payload.get(f["call_result"])
        result_empty = call_result is None or str(call_result).strip() == ""
        if duration_sec == 0 and result_empty:
            return None

        direction = _map_direction(payload.get(f["call_type"]))
        phone = _extract_phone(payload)
        who = payload.get(f["who_id"])
        what = payload.get(f["what_id"])
        se_module = payload.get(f["se_module"]) or payload.get("se_module")

        zoho_contact_id = _lookup_id(who)
        zoho_lead_id: str | None = None
        if isinstance(se_module, str) and se_module == "Leads":
            zoho_lead_id = _lookup_id(what)

        meta: dict[str, Any] = {
            "duration_sec": duration_sec,
            "call_result": call_result,
            "subject": payload.get(f["subject"]),
            "call_purpose": payload.get(f["call_purpose"]),
            "se_module": se_module,
            "who_id": zoho_contact_id,
            "what_id": _lookup_id(what),
            "owner_name": owner.get("name") if isinstance(owner, dict) else None,
        }

        return Event(
            channel="call",
            direction=direction,
            rep_id=str(rep_id),
            occurred_at=occurred_at,
            source_ref=raw.source_ref,
            body=payload.get(f["description"]),
            body_quality="high",
            meta=meta,
            phone=phone,
            zoho_lead_id=zoho_lead_id,
            zoho_contact_id=zoho_contact_id,
        )


def _parse_zoho_datetime(value: Any) -> datetime | None:
    """Zoho ISO-8601 zamanını Europe/Istanbul datetime'a çevirir."""
    if isinstance(value, datetime):
        return to_istanbul(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return to_istanbul(datetime.fromisoformat(text))
    except ValueError:
        return None


def _parse_duration_sec(value: Any) -> int:
    """Call_Duration_in_seconds değerini int saniyeye çevirir; yoksa 0."""
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _map_direction(call_type: Any) -> Direction | None:
    """Outbound → outbound; Inbound/Missed → inbound."""
    if not isinstance(call_type, str):
        return None
    normalized = call_type.strip().lower()
    if normalized == "outbound":
        return "outbound"
    if normalized in ("inbound", "missed"):
        return "inbound"
    return None


def _lookup_id(lookup: Any) -> str | None:
    """Zoho lookup nesnesinden id çıkarır."""
    if isinstance(lookup, dict):
        value = lookup.get("id")
        return str(value) if value else None
    return None


def _extract_phone(payload: dict[str, Any]) -> str | None:
    """Dialled_Number, yoksa Caller_ID; normalize_phone'dan geçirir."""
    for key in (FIELD_MAP["phone"], FIELD_MAP["phone_alt"]):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            normalized = normalize_phone(raw)
            if normalized is not None:
                return normalized
    return None


def _is_sales_rep(rep_id: str) -> bool:
    """reps tablosunda var ve category == 'sales' mi."""
    query = """
        SELECT 1 FROM reps
        WHERE org_id = %s AND rep_id = %s AND category = 'sales'
        LIMIT 1
    """
    with client.transaction() as conn:
        row = conn.execute(query, (get_org_id(), rep_id)).fetchone()
    return row is not None
