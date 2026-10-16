"""Finans hesapları: nakit, bakiye seyri, aylık akış, projeksiyon, şirketler arası.

Tüm tutarlar USD'ye çevrilmiş Decimal döner. Şirketler arası ve transfer
kategorili hareketler gelir/gider toplamlarına ve konsolide akışa girmez.
"Bugün" parametre olarak gelir (Europe/Istanbul); fonksiyonlar saati okumaz.
"""

from __future__ import annotations

import bisect
import calendar
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from pusula.db.models import (
    API_PROVIDERS,
    FinAccount,
    FinCategory,
    FinCategoryRule,
    FinCompany,
    FinPlannedItem,
    FinTransaction,
)
from pusula.finance.fx import FxTable

ZERO = Decimal("0")
IC_CATEGORY = "ic_transfer"
# Sonsuz döngü koruması: haftalık kalem ~11 yıl, aylık ~50 yıl.
MAX_OCCURRENCES = 600
# Eşleşme toleransı: karşı şirkette ±5 gün, tutarda ±%2 (kur ve masraf farkı).
MATCH_DAYS = 5
MATCH_TOLERANCE = Decimal("0.02")
# API hesabı 2 günden eski senkron = senkronize değil (günde 2 çalışır).
API_STALE_DAYS = 2
# Ekstre hesabı 7 günden eski yükleme = güncel değil.
STATEMENT_STALE_DAYS = 7


def week_start(day: date) -> date:
    """Haftanın pazartesisi."""
    return day - timedelta(days=day.weekday())


def add_month(day: date, day_of_month: int) -> date:
    """Bir sonraki ayın aynı günü; ay kısaysa ayın son günü (31 Oca → 28 Şub)."""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day_of_month, last))


# --- sınıflandırma ------------------------------------------------------------


def is_transfer(tx: FinTransaction, kinds: Mapping[str, str]) -> bool:
    """Gelir/gider sayılmayan hareket: şirketler arası veya transfer kategorisi."""
    if tx.counterparty_company_id:
        return True
    return bool(tx.category_id) and kinds.get(tx.category_id or "") == "transfer"


def category_kinds(categories: Iterable[FinCategory]) -> dict[str, str]:
    return {c.id: c.kind for c in categories}


def fold_text(text: str) -> str:
    # Türkçe İ/I/ı tek harfe iner: str.lower() 'İ'yi 'i̇' yapar, kalıp eşleşmez
    return text.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()


def like_to_regex(pattern: str) -> re.Pattern[str]:
    """ILIKE kalıbı → regex: % = herhangi, _ = tek karakter."""
    parts: list[str] = []
    for ch in fold_text(pattern):
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.compile("^" + "".join(parts) + "$", re.S)


def match_company(
    text: str, own_company: str, companies: Sequence[FinCompany]
) -> str | None:
    """Açıklama/karşı tarafta diğer şirketin kalıbı geçiyorsa o şirket."""
    folded = fold_text(text)
    for company in companies:
        if company.id == own_company:
            continue
        if any(like_to_regex(p).match(folded) for p in company.match_patterns):
            return company.id
    return None


def auto_intercompany(
    tx: FinTransaction, companies: Sequence[FinCompany]
) -> tuple[str, str] | None:
    """(counterparty_company_id, 'ic_transfer') veya None.

    Kullanıcının düzelttiği hareket (user_edited_at dolu) hiç değişmez.
    """
    if tx.user_edited_at is not None or tx.counterparty_company_id:
        return None
    text = f"{tx.description} {tx.counterparty or ''}"
    other = match_company(text, tx.company_id, companies)
    if other is None:
        return None
    return other, IC_CATEGORY


# --- kendi hesapları arası -----------------------------------------------------------

OWN_CATEGORY = "hesap_arasi"
OWN_MATCH_DAYS = 3
OWN_MATCH_TOLERANCE = Decimal("0.005")  # aktarım ücreti payı
_LAST4 = re.compile(r"(\d{4})\s*$")
_MASKED_LAST4 = re.compile(r"(?:••|\*{2,}|x{2,}|\.{2,})\s?(\d{4})", re.I)


