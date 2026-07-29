"""Tablolarla birebir eşleşen pydantic modelleri.

Her model schema.sql'deki bir tabloya karşılık gelir. Kısıtlı text
kolonları Literal tipleriyle ifade edilir; DB'deki CHECK kısıtlarıyla
aynıdır.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# schema.sql'deki CHECK kısıtlarıyla birebir aynı değer kümeleri
Channel = Literal["call", "email", "whatsapp", "meeting", "note", "task"]
Direction = Literal["inbound", "outbound", "internal"]
BodyQuality = Literal["low", "medium", "high"]
CommitmentStatus = Literal["open", "fulfilled", "broken", "expired"]


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
