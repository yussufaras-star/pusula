"""Tablolarla birebir eşleşen pydantic modelleri.

Her model schema.sql'deki bir tabloya karşılık gelir. Kısıtlı text
kolonları Literal tipleriyle ifade edilir; DB'deki CHECK kısıtlarıyla
aynıdır.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

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
    status_changed_at: datetime | None = None
    status_changed_source: str | None = None  # gercek | tahmini
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


# --- finans (fin_*) ----------------------------------------------------------
# Tutarlar Decimal: numeric(18,2) ile kayıpsız gidip gelir. Hareket tutarı
# işaretlidir (+ giriş, − çıkış); planlı kalem tutarı pozitif, yönü direction.

CompanyId = Literal["llc", "gmbh", "tr"]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
CredentialKey = Annotated[str, Field(pattern=r"^[A-Z0-9_]+$")]
Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
AccountType = Literal["bank", "fintech", "payment", "card", "shipping", "other"]
Provider = Literal["mercury", "wise", "stripe", "revolut", "csv", "manual"]
CategoryKind = Literal["in", "out", "transfer"]
TxSource = Literal["api", "csv", "manual"]
FlowDirection = Literal["in", "out"]
Recurrence = Literal["none", "weekly", "monthly"]
PlannedStatus = Literal["open", "done", "cancelled"]
SnapshotSide = Literal["asset", "liability"]
SnapshotOrigin = Literal["account", "planned", "manual"]
DecisionStatus = Literal["acik", "aksiyon", "devam", "tamam", "iptal"]
DecisionPriority = Literal["yuksek", "orta", "dusuk"]

# API ile senkronize edilen sağlayıcılar; credential_key zorunlu.
API_PROVIDERS: frozenset[str] = frozenset({"mercury", "wise", "stripe", "revolut"})


class FinCompany(BaseModel):
    """fin_companies tablosu."""

    id: CompanyId
    name: str
    country: str
    base_currency: Currency
    match_patterns: list[str] = Field(default_factory=list)
    sort: int = 0


class FinAccount(BaseModel):
    """fin_accounts tablosu. Kimlik bilgisi değil, yalnız env son eki."""

    id: int | None = None
    company_id: CompanyId
    name: str
    institution: str
    type: AccountType = "bank"
    currency: Currency
    provider: Provider = "manual"
    provider_account_id: str | None = None
    credential_key: CredentialKey | None = None
    current_balance: Money = Decimal("0")
    balance_at: datetime | None = None
    last_synced_at: datetime | None = None
    sync_error: str | None = None
    sync_warning: str | None = None
    active: bool = True
    sort: int = 0
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _api_needs_credential_key(self) -> "FinAccount":
        # DB'deki CHECK ile aynı: API sağlayıcısı env son eki olmadan çalışmaz
        if self.provider in API_PROVIDERS and not self.credential_key:
            raise ValueError("API bağlantısı için credential_key gerekli")
        return self


class FinCategory(BaseModel):
    """fin_categories tablosu."""

    id: str
    name: str
    kind: CategoryKind
    sort: int = 0


class FinTransaction(BaseModel):
    """fin_transactions tablosu. (account_id, external_id) tekil."""

    id: int | None = None
    account_id: int
    company_id: CompanyId
    booked_at: date
    amount: Money
    currency: Currency
    description: str = ""
    counterparty: str | None = None
    category_id: str | None = None
    counterparty_company_id: CompanyId | None = None
    external_id: str
    source: TxSource
    user_edited_at: datetime | None = None
    created_at: datetime | None = None


class FinFxRate(BaseModel):
    """fin_fx_rates tablosu: 1 birim currency = usd_rate USD."""

    date: date
    currency: Currency
    usd_rate: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=8)]
    source: str = "ecb"


class FinPlannedItem(BaseModel):
    """fin_planned_items tablosu."""

    id: int | None = None
    company_id: CompanyId
    direction: FlowDirection
    title: str
    counterparty: str | None = None
    amount: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
    currency: Currency
    due_date: date
    recurrence: Recurrence = "none"
    recur_until: date | None = None
    probability: Annotated[Decimal, Field(ge=0, le=1, max_digits=3, decimal_places=2)] = Decimal("1")
    status: PlannedStatus = "open"
    category_id: str | None = None
    notes: str | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _until_after_due(self) -> "FinPlannedItem":
        if self.recur_until is not None and self.recur_until < self.due_date:
            raise ValueError("recur_until, due_date'ten önce olamaz")
        return self


class FinSnapshot(BaseModel):
    """fin_snapshots tablosu: şirket × hafta (pazartesi)."""

    id: int | None = None
    company_id: CompanyId
    week_date: date
    locked: bool = False
    locked_at: datetime | None = None
    fx: dict[str, Decimal] = Field(default_factory=dict)
    notes: str = ""
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _week_starts_monday(self) -> "FinSnapshot":
        if self.week_date.isoweekday() != 1:
            raise ValueError("week_date pazartesi olmalı")
        return self


class FinSnapshotLine(BaseModel):
    """fin_snapshot_lines tablosu."""

    id: int | None = None
    snapshot_id: int
    side: SnapshotSide
    group_name: str
    label: str
    amount: Money = Decimal("0")
    currency: Currency
    origin: SnapshotOrigin = "manual"
    sort: int = 0


class FinDecision(BaseModel):
    """fin_decisions tablosu. company_id None = grup geneli."""

    id: int | None = None
    company_id: CompanyId | None = None
    title: str
    description: str = ""
    owner: str | None = None
    status: DecisionStatus = "acik"
    priority: DecisionPriority = "orta"
    due_date: date | None = None
    impact_usd: Money | None = None
    meeting_date: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