def _account_last4(account: FinAccount) -> str | None:
    """Hesap adının sonundaki 4 hane (ör. 'Mercury ••4259')."""
    match = _LAST4.search(account.name)
    return match.group(1) if match else None


def own_transfers(
    txs: Sequence[FinTransaction],
    accounts: Sequence[FinAccount],
    companies: Sequence[FinCompany],
    days: int = OWN_MATCH_DAYS,
    tolerance: Decimal = OWN_MATCH_TOLERANCE,
) -> set[int]:
    """Aynı şirketin kendi hesapları arasındaki aktarımların hareket kimlikleri.

    Doğrudan: açıklama/karşı tarafta aynı şirketin başka bir hesabının son 4
    hanesi geçiyor (ör. 'Mercury Checking ••4259').
    Eşleştirme: farklı iki hesapta aynı para birimi, ters işaret, ±gün ve
    ±oran içinde tutar; tesadüfi eşleşmeyi önlemek için en az bir tarafın
    metninde şirketin kendi adı/kalıbı ya da hesap numarası geçmeli.
    Şirketler arası işaretli veya kullanıcının düzelttiği hareket aday değildir.
    """
    own_patterns: dict[str, list[re.Pattern[str]]] = {
        c.id: [like_to_regex(p) for p in c.match_patterns] + [like_to_regex(f"%{c.name}%")]
        for c in companies
    }
    last4: dict[str, dict[str, int]] = {}  # şirket → son 4 hane → hesap id
    for account in accounts:
        digits = _account_last4(account)
        if digits and account.id is not None:
            last4.setdefault(account.company_id, {})[digits] = account.id

    def candidate(tx: FinTransaction) -> bool:
        return (
            tx.id is not None
            and tx.user_edited_at is None
            and not tx.counterparty_company_id
            and tx.category_id in (None, OWN_CATEGORY)
        )

    def mentions_own_account(tx: FinTransaction, text: str) -> bool:
        numbers = last4.get(tx.company_id, {})
        return any(
            numbers.get(m.group(1)) not in (None, tx.account_id) for m in _MASKED_LAST4.finditer(text)
        )

    marked: set[int] = set()
    evidence: dict[int, bool] = {}
    pool = [t for t in txs if candidate(t)]
    for tx in pool:
        raw = f"{tx.description} {tx.counterparty or ''}"
        folded = fold_text(raw)
        direct = mentions_own_account(tx, raw)
        if direct:
            marked.add(tx.id)  # type: ignore[arg-type]
        evidence[tx.id] = direct or any(  # type: ignore[index]
            rx.match(folded) for rx in own_patterns.get(tx.company_id, [])
        )

    outs = sorted((t for t in pool if t.amount < 0), key=lambda t: t.booked_at)
    ins = [t for t in pool if t.amount > 0]
    used: set[int] = set()
    for out in outs:
        best: tuple[int, FinTransaction] | None = None
        for inc in ins:
            if inc.id in used or inc.account_id == out.account_id:
                continue
            if inc.company_id != out.company_id or inc.currency != out.currency:
                continue
            gap = abs((inc.booked_at - out.booked_at).days)
            if gap > days or abs(inc.amount + out.amount) > -out.amount * tolerance:
                continue
            if not (evidence.get(out.id) or evidence.get(inc.id)):  # type: ignore[arg-type]
                continue
            if best is None or gap < best[0]:
                best = (gap, inc)
        if best is not None:
            used.add(best[1].id)  # type: ignore[arg-type]
            marked.add(out.id)  # type: ignore[arg-type]
            marked.add(best[1].id)  # type: ignore[arg-type]
    return marked


# --- otomatik kategori ---------------------------------------------------------------

_RULE_ASCII = str.maketrans("çğıİIöşüÇĞÖŞÜâîû", "cgiiiosucgosuaiu")


def rule_key(text: str | None) -> str:
    """Kural metni: küçük harf, Türkçe harfler ASCII, tek boşluk.

    Kurallar bu biçimle saklanır; değişirse kayıtlı kurallar eşleşmez.
    """
    if not text:
        return ""
    return " ".join(text.translate(_RULE_ASCII).lower().split())


