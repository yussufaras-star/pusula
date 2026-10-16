"""Haftalık bilanço: bölüm yapısı, yeni hafta taslağı, denklik, oranlar, mail özeti.

Yapı Tekdüzen Hesap Planı'nın ana başlıklarını izler (üç şirkette aynı):
Aktif = dönen + duran varlıklar; Pasif = kısa + uzun vadeli yabancı
kaynaklar + özkaynaklar. Bilanço denktir: özkaynağı girilmemiş şirkette
özkaynak "aktif − yabancı kaynak" olarak hesaplanır; girilmişse kalan fark
"denklik farkı" olarak gösterilir (eksik kalem işareti).

Yeni hafta hesap bakiyeleri, açık tek seferlik planlı kalemler ve
(istenirse) şirketler arası net transferlerden dolar; önceki haftanın elle
girilen satırları (stok, demirbaş, kredi, sermaye vb.) kopyalanır. Tutarlar
haftaya kilitlenen kurla USD'ye çevrilir; kur yoksa satır toplama girmez.
Konsolide görünümde şirketler arası alacak ve borçlar elimine edilir.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pusula.db.models import (
    FinAccount,
    FinCompany,
    FinDecision,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
    FinTransaction,
)
from pusula.finance.fx import FxTable

ZERO = Decimal("0")


@dataclass(frozen=True)
class Section:
    key: str
    side: str  # asset | liability (pasif; özkaynak dahil)
    title: str
    groups: tuple[str, ...]


SECTIONS: tuple[Section, ...] = (
    Section(
        "current_assets",
        "asset",
        "Dönen varlıklar",
        (
            "Hazır değerler",
            "Menkul kıymetler",
            "Ticari alacaklar",
            "Şirketler arası alacaklar",
            "Diğer alacaklar",
            "Stoklar",
            "Verilen avanslar ve peşin giderler",
            "Diğer dönen varlıklar",
        ),
    ),
    Section(
        "noncurrent_assets",
        "asset",
        "Duran varlıklar",
        (
            "Uzun vadeli alacaklar",
            "Mali duran varlıklar",
            "Maddi duran varlıklar",
            "Maddi olmayan duran varlıklar",
            "Diğer duran varlıklar",
        ),
    ),
    Section(
        "current_liabilities",
        "liability",
        "Kısa vadeli yabancı kaynaklar",
        (
            "Mali borçlar",
            "Ticari borçlar",
            "Şirketler arası borçlar",
            "Diğer borçlar",
            "Alınan avanslar",
            "Ödenecek vergi, SGK ve maaş",
            "Borç ve gider karşılıkları",
            "Diğer kısa vadeli yabancı kaynaklar",
        ),
    ),
    Section(
        "longterm_liabilities",
        "liability",
        "Uzun vadeli yabancı kaynaklar",
        ("Uzun vadeli mali borçlar", "Uzun vadeli ticari borçlar", "Diğer uzun vadeli borçlar"),
    ),
    Section(
        "equity",
        "liability",
        "Özkaynaklar",
        ("Ödenmiş sermaye", "Sermaye yedekleri", "Kâr yedekleri", "Geçmiş yıllar kâr / zararı", "Dönem net kâr / zararı"),
    ),
)
SECTION_BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}

CASH = "Hazır değerler"
INVENTORY = "Stoklar"
IC_RECEIVABLE = "Şirketler arası alacaklar"
IC_PAYABLE = "Şirketler arası borçlar"
IC_GROUPS = frozenset({IC_RECEIVABLE, IC_PAYABLE})
FINANCIAL_DEBT = "Mali borçlar"
TAX_PAYROLL = "Ödenecek vergi, SGK ve maaş"
TRADE_RECEIVABLE = "Ticari alacaklar"
TRADE_PAYABLE = "Ticari borçlar"
LONG_RECEIVABLE = "Uzun vadeli alacaklar"
LONG_PAYABLE = "Uzun vadeli ticari borçlar"
# Vadesi bu kadar günden uzak kalem uzun vadelidir (bilanço tarihinden 1 yıl).
LONG_TERM_DAYS = 365
TAX_CATEGORIES = frozenset({"vergi", "maas"})
# Denklik farkı bundan küçükse yuvarlama sayılır (USD).
BALANCE_TOLERANCE = Decimal("1")
OPEN_DECISION_STATUSES = ("aksiyon", "devam", "acik")

ACCOUNT_GROUP: dict[str, tuple[str, str]] = {
    # hesap türü → (bölüm, grup) pozitif bakiyede
    "bank": ("current_assets", CASH),
    "fintech": ("current_assets", CASH),
    "payment": ("current_assets", CASH),
    "card": ("current_assets", CASH),
    "shipping": ("current_assets", "Verilen avanslar ve peşin giderler"),
    "other": ("current_assets", "Diğer dönen varlıklar"),
}

TR_MONTHS = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


def default_section(side: str) -> str:
    """Bölümü boş (eski) satır: tarafın kısa vadeli bölümü."""
    return "current_assets" if side == "asset" else "current_liabilities"


def section_of(line: FinSnapshotLine | DraftLine) -> str:
    return line.section or default_section(line.side)


def side_of(section: str) -> str:
    return SECTION_BY_KEY[section].side


@dataclass(frozen=True)
class DraftLine:
    """Henüz snapshot_id'si olmayan satır (yeni hafta taslağı)."""

    side: str
    section: str
    group_name: str
    label: str
    amount: Decimal
    currency: str
    origin: str
    sort: int


