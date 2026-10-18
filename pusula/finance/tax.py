"""LLC: satıcı cüzdanları, üretici bakiyeleri ve vergi senaryoları.

Rexven LLC'nin kasasındaki paranın bir kısmı kendisinin değildir:
- Satıcı cüzdanları: satıcıların Wise / Mercury / Stripe ile yüklediği,
  henüz harcanmamış para. Açıklamada müşteri numarası 'REX-<no>' geçer.
- Üretici bakiyeleri: üreticilere ödenecek tutar (negatifse verilen avans).
Her ikisi de şimdilik elle girilir. ABD vergisi bu borçlar düşüldükten
sonra kalan net varlık üzerinden ödenir; senaryo oranları ve düzeltmeleri
değiştirir.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pusula.db.models import FinAccount, FinTaxScenario, FinTransaction, FinWallet
from pusula.finance.fx import FxTable

ZERO = Decimal("0")
# Satıcı cüzdanı yalnız LLC'de; diğer şirketlerde 'REX' geçen açıklama sınıflanmaz.
WALLET_COMPANY = "llc"
WALLET_CATEGORY = "cuzdan"
TAX_CATEGORY = "vergi"
# 'REX-158115', 'Rex 49980', 'REX64976'; 'Rexven' eşleşmez (harften sonra rakam gerekir).
_REF = re.compile(r"(?<![a-z0-9])rex[^a-z0-9]{0,3}(\d+)", re.I)


def wallet_ref(text: str | None) -> str | None:
    """Açıklamadaki satıcı müşteri numarası ('REX-158115'), yoksa None."""
    if not text:
        return None
    match = _REF.search(text)
    return f"REX-{int(match.group(1))}" if match else None


def is_wallet_deposit(tx: FinTransaction) -> bool:
    """Otomatik 'cüzdan yüklemesi' adayı: REX referanslı giriş.

    Kullanıcının düzelttiği, şirketler arası işaretli veya elle verilmiş
    kategorisi olan hareket değişmez (kuralla verilmiş kategori değişebilir).
    """
    if tx.company_id != WALLET_COMPANY:
        return False
    if tx.amount <= 0 or tx.user_edited_at is not None or tx.counterparty_company_id:
        return False
    if tx.category_id is not None and tx.category_rule_id is None:
        return False
    return tx.category_id != WALLET_CATEGORY and wallet_ref(tx.description) is not None


# --- yüklemeler -------------------------------------------------------------------


@dataclass
class Channel:
    account_id: int
    name: str
    usd: Decimal = ZERO
    count: int = 0


@dataclass
class Customer:
    ref: str
    name: str
    usd: Decimal = ZERO
    count: int = 0
    last: date | None = None


@dataclass
class Deposits:
    usd: Decimal = ZERO
    count: int = 0
    refunds_usd: Decimal = ZERO  # cüzdan kategorisindeki çıkışlar (iade, chargeback)
    without_ref: int = 0  # referanssız yükleme (Stripe vb.)
    channels: list[Channel] = field(default_factory=list)
    customers: list[Customer] = field(default_factory=list)


def deposits(
    txs: Iterable[FinTransaction], accounts: Sequence[FinAccount], fx: FxTable
) -> Deposits:
    """Cüzdan kategorisindeki hareketler: kanal ve müşteri kırılımı (USD, işlem günü kuru)."""
    names = {a.id: a.name for a in accounts}
    out = Deposits()
    channels: dict[int, Channel] = {}
    customers: dict[str, Customer] = {}
    for tx in txs:
        if tx.category_id != WALLET_CATEGORY:
            continue
        usd = fx.to_usd(tx.amount, tx.currency, tx.booked_at)
        if tx.amount < 0:
            out.refunds_usd -= usd
            continue
        out.usd += usd
        out.count += 1
        channel = channels.setdefault(tx.account_id, Channel(tx.account_id, names.get(tx.account_id, "")))
        channel.usd += usd
        channel.count += 1
        ref = wallet_ref(tx.description)
        if ref is None:
            out.without_ref += 1
            continue
        customer = customers.setdefault(ref, Customer(ref, (tx.counterparty or "").strip()))
        customer.usd += usd
        customer.count += 1
        if customer.last is None or tx.booked_at > customer.last:
            customer.last = tx.booked_at
            customer.name = (tx.counterparty or customer.name).strip()
    out.channels = sorted(channels.values(), key=lambda c: c.usd, reverse=True)
    out.customers = sorted(customers.values(), key=lambda c: c.usd, reverse=True)
    return out


# --- vergi ------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxBase:
    cash_usd: Decimal  # aktif hesap bakiyeleri (negatif bakiye düşer)
    seller_usd: Decimal  # satıcı cüzdanları
    producer_usd: Decimal  # üretici bakiyeleri (avans negatif)
    paid_usd: Decimal  # yıl başından beri ödenen vergi

    @property
    def net_assets(self) -> Decimal:
        return self.cash_usd - self.seller_usd - self.producer_usd


def tax_base(
    company_id: str,
    accounts: Iterable[FinAccount],
    wallets: Iterable[FinWallet],
    txs: Iterable[FinTransaction],
    fx: FxTable,
    year_start: date,
) -> TaxBase:
    usd = fx.to_usd  # kur yoksa 0 ve fx.missing'e yazılır
    cash = sum(
        (usd(a.current_balance, a.currency) for a in accounts if a.company_id == company_id and a.active),
        ZERO,
    )
    seller = producer = ZERO
    for w in wallets:
        if w.company_id != company_id or not w.active:
            continue
        if w.kind == "seller":
            seller += usd(w.balance, w.currency)
        else:
            producer += usd(w.balance, w.currency)
    paid = -sum(
        (
            usd(t.amount, t.currency, t.booked_at)
            for t in txs
            if t.company_id == company_id and t.category_id == TAX_CATEGORY and t.booked_at >= year_start
        ),
        ZERO,
    )
    return TaxBase(cash, seller, producer, paid)


@dataclass(frozen=True)
class TaxResult:
    scenario: FinTaxScenario
    taxable: Decimal  # matrah
    state: Decimal
    federal: Decimal
    paid: Decimal

    @property
    def total(self) -> Decimal:
        return self.state + self.federal

    @property
    def remaining(self) -> Decimal:
        """Ödenecek kalan (yıl içinde ödenen düşülür; en az 0)."""
        return max(self.total - self.paid, ZERO)


def compute(base: TaxBase, scenario: FinTaxScenario) -> TaxResult:
    """Matrah = net varlık − ek gider − vergilenmiş birikim (en az 0).

    Eyalet vergisi önce hesaplanır ve federal matrahtan düşülür.
    """
    taxable = max(base.net_assets - scenario.extra_deductions - scenario.taxed_base, ZERO)
    state = taxable * scenario.state_rate
    federal = (taxable - state) * scenario.federal_rate
    cent = Decimal("0.01")
    return TaxResult(scenario, taxable.quantize(cent), state.quantize(cent), federal.quantize(cent), base.paid_usd)


def primary(scenarios: Iterable[FinTaxScenario], company_id: str) -> FinTaxScenario | None:
    """Bilançoya giren senaryo: şirketin ilk aktif senaryosu (sıraya göre)."""
    own = [s for s in scenarios if s.company_id == company_id and s.active]
    return min(own, key=lambda s: (s.sort, s.id or 0)) if own else None