def tx_rule_key(tx: FinTransaction) -> str:
    """'exact' kuralın anahtarı: karşı taraf, yoksa açıklama."""
    return rule_key(tx.counterparty) or rule_key(tx.description)


def _contains_regex(pattern: str) -> re.Pattern[str]:
    # Başta kelime sınırı; kalıp boşlukla bitiyorsa sonda da (ör. 'ups ' ≠ 'groups')
    core = re.escape(rule_key(pattern))
    tail = r"(?![a-z0-9])" if pattern.endswith(" ") else ""
    return re.compile(r"(?<![a-z0-9])" + core + tail)


def _rule_rank(rule: FinCategoryRule) -> tuple[int, int, int]:
    """Küçük olan önce: kullanıcı > sistem, exact > contains, şirkete özel > genel."""
    return (
        0 if rule.source == "user" else 1,
        0 if rule.match_type == "exact" else 1,
        0 if rule.company_id else 1,
    )


def categorize(
    txs: Iterable[FinTransaction], rules: Sequence[FinCategoryRule]
) -> dict[int, FinCategoryRule]:
    """Kategorisiz hareket → ilk uyan kural (öncelik sırasıyla).

    Aday: kategorisi boş, şirketler arası işaretsiz, kullanıcının
    düzeltmediği hareket. Yön kuralı: giriş 'in', çıkış 'out' kuralına uyar.
    """
    ordered = sorted(rules, key=_rule_rank)
    compiled = [(r, _contains_regex(r.pattern) if r.match_type == "contains" else None) for r in ordered]
    out: dict[int, FinCategoryRule] = {}
    for tx in txs:
        if tx.id is None or tx.category_id or tx.counterparty_company_id or tx.user_edited_at:
            continue
        direction = "in" if tx.amount > 0 else "out"
        exact = tx_rule_key(tx)
        text = rule_key(f"{tx.description} {tx.counterparty or ''}")
        for rule, rx in compiled:
            if rule.direction not in ("any", direction):
                continue
            if rule.company_id and rule.company_id != tx.company_id:
                continue
            hit = rule.pattern == exact if rx is None else bool(rx.search(text))
            if hit:
                out[tx.id] = rule
                break
    return out


def rule_direction_ok(direction: str, category_kind: str) -> bool:
    """Giriş kuralı gider kategorisi veremez (transfer her yöne uyar)."""
    if category_kind == "transfer" or direction == "any":
        return True
    return category_kind == direction


def top_uncategorized(
    txs: Iterable[FinTransaction], fx: FxTable, limit: int = 20
) -> list[dict[str, object]]:
    """Kategorisiz karşı taraflar, USD hacmine göre (tek seçimle kural için)."""
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    for tx in txs:
        if tx.category_id or tx.counterparty_company_id or tx.user_edited_at:
            continue
        key = tx_rule_key(tx)
        if len(key) < 2:
            continue
        direction = "in" if tx.amount > 0 else "out"
        row = groups.setdefault(
            (tx.company_id, direction, key),
            {
                "company_id": tx.company_id,
                "direction": direction,
                "pattern": key,
                "label": (tx.counterparty or tx.description).strip(),
                "count": 0,
                "usd": Decimal("0"),
            },
        )
        row["count"] = int(row["count"]) + 1  # type: ignore[call-overload]
        row["usd"] = row["usd"] + abs(fx.to_usd(tx.amount, tx.currency, tx.booked_at))  # type: ignore[operator]
    rows = sorted(groups.values(), key=lambda r: r["usd"], reverse=True)  # type: ignore[arg-type,return-value]
    return rows[:limit]


# --- nakit ve bakiye seyri -------------------------------------------------------


def cash_usd(accounts: Iterable[FinAccount], fx: FxTable) -> Decimal:
    """Aktif hesapların güncel bakiyesi, son kurla."""
    return sum(
        (fx.to_usd(a.current_balance, a.currency) for a in accounts if a.active), ZERO
    )


