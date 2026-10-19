"""Finans ekranlarının yanıtları: yüklenmiş veriden saf hesap.

Her fonksiyon Bundle + kapsam (şirket listesi) alır, JSON'a hazır sözlük
döner. Tutarlar USD, 2 haneye yuvarlanır. Kur bulunamayan para birimleri
`missing_fx` ile bildirilir; arayüz "kur verisi yetersiz" gösterir.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pusula.db.models import (
    FinAccount,
    FinCategory,
    FinCompany,
    FinDecision,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
    FinTaxSettings,
    FinTransaction,
    FinWallet,
)
from pusula.finance import analytics, recurring, tax
from pusula.finance.fx import FxTable
from pusula.finance.snapshot import SECTIONS, BalanceSheet, balance_sheet, section_of

ALL = "all"
TX_PAGE = 100
TX_PAGE_MAX = 500
TX_KINDS = ("all", "in", "out", "transfer", "uncategorized")
OPEN_DECISIONS = ("acik", "aksiyon", "devam")


@dataclass
class Bundle:
    companies: list[FinCompany]
    categories: list[FinCategory] = field(default_factory=list)
    accounts: list[FinAccount] = field(default_factory=list)
    transactions: list[FinTransaction] = field(default_factory=list)
    planned: list[FinPlannedItem] = field(default_factory=list)
    decisions: list[FinDecision] = field(default_factory=list)
    fx: FxTable = field(default_factory=lambda: FxTable([]))


def money(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(Decimal("0.01")))


def scope_ids(company: str, companies: Sequence[FinCompany]) -> list[str]:
    """'all' → tüm şirketler (sıralı); tek şirket → [id]. Bilinmeyen → ValueError."""
    ids = [c.id for c in sorted(companies, key=lambda c: (c.sort, c.id))]
    if company == ALL:
        return ids
    if company not in ids:
        raise ValueError("Bilinmeyen şirket")
    return [company]


def _fx_meta(fx: FxTable) -> dict[str, Any]:
    return {
        "fx_date": fx.latest_date(),
        "fx": {cur: float(rate) for cur, rate in fx.latest_rates().items()},
        "missing_fx": sorted(fx.missing),
    }


def account_out(a: FinAccount, fx: FxTable) -> dict[str, Any]:
    return {
        "id": a.id,
        "company_id": a.company_id,
        "name": a.name,
        "institution": a.institution,
        "type": a.type,
        "currency": a.currency,
        "provider": a.provider,
        "provider_account_id": a.provider_account_id,
        "credential_key": a.credential_key,
        "balance": money(a.current_balance),
        "balance_usd": money(fx.to_usd(a.current_balance, a.currency)),
        "balance_at": a.balance_at,
        "last_synced_at": a.last_synced_at,
        "sync_error": a.sync_error,
        "sync_warning": a.sync_warning,
        "active": a.active,
        "sort": a.sort,
    }


def tx_out(t: FinTransaction, fx: FxTable, account_names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": t.id,
        "account_id": t.account_id,
        "account": account_names.get(t.account_id, ""),
        "company_id": t.company_id,
        "booked_at": t.booked_at,
        "amount": money(t.amount),
        "currency": t.currency,
        "amount_usd": money(fx.to_usd(t.amount, t.currency, t.booked_at)),
        "description": t.description,
        "counterparty": t.counterparty,
        "category_id": t.category_id,
        "counterparty_company_id": t.counterparty_company_id,
        "source": t.source,
        "edited": t.user_edited_at is not None,
    }


# --- meta / hesaplar -------------------------------------------------------------


def meta_view(bundle: Bundle) -> dict[str, Any]:
    return {
        "companies": [c.model_dump() for c in sorted(bundle.companies, key=lambda c: c.sort)],
        "categories": [c.model_dump() for c in sorted(bundle.categories, key=lambda c: c.sort)],
        **_fx_meta(bundle.fx),
    }


def accounts_view(bundle: Bundle, scope: Sequence[str]) -> dict[str, Any]:
    rows = [
        account_out(a, bundle.fx)
        for a in sorted(bundle.accounts, key=lambda a: (a.company_id, a.sort, a.id or 0))
        if a.company_id in scope
    ]
    return {"accounts": rows, **_fx_meta(bundle.fx)}


# --- genel bakış -----------------------------------------------------------------


def _local(usd: Decimal, currency: str, fx: FxTable) -> Decimal | None:
    rate = fx.rate(currency)
    return None if rate is None else usd / rate


def overview_view(
    bundle: Bundle, scope: Sequence[str], start: date, now: datetime
) -> dict[str, Any]:
    today = now.date()
    fx = bundle.fx
    accounts = [a for a in bundle.accounts if a.company_id in scope]
    txs = [t for t in bundle.transactions if t.company_id in scope]
    planned = [p for p in bundle.planned if p.company_id in scope]
    kinds = analytics.category_kinds(bundle.categories)

    cash = analytics.cash_usd(accounts, fx)
    history = analytics.balance_history(accounts, txs, fx, start, today, scope)
    flows = analytics.monthly_flows([t for t in txs if start <= t.booked_at <= today], fx, kinds)
    proj = analytics.projection(cash, planned, fx, today, weeks=13)
    low = analytics.lowest_week(proj)
    alerts = analytics.overview_alerts(accounts, planned, proj, fx, now)

    cards = []
    for company in sorted(bundle.companies, key=lambda c: c.sort):
        if company.id not in scope:
            continue
        own = [a for a in accounts if a.company_id == company.id]
        usd = analytics.cash_usd(own, fx)
        local = _local(usd, company.base_currency, fx)
        cards.append(
            {
                "id": company.id,
                "name": company.name,
                "currency": company.base_currency,
                "cash_usd": money(usd),
                "cash_local": money(local),
                "accounts": sum(1 for a in own if a.active),
            }
        )

    start_cash = history[0].total if history else cash
    actions = [
        d for d in bundle.decisions if d.status == "aksiyon" and (d.company_id is None or d.company_id in scope)
    ]
    return {
        "as_of": now,
        "cash_usd": money(cash),
        "start_cash_usd": money(start_cash),
        "change_usd": money(cash - start_cash),
        "net_flow_usd": money(sum((f.net for f in flows), Decimal("0"))),
        "companies": cards,
        "history": [
            {"day": p.day, "total": money(p.total), **{c: money(v) for c, v in p.by_company.items()}}
            for p in history
        ],
        "projection": [{"week": w.week, "close": money(w.close)} for w in proj],
        "lowest": {"week": low.week, "close": money(low.close)} if low else None,
        "alerts": {
            "cash_gap": {"week": alerts.cash_gap.week, "close": money(alerts.cash_gap.close)}
            if alerts.cash_gap
            else None,
            "overdue": {"count": len(alerts.overdue), "usd": money(alerts.overdue_usd)},
            "sync_errors": [{"id": a.id, "name": a.name, "error": a.sync_error} for a in alerts.sync_errors],
            "stale": [{"id": a.id, "name": a.name, "last_synced_at": a.last_synced_at} for a in alerts.stale],
            "warnings": [
                {"id": a.id, "name": a.name, "warning": a.sync_warning}
                for a in accounts
                if a.active and a.sync_warning
            ],
        },
        "open_actions": len(actions),
        **_fx_meta(fx),
    }


# --- nakit akışı -----------------------------------------------------------------


def _kind_match(t: FinTransaction, kind: str, kinds: dict[str, str]) -> bool:
    transfer = analytics.is_transfer(t, kinds)
    if kind == "transfer":
        return transfer
    if kind == "in":
        return not transfer and t.amount > 0
    if kind == "out":
        return not transfer and t.amount < 0
    if kind == "uncategorized":
        return not transfer and not t.category_id
    return True


def flows_view(
    bundle: Bundle,
    scope: Sequence[str],
    start: date,
    end: date,
    *,
    kind: str = "all",
    category: str | None = None,
    account_id: int | None = None,
    query: str | None = None,
    limit: int = TX_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    fx = bundle.fx
    kinds = analytics.category_kinds(bundle.categories)
    txs = [t for t in bundle.transactions if t.company_id in scope and start <= t.booked_at <= end]
    flows = analytics.monthly_flows(txs, fx, kinds)
    cats = analytics.category_totals(txs, fx, bundle.categories)

    listed = [t for t in txs if _kind_match(t, kind, kinds)]
    if category:
        listed = [t for t in listed if (t.category_id or "_none") == category]
    if account_id is not None:
        listed = [t for t in listed if t.account_id == account_id]
    if query:
        needle = analytics.fold_text(query)
        listed = [
            t for t in listed if needle in analytics.fold_text(f"{t.description} {t.counterparty or ''}")
        ]
    listed.sort(key=lambda t: (t.booked_at, t.id or 0), reverse=True)
    names = {a.id: a.name for a in bundle.accounts if a.id is not None}
    limit = max(1, min(limit, TX_PAGE_MAX))
    return {
        "months": [
            {"month": f.month, "in": money(f.inflow), "out": money(f.outflow), "net": money(f.net)} for f in flows
        ],
        "categories": [
            {"id": c.id, "name": c.name, "in": money(c.inflow), "out": money(c.outflow), "count": c.count}
            for c in cats
        ],
        "totals": {
            "in": money(sum((f.inflow for f in flows), Decimal("0"))),
            "out": money(sum((f.outflow for f in flows), Decimal("0"))),
            "net": money(sum((f.net for f in flows), Decimal("0"))),
        },
        "transactions": [tx_out(t, fx, names) for t in listed[offset : offset + limit]],
        "total": len(listed),
        **_fx_meta(fx),
    }


# --- projeksiyon -----------------------------------------------------------------


def planned_out(p: FinPlannedItem, fx: FxTable, today: date) -> dict[str, Any]:
    return {
        **p.model_dump(),
        "amount": money(p.amount),
        "probability": float(p.probability),
        "amount_usd": money(fx.to_usd(p.amount, p.currency)),
        "overdue": p.status == "open" and p.recurrence == "none" and p.due_date < today,
    }


def projection_view(
    bundle: Bundle,
    scope: Sequence[str],
    today: date,
    *,
    weeks: int = 13,
    weighted: bool = True,
    floor: Decimal = Decimal("0"),
) -> dict[str, Any]:
    fx = bundle.fx
    accounts = [a for a in bundle.accounts if a.company_id in scope]
    planned = [p for p in bundle.planned if p.company_id in scope]
    cash = analytics.cash_usd(accounts, fx)
    rows = analytics.projection(cash, planned, fx, today, weeks=weeks, weighted=weighted)
    low = analytics.lowest_week(rows)
    breach = analytics.first_below(rows, floor)
    return {
        "start_cash_usd": money(cash),
        "weeks": [
            {
                "week": r.week,
                "in": money(r.inflow),
                "out": money(r.outflow),
                "close": money(r.close),
                "items": [
                    {
                        "item_id": i.item_id,
                        "title": i.title,
                        "company_id": i.company_id,
                        "day": i.day,
                        "usd": money(i.usd),
                        "overdue": i.overdue,
                    }
                    for i in r.items
                ],
            }
            for r in rows
        ],
        "lowest": {"week": low.week, "close": money(low.close)} if low else None,
        "first_below": {"week": breach.week, "close": money(breach.close)} if breach else None,
        "floor": money(floor),
        "weighted": weighted,
        "items": [
            planned_out(p, fx, today)
            for p in sorted(planned, key=lambda p: (p.status != "open", p.due_date))
        ],
        **_fx_meta(fx),
    }


# --- şirketler arası -------------------------------------------------------------


def intercompany_view(bundle: Bundle, scope: Sequence[str], start: date, end: date) -> dict[str, Any]:
    fx = bundle.fx
    loaded = [t for t in bundle.transactions if t.counterparty_company_id]
    in_range = [t for t in loaded if start <= t.booked_at <= end]

    def touches(t: FinTransaction) -> bool:
        return t.company_id in scope or t.counterparty_company_id in scope

    def shown(t: FinTransaction) -> bool:
        return touches(t) and start <= t.booked_at <= end

    # Eşleşme her iki tarafın kaydını ister: aralık kenarındaki karşılık da
    # (çağıranın yüklediği ±gün payı) sayılır; kapsam ve aralık sonra uygulanır.
    unmatched = analytics.unmatched_transfers(loaded, fx)
    visible = [t for t in in_range if touches(t)]
    matrix = analytics.intercompany_matrix(visible, fx)
    ids = [c.id for c in sorted(bundle.companies, key=lambda c: c.sort)]
    names = {a.id: a.name for a in bundle.accounts if a.id is not None}
    return {
        "matrix": [
            {"from": a, "to": b, "usd": money(v)} for (a, b), v in sorted(matrix.items())
        ],
        "pairs": [
            {"a": p.a, "b": p.b, "a_to_b": money(p.a_to_b), "b_to_a": money(p.b_to_a), "net": money(p.net)}
            for p in analytics.pair_positions(matrix, ids)
            if p.a in scope or p.b in scope
        ],
        "unmatched_outgoing": [tx_out(t, fx, names) for t in unmatched.outgoing if shown(t)],
        "unmatched_incoming": [tx_out(t, fx, names) for t in unmatched.incoming if shown(t)],
        "transactions": [
            tx_out(t, fx, names) for t in sorted(visible, key=lambda t: t.booked_at, reverse=True)
        ],
        **_fx_meta(fx),
    }


# --- haftalık bilanço ------------------------------------------------------------


def _ratio_out(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(Decimal("0.01")))


def sheet_out(sheet: BalanceSheet) -> dict[str, Any]:
    """Bilanço yanıtı: bölüm → grup toplamları, denklik, eliminasyon, oranlar."""
    r = sheet.ratios
    return {
        "assets_usd": money(sheet.assets),
        "debts_usd": money(sheet.debts),
        "equity_usd": money(sheet.equity),
        "equity_entered_usd": money(sheet.equity_entered),
        "computed_equity_usd": money(sheet.computed_equity),
        "has_equity": sheet.has_equity,
        "liabilities_and_equity_usd": money(sheet.liabilities_and_equity),
        "difference_usd": money(sheet.difference),
        "balanced": sheet.balanced,
        "eliminated_receivables_usd": money(sheet.eliminated_receivables),
        "eliminated_payables_usd": money(sheet.eliminated_payables),
        "elimination_gap_usd": money(sheet.elimination_gap),
        "sections": [
            {
                "key": sec.key,
                "title": sec.title,
                "side": sec.side,
                "usd": money(sheet.sections[sec.key].usd),
                "groups": [
                    {"name": g.name, "usd": money(g.usd)} for g in sheet.sections[sec.key].groups
                ],
            }
            for sec in SECTIONS
        ],
        "ratios": {
            "current": _ratio_out(r.current),
            "acid_test": _ratio_out(r.acid_test),
            "cash": _ratio_out(r.cash),
            "working_capital_usd": money(r.working_capital),
            "debt_to_equity": _ratio_out(r.debt_to_equity),
            "equity_ratio": _ratio_out(r.equity_ratio),
        },
        "missing_fx": sorted(sheet.missing_fx),
    }


def _brief(sheet: BalanceSheet) -> dict[str, Any]:
    return {
        "assets_usd": money(sheet.assets),
        "debts_usd": money(sheet.debts),
        "equity_usd": money(sheet.equity),
    }


def snapshots_view(
    snapshots: Sequence[FinSnapshot],
    lines: Iterable[FinSnapshotLine],
    scope: Sequence[str],
    week: date | None,
) -> dict[str, Any]:
    """Hafta listesi + seçili haftanın şirket bilançoları (kilitli kurla).

    Birden çok şirket kapsamda ise konsolide bilanço da döner; şirketler
    arası alacak ve borçlar elimine edilir. Her bilanço, aynı şirketin
    önceki haftasıyla kıyaslanır.
    """
    in_scope = [s for s in snapshots if s.company_id in scope]
    weeks = sorted({s.week_date for s in in_scope}, reverse=True)
    chosen = week or (weeks[0] if weeks else None)
    by_snapshot: dict[int, list[FinSnapshotLine]] = {}
    for line in lines:
        by_snapshot.setdefault(line.snapshot_id, []).append(line)

    def pairs(snaps: Iterable[FinSnapshot]) -> list[tuple[FinSnapshotLine, dict[str, Decimal]]]:
        return [(line, snap.fx) for snap in snaps for line in by_snapshot.get(snap.id or 0, [])]

    current = sorted((s for s in in_scope if s.week_date == chosen), key=lambda s: scope.index(s.company_id))
    cards = []
    for snap in current:
        snap_lines = sorted(by_snapshot.get(snap.id or 0, []), key=lambda l: l.sort)
        sheet = balance_sheet(pairs([snap]))
        prev = max(
            (s for s in in_scope if s.company_id == snap.company_id and s.week_date < snap.week_date),
            key=lambda s: s.week_date,
            default=None,
        )
        cards.append(
            {
                "id": snap.id,
                "company_id": snap.company_id,
                "week_date": snap.week_date,
                "locked": snap.locked,
                "locked_at": snap.locked_at,
                "notes": snap.notes,
                "fx": {k: float(v) for k, v in snap.fx.items()},
                "sheet": sheet_out(sheet),
                "previous": {"week": prev.week_date, **_brief(balance_sheet(pairs([prev])))} if prev else None,
                "lines": [
                    {**l.model_dump(), "section": section_of(l), "amount": money(l.amount)} for l in snap_lines
                ],
            }
        )

    consolidated = None
    previous_consolidated = None
    if len(scope) > 1 and current:
        consolidated = sheet_out(balance_sheet(pairs(current), eliminate_intercompany=True))
        earlier = [w for w in weeks if chosen and w < chosen]
        if earlier:
            prev_snaps = [s for s in in_scope if s.week_date == earlier[0]]
            previous_consolidated = {
                "week": earlier[0],
                **_brief(balance_sheet(pairs(prev_snaps), eliminate_intercompany=True)),
            }
    totals_sheet = balance_sheet(pairs(current), eliminate_intercompany=len(scope) > 1)
    return {
        "weeks": weeks,
        "week": chosen,
        "snapshots": cards,
        "consolidated": consolidated,
        "previous_consolidated": previous_consolidated,
        "totals": _brief(totals_sheet),
        "missing_fx": sorted(totals_sheet.missing_fx),
        "catalog": [
            {"key": sec.key, "title": sec.title, "side": sec.side, "groups": list(sec.groups)} for sec in SECTIONS
        ],
    }


# --- kararlar --------------------------------------------------------------------


def decisions_view(decisions: Sequence[FinDecision], scope: Sequence[str], today: date) -> dict[str, Any]:
    visible = [d for d in decisions if d.company_id is None or d.company_id in scope]
    active = [d for d in visible if d.status in OPEN_DECISIONS]
    overdue = [d for d in active if d.due_date and d.due_date < today]
    priority = {"yuksek": 0, "orta": 1, "dusuk": 2}
    ordered = sorted(
        visible,
        key=lambda d: (
            d.status not in OPEN_DECISIONS,
            priority.get(d.priority, 9),
            d.due_date or date.max,
        ),
    )
    return {
        "decisions": [
            {
                **d.model_dump(),
                "impact_usd": money(d.impact_usd),
                "overdue": d in overdue,
            }
            for d in ordered
        ],
        "stats": {
            "open": len(active),
            "action": sum(1 for d in visible if d.status == "aksiyon"),
            "overdue": len(overdue),
            "impact_usd": money(sum((d.impact_usd or Decimal("0") for d in active), Decimal("0"))),
        },
    }


# --- LLC cüzdan ve vergi ------------------------------------------------------------

TOP_CUSTOMERS = 15


def _rate_out(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(Decimal("0.0001")))


def settings_out(st: FinTaxSettings) -> dict[str, Any]:
    return {
        "year_start_net_assets": money(st.year_start_net_assets),
        "distributions": money(st.distributions),
        "contributions": money(st.contributions),
        "structure": st.structure,
        "dividend_ratio": _rate_out(st.dividend_ratio),
        "fdii_share": _rate_out(st.fdii_share),
        "partners": [{"name": p.name, "share": _rate_out(p.share)} for p in st.partners],
        "updated_at": st.updated_at,
    }


def tax_view(
    bundle: Bundle,
    wallets: Sequence[FinWallet],
    settings: FinTaxSettings,
    company_id: str,
    start: date,
    end: date,
    now: datetime,
) -> dict[str, Any]:
    """Net varlık, yılın kârı ve beş yapıda ABD vergisi.

    Kâr yıl başı net varlık girilmeden hesaplanamaz (profit None, senaryo
    yok). Senaryolar yıl sonu tahmini kâr üzerinden; ortak ve yüklenici
    ödemeleri yıl başından (kayıtlı hareketlerden) bilgi amaçlı.
    Yüklemeler [start, end] aralığından.
    """
    fx = bundle.fx
    today = now.date()
    year_start = date(today.year, 1, 1)
    assets = tax.net_assets(company_id, bundle.accounts, wallets, fx)
    own = [t for t in bundle.transactions if t.company_id == company_id]
    dep = tax.deposits([t for t in own if start <= t.booked_at <= end], bundle.accounts, fx)
    estimate = tax.foreign_share_estimate(dep)
    foreign = settings.fdii_share if settings.fdii_share is not None else (estimate or Decimal("0"))
    prof = tax.profit(assets.net, settings, today)
    scenarios = tax.scenarios(prof.projected, settings, foreign) if prof else []
    current = tax.STRUCTURE_SCENARIO[settings.structure]
    accounts = [a for a in bundle.accounts if a.company_id == company_id and a.active]
    first_tx = min((t.booked_at for t in own), default=None)
    return {
        "company_id": company_id,
        "as_of": now,
        "year": today.year,
        "start": start,
        "end": end,
        "base": {
            "cash_usd": money(assets.cash_usd),
            "seller_usd": money(assets.seller_usd),
            "producer_usd": money(assets.producer_usd),
            "net_assets_usd": money(assets.net),
        },
        "profit": (
            {"ytd_usd": money(prof.ytd), "projected_usd": money(prof.projected), "elapsed_days": prof.elapsed_days}
            if prof
            else None
        ),
        "payments": {
            "since": max(year_start, first_tx) if first_tx else year_start,
            "partners_usd": money(tax.outflow_total(own, company_id, tax.PARTNER_CATEGORY, year_start, fx)),
            "contractors_usd": money(tax.outflow_total(own, company_id, tax.CONTRACTOR_CATEGORY, year_start, fx)),
            "irs_usd": money(tax.outflow_total(own, company_id, "vergi", year_start, fx)),
        },
        "settings": settings_out(settings),
        "foreign_share": _rate_out(foreign),
        "foreign_share_estimate": _rate_out(estimate),
        "scenarios": [
            {
                "key": sc.key,
                "title": sc.title,
                "note": sc.note,
                "tax_usd": money(sc.tax),
                "rate": _rate_out(sc.tax / prof.projected) if prof and prof.projected > 0 else None,
                "current": sc.key == current,
                "partners": [
                    {"name": r.name, "share": _rate_out(r.share), "income_usd": money(r.income), "tax_usd": money(r.tax)}
                    for r in sc.partners
                ],
            }
            for sc in scenarios
        ],
        "accounts": [
            {"id": a.id, "name": a.name, "currency": a.currency, "balance": money(a.current_balance),
             "balance_usd": money(fx.to_usd(a.current_balance, a.currency)), "balance_at": a.balance_at}
            for a in sorted(accounts, key=lambda a: (a.sort, a.id or 0))
        ],
        "wallets": [
            {**w.model_dump(), "balance": money(w.balance), "balance_usd": money(fx.to_usd(w.balance, w.currency))}
            for w in wallets
            if w.company_id == company_id
        ],
        "deposits": {
            "usd": money(dep.usd),
            "count": dep.count,
            "refunds_usd": money(dep.refunds_usd),
            "without_ref": dep.without_ref,
            "customers": len(dep.customers),
            "channels": [
                {"account_id": c.account_id, "name": c.name, "usd": money(c.usd), "count": c.count} for c in dep.channels
            ],
            "top": [
                {"ref": c.ref, "name": c.name, "usd": money(c.usd), "count": c.count, "last": c.last}
                for c in dep.customers[:TOP_CUSTOMERS]
            ],
        },
        **_fx_meta(fx),
    }


# --- düzenli ödemeler -----------------------------------------------------------


def recurring_view(bundle: Bundle, scope: Sequence[str], start: date, end: date) -> dict[str, Any]:
    """Düzenli çıkışlar + kategori toplamları (aylık USD)."""
    kinds = analytics.category_kinds(bundle.categories)
    names = {c.id: c.name for c in bundle.categories}
    accounts = {a.id: a for a in bundle.accounts if a.id is not None}
    txs = [t for t in bundle.transactions if t.company_id in scope]
    rows = recurring.recurring(txs, bundle.fx, kinds, start, end)
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r.category_id or "_none"
        g = groups.setdefault(key, {"id": key, "name": names.get(r.category_id or "", "Kategorisiz"), "monthly_usd": Decimal("0"), "count": 0})
        g["monthly_usd"] += r.monthly_usd
        g["count"] += 1
    return {
        "start": start,
        "end": end,
        "monthly_usd": money(sum((r.monthly_usd for r in rows), Decimal("0"))),
        "groups": [
            {**g, "monthly_usd": money(g["monthly_usd"])}
            for g in sorted(groups.values(), key=lambda g: g["monthly_usd"], reverse=True)
        ],
        "items": [
            {
                "key": r.key,
                "label": r.label,
                "category_id": r.category_id,
                "company_id": accounts[r.accounts[0]].company_id if r.accounts and r.accounts[0] in accounts else None,
                "accounts": [accounts[a].name for a in r.accounts if a in accounts],
                "count": r.count,
                "months": r.months,
                "cadence": r.cadence,
                "total_usd": money(r.total_usd),
                "median_usd": money(r.median_usd),
                "last_usd": money(r.last_usd),
                "monthly_usd": money(r.monthly_usd),
                "yearly_usd": money(r.monthly_usd * 12),
                "first": r.first,
                "last": r.last,
                "next_due": r.next_due,
                "rising": r.rising,
                "stopped": r.stopped,
            }
            for r in rows
        ],
        **_fx_meta(bundle.fx),
    }


def default_start(today: date) -> date:
    """Varsayılan aralık: son 6 ay."""
    return today - timedelta(days=182)
