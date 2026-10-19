"""LLC: satıcı cüzdanları, üretici bakiyeleri ve vergi senaryoları.

Rexven LLC'nin kasasındaki paranın bir kısmı kendisinin değildir:
- Satıcı cüzdanları: satıcıların Wise / Mercury / Stripe ile yüklediği,
  henüz harcanmamış para. Açıklamada müşteri numarası 'REX-<no>' geçer.
- Üretici bakiyeleri: üreticilere ödenecek tutar (negatifse verilen avans).
Her ikisi de şimdilik elle girilir.

ABD vergisi varlık üzerinden değil yılın kârı üzerinden alınır. Kâr net
varlık değişiminden çıkarılır: net varlık = hesaplar − satıcı cüzdanları −
üretici bakiyeleri; kâr = bugünkü − yıl başı net varlık + kâr dağıtımı −
konulan sermaye. Ortaklara operating agreement ile ödenen guaranteed
payment gider olduğundan net varlığı zaten düşürür, geri eklenmez.
Senaryolar aynı kârı farklı yapılarda vergiler (ortaklık / C-Corp).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pusula.db.models import FinAccount, FinTaxSettings, FinTransaction, FinWallet
from pusula.finance.fx import FxTable

ZERO = Decimal("0")
# Satıcı cüzdanı yalnız LLC'de; diğer şirketlerde 'REX' geçen açıklama sınıflanmaz.
WALLET_COMPANY = "llc"
WALLET_CATEGORY = "cuzdan"
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


# --- kâr ---------------------------------------------------------------------------

PARTNER_CATEGORY = "ortak"
CONTRACTOR_CATEGORY = "yuklenici"
CENT = Decimal("0.01")


@dataclass(frozen=True)
class NetAssets:
    cash_usd: Decimal  # aktif hesap bakiyeleri (negatif bakiye düşer)
    seller_usd: Decimal  # satıcı cüzdanları
    producer_usd: Decimal  # üretici bakiyeleri (avans negatif)

    @property
    def net(self) -> Decimal:
        return self.cash_usd - self.seller_usd - self.producer_usd


def net_assets(
    company_id: str, accounts: Iterable[FinAccount], wallets: Iterable[FinWallet], fx: FxTable
) -> NetAssets:
    """Güncel bakiyeler, son kurla (kur yoksa 0 ve fx.missing'e yazılır)."""
    cash = sum(
        (fx.to_usd(a.current_balance, a.currency) for a in accounts if a.company_id == company_id and a.active),
        ZERO,
    )
    seller = producer = ZERO
    for w in wallets:
        if w.company_id != company_id or not w.active:
            continue
        if w.kind == "seller":
            seller += fx.to_usd(w.balance, w.currency)
        else:
            producer += fx.to_usd(w.balance, w.currency)
    return NetAssets(cash, seller, producer)


def outflow_total(
    txs: Iterable[FinTransaction], company_id: str, category: str, since: date, fx: FxTable
) -> Decimal:
    """Kategorinin net çıkışı (USD, pozitif); iade girişleri düşer."""
    return -sum(
        (
            fx.to_usd(t.amount, t.currency, t.booked_at)
            for t in txs
            if t.company_id == company_id and t.category_id == category and t.booked_at >= since
        ),
        ZERO,
    )


@dataclass(frozen=True)
class Profit:
    ytd: Decimal  # yıl başından bugüne
    projected: Decimal  # yıl sonu tahmini (geçen gün oranıyla)
    elapsed_days: int


def profit(net_now: Decimal, settings: FinTaxSettings, today: date) -> Profit | None:
    """Yıl başı net varlık girilmediyse None."""
    if settings.year_start_net_assets is None:
        return None
    ytd = net_now - settings.year_start_net_assets + settings.distributions - settings.contributions
    start = date(today.year, 1, 1)
    elapsed = (today - start).days + 1
    year_days = (date(today.year + 1, 1, 1) - start).days
    return Profit(ytd, ytd * year_days / elapsed, elapsed)


# --- vergi ------------------------------------------------------------------------

# 2026 ABD gelir vergisi tarifesi (tek kişi). Yabancı ortak 1040-NR verir,
# standart indirim yoktur. Üst sınır USD; None = üstü.
BRACKETS_2026: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("12400"), Decimal("0.10")),
    (Decimal("50400"), Decimal("0.12")),
    (Decimal("105700"), Decimal("0.22")),
    (Decimal("201775"), Decimal("0.24")),
    (Decimal("256225"), Decimal("0.32")),
    (Decimal("640600"), Decimal("0.35")),
    (None, Decimal("0.37")),
)
CORPORATE_RATE = Decimal("0.21")
# FDII (2026'dan itibaren, OBBBA): yurtdışı müşteriye hizmet kârına etkin oran.
FDII_RATE = Decimal("0.14")
# ABD–Türkiye anlaşması: kişi ortağa kâr payında kaynakta kesinti.
DIVIDEND_RATE = Decimal("0.20")
# Bilançoya giren senaryo: şirketin bugünkü yapısı.
STRUCTURE_SCENARIO = {"none": "none", "partnership": "partnership", "ccorp": "ccorp_fdii"}
STRUCTURE_LABEL = {"none": "faaliyetsiz ortaklık", "partnership": "ortaklık", "ccorp": "C-Corp"}