@dataclass(frozen=True)
class BalancePoint:
    day: date
    by_company: dict[str, Decimal]

    @property
    def total(self) -> Decimal:
        return sum(self.by_company.values(), ZERO)


def balance_history(
    accounts: Sequence[FinAccount],
    txs: Iterable[FinTransaction],
    fx: FxTable,
    start: date,
    today: date,
    company_ids: Sequence[str],
) -> list[BalancePoint]:
    """Haftalık bakiye seyri (USD): güncel bakiyeden sonraki hareketler düşülür.

    Noktalar start'ın pazartesisinden itibaren her pazartesi, sonda bugün.
    Her nokta o günün kuruyla çevrilir.
    """
    points: list[date] = []
    cursor = week_start(start)
    while cursor < today:
        points.append(cursor)
        cursor += timedelta(days=7)
    points.append(today)

    # Hesap başı: tarihe göre sıralı hareketler ve sondan kümülatif toplam
    by_account: dict[int, list[FinTransaction]] = {}
    for tx in txs:
        by_account.setdefault(tx.account_id, []).append(tx)
    suffix: dict[int, tuple[list[date], list[Decimal]]] = {}
    for account_id, rows in by_account.items():
        rows.sort(key=lambda t: t.booked_at)
        days = [t.booked_at for t in rows]
        sums = [ZERO] * (len(rows) + 1)
        for i in range(len(rows) - 1, -1, -1):
            sums[i] = sums[i + 1] + rows[i].amount
        suffix[account_id] = (days, sums)

    active = [a for a in accounts if a.active and a.company_id in company_ids]
    out: list[BalancePoint] = []
    for day in points:
        row = {c: ZERO for c in company_ids}
        for account in active:
            after = ZERO
            if account.id in suffix:
                days, sums = suffix[account.id]
                after = sums[bisect.bisect_right(days, day)]
            row[account.company_id] += fx.to_usd(account.current_balance - after, account.currency, day)
        out.append(BalancePoint(day=day, by_company=row))
    return out


# --- akış ------------------------------------------------------------------------


@dataclass
class MonthFlow:
    month: str  # YYYY-AA
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        return self.inflow - self.outflow


def monthly_flows(
    txs: Iterable[FinTransaction], fx: FxTable, kinds: Mapping[str, str]
) -> list[MonthFlow]:
    """Aylık giriş/çıkış (USD, işlem tarihindeki kur). Transferler hariç."""
    months: dict[str, MonthFlow] = {}
    for tx in txs:
        if is_transfer(tx, kinds):
            continue
        key = tx.booked_at.strftime("%Y-%m")
        row = months.setdefault(key, MonthFlow(month=key))
        usd = fx.to_usd(tx.amount, tx.currency, tx.booked_at)
        if usd >= 0:
            row.inflow += usd
        else:
            row.outflow -= usd
    return [months[k] for k in sorted(months)]


@dataclass
class CategoryTotal:
    id: str | None
    name: str
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO
    count: int = 0


def category_totals(
    txs: Iterable[FinTransaction], fx: FxTable, categories: Sequence[FinCategory]
) -> list[CategoryTotal]:
    """Kategori kırılımı, büyükten küçüğe. Transferler hariç."""
    names = {c.id: c.name for c in categories}
    kinds = category_kinds(categories)
    totals: dict[str | None, CategoryTotal] = {}
    for tx in txs:
        if is_transfer(tx, kinds):
            continue
        key = tx.category_id if tx.category_id in names else None
        row = totals.setdefault(key, CategoryTotal(id=key, name=names.get(key or "", "Kategorisiz")))
        usd = fx.to_usd(tx.amount, tx.currency, tx.booked_at)
        if usd >= 0:
            row.inflow += usd
        else:
            row.outflow -= usd
        row.count += 1
    return sorted(totals.values(), key=lambda r: r.inflow + r.outflow, reverse=True)


# --- projeksiyon -----------------------------------------------------------------


@dataclass(frozen=True)
class Occurrence:
    item: FinPlannedItem
    day: date
    overdue: bool


