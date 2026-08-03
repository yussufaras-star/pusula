"""Tablolarla birebir eşleşen pydantic modelleri.

Her model schema.sql'deki bir tabloya karşılık gelir. Kısıtlı text
kolonları Literal tipleriyle ifade edilir; DB'deki CHECK kısıtlarıyla
aynıdır.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# schema.sql'deki CHECK kısıtlarıyla birebir aynı değer kümeleri
Channel = Literal["call", "email", "whatsapp", "meeting", "note", "task"]
Direction = Literal["inbound", "outbound", "internal"]
BodyQuality = Literal["low", "medium", "high"]
CommitmentStatus = Literal["open", "fulfilled", "broken", "expired"]
PusulaLeadState = Literal[
    "active", "stale", "aging", "archived", "nurture", "closed"
]


class Event(BaseModel):
    """events tablosu: tüm kanallardan gelen ham olay kayıtları."""

    id: int | None = None
    thread_id: str | None = None
    channel: Channel
    direction: Direction | None = None
    rep_id: str | None = None
    occurred_at: datetime | None = None
    source_ref: str
    body: str | None = None
    body_quality: BodyQuality | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime | None = None

    # Geçici kimlik alanları: DB'ye YAZILMAZ (exclude=True), sadece
    # resolve_thread için kullanılır. Ingester'ın to_event'i ham
    # kayıttan çıkarıp doldurur (bkz. ingest/base.py).
    phone: str | None = Field(default=None, exclude=True)
    email: str | None = Field(default=None, exclude=True)
    zoho_lead_id: str | None = Field(default=None, exclude=True)
    zoho_contact_id: str | None = Field(default=None, exclude=True)


class Thread(BaseModel):
    """threads tablosu: konuşma zinciri özeti ve durumu."""

    thread_id: str
    segment: str | None = None
    owner_rep_id: str | None = None
    first_touch_at: datetime | None = None
    last_touch_at: datetime | None = None
    touch_count_by_channel: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    created_at: datetime | None = None


class Commitment(BaseModel):
    """commitments tablosu: konuşmalardan çıkarılan taahhütler."""

    id: int | None = None
    thread_id: str | None = None
    source_event_id: int | None = None
    text: str | None = None
    quote: str | None = None
    due_at: datetime | None = None
    fulfilled_event_id: int | None = None
    status: CommitmentStatus | None = None
    created_at: datetime | None = None


class SyncState(BaseModel):
    """sync_state tablosu: kaynak bazında senkronizasyon imleci."""

    source_name: str
    last_synced_at: datetime | None = None
    last_cursor: str | None = None
    updated_at: datetime | None = None


class Lead(BaseModel):
    """leads tablosu: Zoho özeti + yerel pusula_state."""

    lead_id: str
    thread_id: str | None = None
    status: str | None = None  # Zoho Lead_Status; Pusula yazmaz
    owner_rep_id: str | None = None
    assigned_at: datetime | None = None  # Zoho Created_Time
    source: str | None = None
    full_name: str | None = None  # Zoho Full_Name; identities'e yazılmaz
    pusula_state: PusulaLeadState | None = None
    pusula_state_at: datetime | None = None
    created_at: datetime | None = None


class Contact(BaseModel):
    """contacts tablosu: Zoho Contacts + opsiyonel lead/thread bağları."""

    contact_id: str
    lead_id: str | None = None
    thread_id: str | None = None
    created_at: datetime | None = None  # Zoho Created_Time
    owner_rep_id: str | None = None
    full_name: str | None = None  # Zoho Full_Name; identities'e yazılmaz


class Deal(BaseModel):
    """deals tablosu: Zoho Deals + satış döngüsü başlangıcı."""

    deal_id: str
    contact_id: str | None = None
    lead_id: str | None = None
    thread_id: str | None = None
    stage: str | None = None
    amount: float | None = None
    created_at: datetime | None = None
    closed_at: datetime | None = None
    owner_rep_id: str | None = None
    source: str | None = None
    cycle_start_at: datetime | None = None
    cycle_start_reliable: bool | None = None