# --- yeni hafta ------------------------------------------------------------------


def week_end(week: date) -> date:
    """Bilanço tarihi: haftanın pazarı."""
    return week + timedelta(days=6)


def intercompany_balances(
    company_id: str,
    transactions: Iterable[FinTransaction],
    fx: FxTable,
    until: date,
    names: Mapping[str, str],
) -> list[tuple[str, str, Decimal]]:
    """Karşı şirket başına net transfer (USD, işlem günü kuru), bu şirketin kayıtlarından.

    Pozitif: gönderilen > alınan → alacak. Negatif → borç. Dönüş:
    (karşı şirket, etiket, tutar). Sıfır olanlar atlanır.
    """
    net: dict[str, Decimal] = {}
    for tx in transactions:
        if tx.company_id != company_id or not tx.counterparty_company_id or tx.booked_at > until:
            continue
        net[tx.counterparty_company_id] = net.get(tx.counterparty_company_id, ZERO) - fx.to_usd(
            tx.amount, tx.currency, tx.booked_at
        )
    out = []
    for other in sorted(net):
        value = net[other].quantize(Decimal("0.01"))
        if value != 0:
            out.append((other, names.get(other, other), value))
    return out


def build_week_lines(
    company_id: str,
    accounts: Iterable[FinAccount],
    planned: Iterable[FinPlannedItem],
    previous_lines: Iterable[FinSnapshotLine] = (),
    week: date | None = None,
    intercompany: Sequence[tuple[str, str, Decimal]] = (),
) -> list[DraftLine]:
    """Yeni hafta satırları.

    - Hesaplar: pozitif bakiye türüne göre aktif (çoğu Hazır değerler);
      negatif bakiye (kart, kredi) kısa vadeli Mali borçlar.
    - Açık tek seferlik planlı kalemler: giriş → Ticari alacaklar, çıkış →
      Ticari borçlar (vergi/maaş kategorisi → Ödenecek vergi, SGK ve maaş);
      vadesi bilanço tarihinden 1 yıl sonrası uzun vadeli.
    - Şirketler arası net transferler: alacak / borç (USD).
    - Önceki haftanın elle girilen satırları kopyalanır; otomatik gelen bir
      satırla aynı bölüm + etiketi taşıyan kopyalanmaz.
    """
    lines: list[DraftLine] = []
    horizon = week_end(week) + timedelta(days=LONG_TERM_DAYS) if week else None

    def push(section: str, group: str, label: str, amount: Decimal, currency: str, origin: str) -> None:
        lines.append(DraftLine(side_of(section), section, group, label, amount, currency, origin, len(lines)))

    for account in accounts:
        if account.company_id != company_id or not account.active:
            continue
        if account.current_balance < 0:
            push("current_liabilities", FINANCIAL_DEBT, account.name, -account.current_balance, account.currency, "account")
        else:
            section, group = ACCOUNT_GROUP[account.type]
            push(section, group, account.name, account.current_balance, account.currency, "account")

    for item in planned:
        if item.company_id != company_id or item.status != "open" or item.recurrence != "none":
            continue
        long_term = horizon is not None and item.due_date > horizon
        if item.direction == "in":
            section, group = ("noncurrent_assets", LONG_RECEIVABLE) if long_term else ("current_assets", TRADE_RECEIVABLE)
        elif long_term:
            section, group = "longterm_liabilities", LONG_PAYABLE
        else:
            group = TAX_PAYROLL if item.category_id in TAX_CATEGORIES else TRADE_PAYABLE
            section = "current_liabilities"
        push(section, group, item.title, item.amount, item.currency, "planned")

    for _, label, usd in intercompany:
        if usd > 0:
            push("current_assets", IC_RECEIVABLE, label, usd, "USD", "intercompany")
        else:
            push("current_liabilities", IC_PAYABLE, label, -usd, "USD", "intercompany")

    auto = {(line.section, line.label) for line in lines}
    for prev in sorted(previous_lines, key=lambda line: line.sort):
        if prev.origin != "manual":
            continue
        section = section_of(prev)
        if (section, prev.label) in auto:
            continue
        push(section, prev.group_name, prev.label, prev.amount, prev.currency, "manual")
    return lines


