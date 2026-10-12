"""Zoho CRM messages__s → giden WhatsApp olayları.

Modül konuşma tutar, tek tek balon tutmaz. conversation_status__s
'Replied' son hareketin temsilciden gittiği kayıttır. message_time__s
o konuşmanın son mesaj zamanıdır. source_ref = id + message_time:
zaman değişince yeni olay yazılır, eskisi durur. İki senkron arasında
aynı konuşmaya art arda giden mesajlar tek olayda birleşir.

COQL bu modülde scope vermiyor; çekim get_records ile.
Mesaj gövdesi alınmaz ve yazılmaz — doluluk için zaman ve temsilci yeter.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_phone
from pusula.db.models import Event
from pusula.ingest.base import ISTANBUL, Ingester, RawRecord, to_istanbul
from pusula.ingest.registry import register
from pusula.zoho.crm import get_records

logger = logging.getLogger(__name__)

MODULE = "messages__s"

# Giden: Zoho picklist actual value. Responded müşteri tarafıdır, alınmaz.
_OUTBOUND_STATUS = "replied"
_WHATSAPP_SERVICE = "whatsapp"

_FIELDS: tuple[str, ...] = (
    "id",
    "conversation_status__s",
    "message_service__s",
    "message_time__s",
    "modified_time__s",
    "replied_by__s",
    "modified_by__s",
    "record_owner__s",
    "sender__s",
    "mobile_number__s",
)

# Temsilci: önce yanıtlayan, yoksa son düzenleyen, yoksa kayıt sahibi.
_REP_FIELDS: tuple[tuple[str, str], ...] = (
    ("replied_by__s", "replied_by"),
    ("modified_by__s", "modified_by"),
    ("record_owner__s", "record_owner"),
)


@register
class CrmWhatsappIngester(Ingester):
    """Zoho CRM Messages modülünden giden WhatsApp temasını events'e yazar."""

    source_name = "zoho_crm_whatsapp"
    channel = "whatsapp"

    def __init__(self) -> None:
        self._reps: dict[str, tuple[str, bool]] | None = None
        self.last_skip_reason: str | None = None
        self.last_skip_sample: dict[str, Any] | None = None
        self.fetch_limit: int | None = None
        self.fetch_truncated = False

    def fetch(self, since: datetime | None) -> Iterator[RawRecord]:
        """messages__s delta: If-Modified-Since; Modified_Time artan.

        Watermark RawRecord.occurred_at = Modified_Time.
        Event.occurred_at to_event'te message_time kalır.
        API sıra garantisi vermez; tamponlayıp sıralarız.
        """
        self._reps = _load_reps()
        buffered: list[dict[str, Any]] = []
        for record in get_records(MODULE, _FIELDS, modified_since=since):
            if not record.get("id"):
                logger.debug("messages__s kaydı id eksik, atlandı")
                continue
            buffered.append(record)
        buffered.sort(key=_sort_key)

        yielded = 0
        for record in buffered:
            modified_at = _parse_zoho_datetime(record.get("modified_time__s"))
            message_at = _parse_zoho_datetime(record.get("message_time__s"))
            occurred_at = modified_at or message_at
            if occurred_at is None:
                logger.debug(
                    "messages__s %s: zaman alanı yok, atlandı", record.get("id")
                )
                continue
            yield RawRecord(
                source_ref=_source_ref(record),
                occurred_at=occurred_at,
                payload=record,
            )
            yielded += 1
            if self.fetch_limit is not None and yielded >= self.fetch_limit:
                self.fetch_truncated = True
                return

    def to_event(self, raw: RawRecord) -> Event | None:
        """Giden WhatsApp konuşma anını Event'e çevirir."""
        self.last_skip_reason = None
        self.last_skip_sample = None
        payload = raw.payload

        service = _text(payload.get("message_service__s"))
        if service is None or service.casefold() != _WHATSAPP_SERVICE:
            return self._skip("not_whatsapp", payload)

        status = _text(payload.get("conversation_status__s"))
        if status is None or status.casefold() != _OUTBOUND_STATUS:
            return self._skip("not_outbound", payload)

        occurred_at = _parse_zoho_datetime(payload.get("message_time__s"))
        if occurred_at is None:
            return self._skip("no_message_time", payload)

        rep_id, rep_source = self._resolve_rep(payload)
        if rep_id is None:
            return self._skip("no_sales_rep", payload)

        zoho_lead_id, zoho_contact_id = _sender_ids(payload.get("sender__s"))
        phone = _phone(payload.get("mobile_number__s"))

        return Event(
            channel="whatsapp",
            direction="outbound",
            rep_id=rep_id,
            occurred_at=occurred_at,
            source_ref=raw.source_ref,
            body=None,
            body_quality=None,
            meta={
                "conversation_status": status,
                "message_service": service,
                "rep_source": rep_source,
                "zoho_message_id": str(payload.get("id")),
                # Konuşmanın son giden anı; balon sayısı değil.
                "grain": "conversation_last_outbound",
            },
            phone=phone,
            zoho_lead_id=zoho_lead_id,
            zoho_contact_id=zoho_contact_id,
        )

    def _resolve_rep(self, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        """Satış temsilcisi olan ilk kullanıcıyı ve kaynağını döner."""
        reps = self._reps if self._reps is not None else _load_reps()
        self._reps = reps
        for field, source in _REP_FIELDS:
            user_id = _lookup_id(payload.get(field))
            if user_id is None:
                continue
            info = reps.get(user_id)
            if info is None:
                continue
            category, active = info
            if category == "sales" and active:
                return user_id, source
        return None, None

    def _skip(self, reason: str, payload: dict[str, Any]) -> None:
        self.last_skip_reason = reason
        self.last_skip_sample = {
            "id": payload.get("id"),
            "status": payload.get("conversation_status__s"),
            "service": payload.get("message_service__s"),
            "has_message_time": bool(payload.get("message_time__s")),
            "has_replied_by": _lookup_id(payload.get("replied_by__s")) is not None,
        }
        return None


def _source_ref(record: dict[str, Any]) -> str:
    """Aynı konuşmada yeni son-mesaj zamanı yeni olay olsun."""
    message_time = record.get("message_time__s") or ""
    return f"{record.get('id')}@{message_time}"


def _sort_key(record: dict[str, Any]) -> tuple[bool, datetime, str]:
    """Modified_Time artan; zamanı olmayanlar sona."""
    modified = _parse_zoho_datetime(record.get("modified_time__s"))
    record_id = str(record.get("id") or "")
    if modified is None:
        return (True, datetime.min.replace(tzinfo=ISTANBUL), record_id)
    return (False, modified, record_id)


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


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _lookup_id(lookup: Any) -> str | None:
    if isinstance(lookup, dict) and lookup.get("id"):
        return str(lookup["id"])
    return None


def _sender_ids(sender: Any) -> tuple[str | None, str | None]:
    """sender__s → (zoho_lead_id, zoho_contact_id)."""
    if not isinstance(sender, dict):
        return None, None
    module = sender.get("module")
    api_name = module.get("api_name") if isinstance(module, dict) else None
    sender_id = _lookup_id(sender)
    if sender_id is None or not isinstance(api_name, str):
        return None, None
    if api_name == "Leads":
        return sender_id, None
    if api_name == "Contacts":
        return None, sender_id
    return None, None


def _phone(raw: Any) -> str | None:
    """Normalize edilmiş telefon; engelli numaralar kimlik sayılmaz."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    normalized = normalize_phone(str(raw))
    if normalized is None:
        return None
    if client.is_identifier_blocked("phone", normalized):
        return None
    return normalized


def _load_reps() -> dict[str, tuple[str, bool]]:
    """rep_id → (category, active)."""
    query = """
        SELECT rep_id, category, active FROM reps
        WHERE org_id = %s
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(),)).fetchall()
    return {str(row[0]): (str(row[1]), bool(row[2])) for row in rows}
