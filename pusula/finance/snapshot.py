"""Haftalık bilanço: yeni hafta taslağı, kilitli kurla toplamlar, mail özeti.

Yeni hafta hesap bakiyeleri ve açık tek seferlik planlı kalemlerden dolar.
Önceki haftanın elle girilen pasifleri (tedarikçi, maaş vb.) kopyalanır.
Toplamlar haftaya kilitlenen kurla hesaplanır; kur yoksa satır toplama
girmez ve para birimi eksik listesine yazılır.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pusula.db.models import (
    FinAccount,
    FinCompany,
    FinDecision,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
)

ZERO = Decimal("0")

ACCOUNT_GROUP: dict[str, str] = {
    "bank": "Banka hesapları",
    "fintech": "Fintech",
    "payment": "Ödeme altyapıları",
    "card": "Kredi kartları",
    "shipping": "Kargo bakiyeleri",
    "other": "Diğer",
}
DEBT_GROUP = "Kredi kartları / Krediler"
RECEIVABLE_GROUP = "Vadeli alacaklar"
PAYABLE_GROUP = "Vadeli borçlar"
OPEN_DECISION_STATUSES = ("aksiyon", "devam", "acik")

TR_MONTHS = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


@dataclass(frozen=True)
class DraftLine:
    """Henüz snapshot_id'si olmayan satır (yeni hafta taslağı)."""

    side: str
    group_name: str
    label: str
    amount: Decimal
    currency: str
    origin: str
    sort: int


def build_week_lines(
    company_id: str,
    accounts: Iterable[FinAccount],
    planned: Iterable[FinPlannedItem],
    previous_lines: Iterable[FinSnapshotLine] = (),
) -> list[DraftLine]:
    """Yeni hafta satırları: hesaplar + açık planlı kalemler + önceki elle pasifler.

    Negatif bakiyeli hesap (kart, kredi) pasife pozitif tutarla yazılır.
    Kopyalanan pasif, otomatik gelen bir satırla aynı etiketi taşıyorsa atlanır.
    """
    lines: list[DraftLine] = []

    def push(side: str, group: str, label: str, amount: Decimal, currency: str, origin: str) -> None:
        lines.append(DraftLine(side, group, label, amount, currency, origin, len(lines)))

    for account in accounts:
        if account.company_id != company_id or not account.active:
            continue
        if account.current_balance < 0:
            push("liability", DEBT_GROUP, account.name, -account.current_balance, account.currency, "account")
        else:
            push("asset", ACCOUNT_GROUP[account.type], account.name, account.current_balance, account.currency, "account")

    for item in planned:
        if item.company_id != company_id or item.status != "open" or item.recurrence != "none":
            continue
        if item.direction == "in":
            push("asset", RECEIVABLE_GROUP, item.title, item.amount, item.currency, "planned")
        else:
            push("liability", PAYABLE_GROUP, item.title, item.amount, item.currency, "planned")

    auto = {(line.side, line.label) for line in lines}
    for prev in sorted(previous_lines, key=lambda line: line.sort):
        if prev.side != "liability" or prev.origin != "manual":
            continue
        if (prev.side, prev.label) in auto:
            continue
        push(prev.side, prev.group_name, prev.label, prev.amount, prev.currency, "manual")
    return lines


def previous_snapshot(
    snapshots: Iterable[FinSnapshot], company_id: str, week: date
) -> FinSnapshot | None:
    """Aynı şirketin verilen haftadan önceki son bilançosu."""
    earlier = [s for s in snapshots if s.company_id == company_id and s.week_date < week]
    return max(earlier, key=lambda s: s.week_date) if earlier else None


@dataclass
class Totals:
    assets: Decimal = ZERO
    liabilities: Decimal = ZERO
    missing_fx: set[str] = field(default_factory=set)

    @property
    def net(self) -> Decimal:
        return self.assets - self.liabilities