def expand_planned(
    items: Iterable[FinPlannedItem], start: date, end: date
) -> list[Occurrence]:
    """Açık kalemleri [start, end] aralığındaki oluşumlara açar.

    Vadesi geçmiş tek seferlik kalem gecikmiş sayılır ve start gününe düşer.
    Tekrarlayan kalemin start öncesi oluşumları atlanır. Aylık tekrar ayın
    gününü korur, kısa ayda son güne iner (31 Oca → 28 Şub → 31 Mar).
    """
    out: list[Occurrence] = []
    for item in items:
        if item.status != "open":
            continue
        if item.recurrence == "none":
            if item.due_date <= end:
                overdue = item.due_date < start
                out.append(Occurrence(item, start if overdue else item.due_date, overdue))
            continue
        until = min(item.recur_until, end) if item.recur_until else end
        day = item.due_date
        for _ in range(MAX_OCCURRENCES):
            if day > until:
                break
            if day >= start:
                out.append(Occurrence(item, day, False))
            if item.recurrence == "weekly":
                day += timedelta(days=7)
            else:
                day = add_month(day, item.due_date.day)
    return out


@dataclass(frozen=True)
class ProjItem:
    item_id: int | None
    title: str
    company_id: str
    day: date
    usd: Decimal  # işaretli: + giriş, − çıkış
    overdue: bool


@dataclass
class ProjWeek:
    week: date
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO
    close: Decimal = ZERO
    items: list[ProjItem] = field(default_factory=list)


def projection(
    start_cash: Decimal,
    items: Iterable[FinPlannedItem],
    fx: FxTable,
    today: date,
    weeks: int = 13,
    weighted: bool = True,
) -> list[ProjWeek]:
    """Haftalık kasa projeksiyonu (USD, son kur).

    weighted: tutar × olasılık; False ise tam tutar. Kapanış = önceki
    kapanış + giriş − çıkış; ilk hafta bugünkü kasadan başlar.
    """
    first = week_start(today)
    end = first + timedelta(days=weeks * 7 - 1)
    occurrences = expand_planned(items, today, end)
    rows: list[ProjWeek] = []
    balance = start_cash
    for i in range(weeks):
        ws = first + timedelta(days=7 * i)
        we = ws + timedelta(days=6)
        row = ProjWeek(week=ws)
        for occ in occurrences:
            if not ws <= occ.day <= we:
                continue
            usd = fx.to_usd(occ.item.amount, occ.item.currency)
            if weighted:
                usd *= occ.item.probability
            if occ.item.direction == "in":
                row.inflow += usd
                signed = usd
            else:
                row.outflow += usd
                signed = -usd
            row.items.append(
                ProjItem(
                    item_id=occ.item.id,
                    title=occ.item.title,
                    company_id=occ.item.company_id,
                    day=occ.day,
                    usd=signed,
                    overdue=occ.overdue,
                )
            )
        balance += row.inflow - row.outflow
        row.close = balance
        rows.append(row)
    return rows


def lowest_week(rows: Sequence[ProjWeek]) -> ProjWeek | None:
    """En düşük kapanışlı hafta; eşitlikte ilki."""
    return min(rows, key=lambda r: r.close) if rows else None


def first_below(rows: Sequence[ProjWeek], floor: Decimal) -> ProjWeek | None:
    """Kapanışı alt limitin altına inen ilk hafta."""
    return next((r for r in rows if r.close < floor), None)


# --- şirketler arası -------------------------------------------------------------


def intercompany_matrix(
    txs: Iterable[FinTransaction], fx: FxTable
) -> dict[tuple[str, str], Decimal]:
    """(gönderen, alan) → USD. Gönderen tarafın çıkış kayıtlarından."""
    matrix: dict[tuple[str, str], Decimal] = {}
    for tx in txs:
        if not tx.counterparty_company_id or tx.amount >= 0:
            continue
        key = (tx.company_id, tx.counterparty_company_id)
        matrix[key] = matrix.get(key, ZERO) - fx.to_usd(tx.amount, tx.currency, tx.booked_at)
    return matrix


