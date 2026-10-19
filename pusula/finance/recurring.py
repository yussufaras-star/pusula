"""Düzenli ödemeler: aynı karşı tarafa en az iki ayda tekrarlanan çıkışlar.

Abonelik, üretici, ortak, yüklenici ayrımı kategoriden gelir. Aynı karşı
taraf (kural anahtarı, ör. 'runpod') bir grup olur; aralık ortanca gün
farkından sınıflanır. Aylık maliyet = toplam / seçili aralığın ay sayısı.
Aktarımlar (hesap arası, şirketler arası) ve cüzdan yüklemeleri dışarıda.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from pusula.db.models import FinTransaction
from pusula.finance.analytics import is_transfer, tx_rule_key
from pusula.finance.fx import FxTable

ZERO = Decimal("0")
DAYS_PER_MONTH = Decimal("30.44")
# Son tutar ortancadan bu oranda fazlaysa "arttı"
RISE = Decimal("1.2")
# Ortanca aralık (gün) → sıklık; aralık dışı 'düzensiz'
CADENCES: tuple[tuple[int, int, str], ...] = (
    (1, 10, "sık"),
    (11, 20, "iki haftalık"),
    (21, 40, "aylık"),
    (75, 105, "üç aylık"),
    (330, 400, "yıllık"),
)
REGULAR = frozenset(name for _, _, name in CADENCES)


@dataclass(frozen=True)
class Recurring:
    key: str
    label: str
    category_id: str | None
    accounts: tuple[int, ...]
    count: int
    months: int  # hareket görülen farklı ay sayısı
    total_usd: Decimal
    median_usd: Decimal  # tek ödeme ortancası
    last_usd: Decimal
    monthly_usd: Decimal
    cadence: str
    first: date
    last: date
    next_due: date | None  # düzenli sıklıkta son + ortanca aralık
    rising: bool
    stopped: bool  # beklenen ödeme bir haftadan fazla gecikti


# İade, ödemelerden birine bu oranda yakınsa o ödemenin geri dönüşü sayılır.
REFUND_MATCH = Decimal("0.05")
LATE_DAYS = 7


def cadence_of(gap_days: float) -> str:
    for low, high, name in CADENCES:
        if low <= gap_days <= high:
            return name
    return "düzensiz"


def recurring(
    txs: Iterable[FinTransaction],
    fx: FxTable,
    kinds: Mapping[str, str],
    start: date,
    end: date,
    min_months: int = 2,
) -> list[Recurring]:
    """[start, end] çıkışlarından düzenli olanlar, aylık maliyete göre büyükten küçüğe.

    Kategorisi giriş olan çıkışlar (iade, chargeback) dahil değildir. Aynı
    karşı taraftan gelen giriş yalnız önceki bir ödemenin geri dönüşüyse
    (tutar ±%5) düşülür; ilgisiz büyük girişler ödemeleri silmez. Aylık
    maliyet, verinin başladığı günden (aralık başından geçse) hesaplanır.
    """
    groups: dict[str, list[FinTransaction]] = {}
    for tx in txs:
        if not start <= tx.booked_at <= end or is_transfer(tx, kinds):
            continue
        if tx.amount < 0 and kinds.get(tx.category_id or "", "out") == "in":
            continue
        key = tx_rule_key(tx)
        if len(key) >= 2:
            groups.setdefault(key, []).append(tx)

    seen = [t.booked_at for rows in groups.values() for t in rows]
    data_start = max(start, min(seen)) if seen else start
    span_months = max(Decimal((end - data_start).days + 1) / DAYS_PER_MONTH, Decimal("1"))
    out: list[Recurring] = []
    for key, rows in groups.items():
        payments = sorted((t for t in rows if t.amount < 0), key=lambda t: (t.booked_at, t.id or 0))
        months = {t.booked_at.strftime("%Y-%m") for t in payments}
        if len(payments) < 2 or len(months) < min_months:
            continue
        usd = [-fx.to_usd(t.amount, t.currency, t.booked_at) for t in payments]
        refunds = ZERO
        for back in (t for t in rows if t.amount > 0):
            value = fx.to_usd(back.amount, back.currency, back.booked_at)
            if any(p.booked_at <= back.booked_at and abs(u - value) <= u * REFUND_MATCH for p, u in zip(payments, usd)):
                refunds += value
        total = sum(usd, ZERO) - refunds
        if total <= 0:
            continue
        days = [t.booked_at for t in payments]
        gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days > 0]
        gap = statistics.median(gaps) if gaps else 0
        cadence = cadence_of(gap) if gaps else "düzensiz"
        median = Decimal(statistics.median(usd))
        next_due = days[-1] + timedelta(days=round(gap)) if cadence in REGULAR and gap else None
        last = payments[-1]
        cats = [t.category_id for t in payments if t.category_id]
        out.append(
            Recurring(
                key=key,
                label=(last.counterparty or last.description).strip(),
                category_id=max(set(cats), key=cats.count) if cats else None,
                accounts=tuple(sorted({t.account_id for t in payments})),
                count=len(payments),
                months=len(months),
                total_usd=total,
                median_usd=median,
                last_usd=usd[-1],
                monthly_usd=total / span_months,
                cadence=cadence,
                first=days[0],
                last=days[-1],
                next_due=next_due,
                rising=len(usd) >= 3 and usd[-1] > median * RISE,
                stopped=next_due is not None and (end - next_due).days > LATE_DAYS,
            )
        )
    return sorted(out, key=lambda r: r.monthly_usd, reverse=True)