def previous_snapshot(
    snapshots: Iterable[FinSnapshot], company_id: str, week: date
) -> FinSnapshot | None:
    """Aynı şirketin verilen haftadan önceki son bilançosu."""
    earlier = [s for s in snapshots if s.company_id == company_id and s.week_date < week]
    return max(earlier, key=lambda s: s.week_date) if earlier else None


# --- bilanço hesabı -----------------------------------------------------------------


def line_usd(line: FinSnapshotLine | DraftLine, fx: Mapping[str, Decimal]) -> Decimal | None:
    """Kilitli kurla USD; kur yoksa None."""
    if line.currency == "USD":
        return line.amount
    rate = fx.get(line.currency)
    return None if rate is None else line.amount * rate


@dataclass
class GroupTotal:
    name: str
    usd: Decimal = ZERO


@dataclass
class SectionTotal:
    key: str
    title: str
    usd: Decimal = ZERO
    groups: list[GroupTotal] = field(default_factory=list)


@dataclass
class Ratios:
    current: Decimal | None  # cari oran: dönen varlık / KV yabancı kaynak
    acid_test: Decimal | None  # (dönen − stok) / KV yabancı kaynak
    cash: Decimal | None  # hazır değerler / KV yabancı kaynak
    working_capital: Decimal  # dönen − KV yabancı kaynak
    debt_to_equity: Decimal | None  # yabancı kaynak / özkaynak
    equity_ratio: Decimal | None  # özkaynak / aktif


@dataclass
class BalanceSheet:
    sections: dict[str, SectionTotal]
    assets: Decimal
    debts: Decimal  # KV + UV yabancı kaynak
    equity_entered: Decimal  # girilen özkaynak satırları
    computed_equity: Decimal  # özkaynağı girilmemiş şirketlerde aktif − yabancı kaynak
    has_equity: bool  # en az bir şirkette özkaynak satırı girilmiş mi
    difference: Decimal  # aktif − (yabancı kaynak + özkaynak); denk bilançoda 0
    eliminated_receivables: Decimal
    eliminated_payables: Decimal
    missing_fx: set[str]
    ratios: Ratios

    @property
    def equity(self) -> Decimal:
        return self.equity_entered + self.computed_equity

    @property
    def liabilities_and_equity(self) -> Decimal:
        return self.debts + self.equity

    @property
    def balanced(self) -> bool:
        return abs(self.difference) <= BALANCE_TOLERANCE

    @property
    def elimination_gap(self) -> Decimal:
        """Konsolidede şirketler arası alacak − borç; 0 değilse kayıtlar tutmuyor."""
        return self.eliminated_receivables - self.eliminated_payables


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator <= 0 else numerator / denominator