def individual_tax(income: Decimal) -> Decimal:
    tax = ZERO
    low = ZERO
    for top, rate in BRACKETS_2026:
        if income <= low:
            break
        upper = income if top is None else min(income, top)
        tax += (upper - low) * rate
        if top is None:
            break
        low = top
    return tax


@dataclass(frozen=True)
class PartnerTax:
    name: str
    share: Decimal
    income: Decimal
    tax: Decimal


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    note: str
    tax: Decimal
    partners: tuple[PartnerTax, ...] = ()


def partner_rows(settings: FinTaxSettings) -> list[tuple[str, Decimal]]:
    """Ortaklar + kimseye atanmamış kalan pay (toplam %100 değilse)."""
    rows = [(p.name, p.share) for p in settings.partners if p.share > 0]
    rest = Decimal("1") - sum((s for _, s in rows), ZERO)
    if rest > Decimal("0.0001"):
        rows.append(("Atanmamış pay", rest))
    return rows


def _pct(value: Decimal) -> str:
    return f"%{(value * 100).quantize(Decimal('1'))}"


def scenarios(base_profit: Decimal, settings: FinTaxSettings, foreign_share: Decimal) -> list[Scenario]:
    """Aynı kârın beş yapıdaki ABD federal vergisi. Kâr ≤ 0 ise vergi 0."""
    p = max(base_profit, ZERO)
    partners = tuple(
        PartnerTax(name, share, (p * share).quantize(CENT), individual_tax(p * share).quantize(CENT))
        for name, share in partner_rows(settings)
    )
    fdii = p * foreign_share * FDII_RATE + p * (1 - foreign_share) * CORPORATE_RATE
    dividend = (p - fdii) * settings.dividend_ratio * DIVIDEND_RATE
    out = [
        Scenario("none", "Ortaklık · ABD'de faaliyet yok", "ABD'de çalışan, ofis, depo, acente yok; vergi Türkiye'de", ZERO),
        Scenario(
            "partnership",
            "Ortaklık · ABD faaliyetine bağlı",
            "bugünkü beyan şekli: her ortak payı kadar 1040-NR, kademeli %10–37",
            sum((r.tax for r in partners), ZERO),
            partners,
        ),
        Scenario("ccorp", "C-Corp · kâr şirkette", "%21, dağıtım yok", p * CORPORATE_RATE),
        Scenario(
            "ccorp_fdii",
            "C-Corp + FDII",
            f"yurtdışı müşteri payı {_pct(foreign_share)} → %14, kalanı %21",
            fdii,
        ),
        Scenario(
            "ccorp_dividend",
            "C-Corp + FDII + kâr dağıtımı",
            f"vergi sonrası kârın {_pct(settings.dividend_ratio)}'i dağıtılır, %20 kesinti",
            fdii + dividend,
        ),
    ]
    return [Scenario(s.key, s.title, s.note, s.tax.quantize(CENT), s.partners) for s in out]


# Satıcı adı ABD şirketi gibi görünüyorsa (LLC, Inc, Corp) yurtiçi müşteri sayılır.
_US_ENTITY = re.compile(r"(?<![a-z0-9])(llc|l\.l\.c|inc|corp|corporation)(?![a-z0-9])", re.I)


def foreign_share_estimate(dep: Deposits) -> Decimal | None:
    """REX numaralı yüklemelerde ABD şirketi görünmeyen satıcıların tutar payı (FDII tahmini)."""
    total = sum((c.usd for c in dep.customers), ZERO)
    if total <= 0:
        return None
    foreign = sum((c.usd for c in dep.customers if not _US_ENTITY.search(c.name)), ZERO)
    return (foreign / total).quantize(Decimal("0.0001"))


def structure_tax(base_profit: Decimal, settings: FinTaxSettings, foreign_share: Decimal) -> Decimal:
    """Şirketin bugünkü yapısına göre tahmini vergi (bilanço satırı)."""
    key = STRUCTURE_SCENARIO[settings.structure]
    return next(s.tax for s in scenarios(base_profit, settings, foreign_share) if s.key == key)