@dataclass(frozen=True)
class PairPosition:
    a: str
    b: str
    a_to_b: Decimal
    b_to_a: Decimal

    @property
    def net(self) -> Decimal:
        """Pozitifse a, b'ye net fon sağlamış (b'nin a'ya borcu)."""
        return self.a_to_b - self.b_to_a


def pair_positions(
    matrix: Mapping[tuple[str, str], Decimal], company_ids: Sequence[str]
) -> list[PairPosition]:
    out: list[PairPosition] = []
    for i, a in enumerate(company_ids):
        for b in company_ids[i + 1 :]:
            out.append(
                PairPosition(a, b, matrix.get((a, b), ZERO), matrix.get((b, a), ZERO))
            )
    return out


@dataclass(frozen=True)
class Unmatched:
    outgoing: list[FinTransaction]  # gönderende çıkış var, alanda giriş yok
    incoming: list[FinTransaction]  # alanda giriş var, gönderende çıkış yok


def unmatched_transfers(
    txs: Iterable[FinTransaction],
    fx: FxTable,
    days: int = MATCH_DAYS,
    tolerance: Decimal = MATCH_TOLERANCE,
) -> Unmatched:
    """Karşılığı bulunamayan şirketler arası hareketler (±gün, ±oran).

    Eşleşme bire birdir: bir giriş yalnız bir çıkışı karşılar; adaylar
    arasında tarihi en yakın olan seçilir.
    """
    ic = [t for t in txs if t.counterparty_company_id]
    outs = sorted((t for t in ic if t.amount < 0), key=lambda t: t.booked_at)
    ins = [t for t in ic if t.amount > 0]
    used: set[int] = set()
    lonely_out: list[FinTransaction] = []
    for out in outs:
        usd = -fx.to_usd(out.amount, out.currency, out.booked_at)
        best: tuple[int, int] | None = None  # (gün farkı, giriş indeksi)
        for idx, inc in enumerate(ins):
            if idx in used:
                continue
            if inc.company_id != out.counterparty_company_id or inc.counterparty_company_id != out.company_id:
                continue
            gap = abs((inc.booked_at - out.booked_at).days)
            if gap > days:
                continue
            if abs(fx.to_usd(inc.amount, inc.currency, inc.booked_at) - usd) > usd * tolerance:
                continue
            if best is None or gap < best[0]:
                best = (gap, idx)
        if best is None:
            lonely_out.append(out)
        else:
            used.add(best[1])
    lonely_in = [t for idx, t in enumerate(ins) if idx not in used]
    return Unmatched(outgoing=lonely_out, incoming=sorted(lonely_in, key=lambda t: t.booked_at))


# --- uyarılar --------------------------------------------------------------------


@dataclass(frozen=True)
class Alerts:
    cash_gap: ProjWeek | None  # kapanışı 0'ın altına inen ilk hafta
    overdue: list[FinPlannedItem]  # vadesi geçen tek seferlik açık kalemler
    overdue_usd: Decimal
    sync_errors: list[FinAccount]
    stale: list[FinAccount]  # senkronu / ekstresi eski hesaplar


def _is_stale(account: FinAccount, now: datetime) -> bool:
    if account.provider == "manual":
        return False
    limit = API_STALE_DAYS if account.provider in API_PROVIDERS else STATEMENT_STALE_DAYS
    last = account.last_synced_at
    return last is None or now - last > timedelta(days=limit)


def overview_alerts(
    accounts: Iterable[FinAccount],
    planned: Iterable[FinPlannedItem],
    proj: Sequence[ProjWeek],
    fx: FxTable,
    now: datetime,
) -> Alerts:
    active = [a for a in accounts if a.active]
    today = now.date()
    overdue = [
        p for p in planned if p.status == "open" and p.recurrence == "none" and p.due_date < today
    ]
    return Alerts(
        cash_gap=first_below(proj, ZERO),
        overdue=overdue,
        overdue_usd=sum((fx.to_usd(p.amount, p.currency) for p in overdue), ZERO),
        sync_errors=[a for a in active if a.sync_error],
        stale=[a for a in active if not a.sync_error and _is_stale(a, now)],
    )