@dataclass
class _Entity:
    """Tek şirketin (snapshot) eliminasyon öncesi toplamları; özkaynak hesabı için."""

    assets: Decimal = ZERO
    debts: Decimal = ZERO
    equity: Decimal = ZERO
    has_equity: bool = False


def balance_sheet(
    items: Iterable[tuple[FinSnapshotLine | DraftLine, Mapping[str, Decimal]]],
    eliminate_intercompany: bool = False,
) -> BalanceSheet:
    """(satır, o haftanın kilitli kurları) çiftlerinden bilanço.

    Birden çok şirketin satırları birlikte verilebilir (konsolide); şirket
    snapshot_id ile ayrılır. Özkaynak şirket başına belirlenir: girildiyse
    girilen, girilmediyse o şirketin aktif − yabancı kaynağı. Konsolidede
    şirketler arası alacak/borç düşülür; tutmayan kısım denklik farkına
    yansır (fark = şirketlerin kendi farkları − eliminasyon farkı).
    """
    sections = {
        s.key: SectionTotal(s.key, s.title, groups=[GroupTotal(g) for g in s.groups]) for s in SECTIONS
    }
    entities: dict[int, _Entity] = {}
    missing: set[str] = set()
    elim_recv = elim_pay = ZERO
    for line, fx in items:
        usd = line_usd(line, fx)
        if usd is None:
            missing.add(line.currency)
            continue
        section = section_of(line)
        entity = entities.setdefault(getattr(line, "snapshot_id", 0), _Entity())
        if section in ("current_assets", "noncurrent_assets"):
            entity.assets += usd
        elif section == "equity":
            entity.equity += usd
            entity.has_equity = entity.has_equity or usd != 0
        else:
            entity.debts += usd
        if eliminate_intercompany and line.group_name in IC_GROUPS:
            if line.group_name == IC_RECEIVABLE:
                elim_recv += usd
            else:
                elim_pay += usd
            continue
        total = sections[section]
        total.usd += usd
        group = next((g for g in total.groups if g.name == line.group_name), None)
        if group is None:
            group = GroupTotal(line.group_name)
            total.groups.append(group)
        group.usd += usd

    current_assets = sections["current_assets"].usd
    assets = current_assets + sections["noncurrent_assets"].usd
    current_debt = sections["current_liabilities"].usd
    debts = current_debt + sections["longterm_liabilities"].usd
    equity_entered = sections["equity"].usd
    computed_equity = sum((e.assets - e.debts for e in entities.values() if not e.has_equity), ZERO)
    equity = equity_entered + computed_equity

    def group_usd(section: str, name: str) -> Decimal:
        return next((g.usd for g in sections[section].groups if g.name == name), ZERO)

    ratios = Ratios(
        current=_ratio(current_assets, current_debt),
        acid_test=_ratio(current_assets - group_usd("current_assets", INVENTORY), current_debt),
        cash=_ratio(group_usd("current_assets", CASH), current_debt),
        working_capital=current_assets - current_debt,
        debt_to_equity=_ratio(debts, equity),
        equity_ratio=_ratio(equity, assets),
    )
    return BalanceSheet(
        sections=sections,
        assets=assets,
        debts=debts,
        equity_entered=equity_entered,
        computed_equity=computed_equity,
        has_equity=any(e.has_equity for e in entities.values()),
        difference=assets - debts - equity,
        eliminated_receivables=elim_recv,
        eliminated_payables=elim_pay,
        missing_fx=missing,
        ratios=ratios,
    )


def snapshot_sheet(lines: Iterable[FinSnapshotLine | DraftLine], fx: Mapping[str, Decimal]) -> BalanceSheet:
    return balance_sheet((line, fx) for line in lines)


# --- mail özeti ------------------------------------------------------------------


def fmt_usd(value: Decimal) -> str:
    """$1.234.567 / −$12.400 (tam sayı, TR binlik ayırıcı)."""
    whole = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    text = f"{abs(whole):,}".replace(",", ".")
    return f"−${text}" if whole < 0 else f"${text}"


def fmt_ratio(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}".replace(".", ",")


def fmt_day(day: date) -> str:
    return f"{day.day} {TR_MONTHS[day.month - 1]} {day.year}"