def line_usd(line: FinSnapshotLine | DraftLine, fx: Mapping[str, Decimal]) -> Decimal | None:
    """Kilitli kurla USD; kur yoksa None."""
    if line.currency == "USD":
        return line.amount
    rate = fx.get(line.currency)
    return None if rate is None else line.amount * rate


def snapshot_totals(
    lines: Iterable[FinSnapshotLine | DraftLine], fx: Mapping[str, Decimal]
) -> Totals:
    totals = Totals()
    for line in lines:
        usd = line_usd(line, fx)
        if usd is None:
            totals.missing_fx.add(line.currency)
            continue
        if line.side == "asset":
            totals.assets += usd
        else:
            totals.liabilities += usd
    return totals


@dataclass(frozen=True)
class GroupTotal:
    side: str
    name: str
    usd: Decimal


def group_totals(
    lines: Iterable[FinSnapshotLine], fx: Mapping[str, Decimal]
) -> list[GroupTotal]:
    """Taraf + grup toplamları; ilk görülme sırasıyla, önce aktifler."""
    order: list[tuple[str, str]] = []
    sums: dict[tuple[str, str], Decimal] = {}
    for line in sorted(lines, key=lambda line: line.sort):
        key = (line.side, line.group_name)
        if key not in sums:
            order.append(key)
            sums[key] = ZERO
        sums[key] += line_usd(line, fx) or ZERO
    order.sort(key=lambda k: 0 if k[0] == "asset" else 1)
    return [GroupTotal(side, name, sums[(side, name)]) for side, name in order]


# --- mail özeti ------------------------------------------------------------------


def fmt_usd(value: Decimal) -> str:
    """$1.234.567 / −$12.400 (tam sayı, TR binlik ayırıcı)."""
    whole = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    text = f"{abs(whole):,}".replace(",", ".")
    return f"−${text}" if whole < 0 else f"${text}"


def fmt_day(day: date) -> str:
    return f"{day.day} {TR_MONTHS[day.month - 1]} {day.year}"


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

    consolidated = Totals()
    blocks: list[str] = []
    for snap in current:
        lines = lines_by_snapshot.get(snap.id or 0, [])
        totals = snapshot_totals(lines, snap.fx)
        consolidated.assets += totals.assets
        consolidated.liabilities += totals.liabilities
        consolidated.missing_fx |= totals.missing_fx
        block = [
            names.get(snap.company_id, snap.company_id).upper(),
            f"Aktif {fmt_usd(totals.assets)} · Pasif {fmt_usd(totals.liabilities)} · Net {fmt_usd(totals.net)}",
        ]
        for group in group_totals(lines, snap.fx):
            side = "A" if group.side == "asset" else "P"
            block.append(f"• {side} {group.name}: {fmt_usd(group.usd)}")
        if snap.notes:
            block.append(f"Not: {snap.notes}")
        if not snap.locked:
            block.append("Kesinleşmedi.")
        blocks.append("\n".join(block))

    head = [
        f"{fmt_day(week)} haftalık finans özeti",
        "",
        "KONSOLİDE",
        f"Aktifler: {fmt_usd(consolidated.assets)}",
        f"Pasifler: {fmt_usd(consolidated.liabilities)}",
        f"Net: {fmt_usd(consolidated.net)}",
    ]
    if consolidated.missing_fx:
        head.append(f"Kur verisi yetersiz: {', '.join(sorted(consolidated.missing_fx))}")

    parts = ["\n".join(head), *blocks]
    open_decisions = [d for d in decisions if d.status in OPEN_DECISION_STATUSES]
    if open_decisions:
        rows = ["AÇIK KARARLAR"]
        for d in open_decisions:
            owner = f" ({d.owner})" if d.owner else ""
            due = f", {fmt_day(d.due_date)}" if d.due_date else ""
            rows.append(f"• {d.title}{owner}{due}")
        parts.append("\n".join(rows))
    return "\n\n".join(parts) + "\n"