def _sheet_lines(sheet: BalanceSheet, with_groups: bool) -> list[str]:
    out = [
        f"Aktif {fmt_usd(sheet.assets)} · Yabancı kaynak {fmt_usd(sheet.debts)} · Özkaynak {fmt_usd(sheet.equity)}",
    ]
    for section in SECTIONS:
        total = sheet.sections[section.key]
        if total.usd == 0 and not any(g.usd for g in total.groups):
            continue
        out.append(f"• {section.title}: {fmt_usd(total.usd)}")
        if with_groups:
            for group in total.groups:
                if group.usd:
                    out.append(f"   – {group.name}: {fmt_usd(group.usd)}")
    r = sheet.ratios
    out.append(
        f"Cari oran {fmt_ratio(r.current)} · Asit-test {fmt_ratio(r.acid_test)} · "
        f"Nakit oranı {fmt_ratio(r.cash)} · Net işletme sermayesi {fmt_usd(r.working_capital)}"
    )
    if not sheet.balanced:
        out.append(f"Denklik farkı {fmt_usd(sheet.difference)}: eksik kalem olabilir.")
    return out


def mail_summary(
    week: date,
    companies: Sequence[FinCompany],
    snapshots: Sequence[FinSnapshot],
    lines_by_snapshot: Mapping[int, Sequence[FinSnapshotLine]],
    decisions: Iterable[FinDecision] = (),
) -> str:
    """Haftalık finans özeti, düz metin. Başlık ve imza yok; doğrudan içerik."""
    names = {c.id: c.name for c in companies}
    order = {c.id: c.sort for c in companies}
    current = sorted(
        (s for s in snapshots if s.week_date == week and s.id is not None),
        key=lambda s: order.get(s.company_id, 99),
    )
    if not current:
        return f"{fmt_day(week)} haftası için bilanço yok."

    pairs = [(line, snap.fx) for snap in current for line in lines_by_snapshot.get(snap.id or 0, [])]
    consolidated = balance_sheet(pairs, eliminate_intercompany=len(current) > 1)
    head = [f"{fmt_day(week)} haftalık finans özeti", "", "KONSOLİDE" if len(current) > 1 else names.get(current[0].company_id, "").upper()]
    head += _sheet_lines(consolidated, with_groups=len(current) == 1)
    if len(current) > 1 and (consolidated.eliminated_receivables or consolidated.eliminated_payables):
        head.append(
            f"Şirketler arası elimine: alacak {fmt_usd(consolidated.eliminated_receivables)}, "
            f"borç {fmt_usd(consolidated.eliminated_payables)}"
            + (f" (fark {fmt_usd(consolidated.elimination_gap)})" if abs(consolidated.elimination_gap) > BALANCE_TOLERANCE else "")
        )
    if consolidated.missing_fx:
        head.append(f"Kur verisi yetersiz: {', '.join(sorted(consolidated.missing_fx))}")

    parts = ["\n".join(head)]
    if len(current) > 1:
        for snap in current:
            sheet = snapshot_sheet(lines_by_snapshot.get(snap.id or 0, []), snap.fx)
            block = [names.get(snap.company_id, snap.company_id).upper(), *_sheet_lines(sheet, with_groups=True)]
            if snap.notes:
                block.append(f"Not: {snap.notes}")
            if not snap.locked:
                block.append("Kesinleşmedi.")
            parts.append("\n".join(block))
    else:
        snap = current[0]
        extra = []
        if snap.notes:
            extra.append(f"Not: {snap.notes}")
        if not snap.locked:
            extra.append("Kesinleşmedi.")
        if extra:
            parts.append("\n".join(extra))

    open_decisions = [d for d in decisions if d.status in OPEN_DECISION_STATUSES]
    if open_decisions:
        rows = ["AÇIK KARARLAR"]
        for d in open_decisions:
            owner = f" ({d.owner})" if d.owner else ""
            due = f", {fmt_day(d.due_date)}" if d.due_date else ""
            rows.append(f"• {d.title}{owner}{due}")
        parts.append("\n".join(rows))
    return "\n\n".join(parts) + "\n"
