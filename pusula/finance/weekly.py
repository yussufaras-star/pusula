"""Haftalık bilanço oluşturma: paneldeki "Yeni hafta" ve otomatik haftalık iş.

Otomatik iş (scripts/weekly_balance.py, her sabah gun-saati) biten haftanın
bilançosunu yoksa açar: hafta pazartesi başlar, bilanço tarihi pazardır.
Bilanço tarihi geçmişte kaldığı için bakiyeler o pazarın sonuna geri
hesaplanır, cüzdanlar o güne kadarki son bakiye, kurlar o günün kurudur.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

import psycopg

from pusula.db.models import FinAccount, FinReceivable, FinSnapshotLine, FinTaxSettings, FinTransaction, FinWallet
from pusula.finance import store, tax
from pusula.finance.fx import FxTable, load_fx
from pusula.finance.snapshot import build_week_lines, intercompany_balances, previous_snapshot, week_end


class WeekExists(Exception):
    def __init__(self, company_ids: Sequence[str]) -> None:
        self.company_ids = list(company_ids)
        super().__init__(f"Bu hafta zaten var: {', '.join(company_ids)}")


def last_closed_week(today: date) -> date:
    """Pazarı bugünden önce biten son haftanın pazartesisi."""
    sunday = today - timedelta(days=today.isoweekday())
    return sunday - timedelta(days=6)


def create_week(
    conn: psycopg.Connection,
    week_date: date,
    today: date,
    company_ids: Sequence[str] | None = None,
    *,
    copy_previous: bool = True,
    include_intercompany: bool = False,
) -> list[int]:
    """Yeni hafta: hesap bakiyeleri + açık planlı kalemler + cüzdanlar + ödeme
    kuruluşunda bekleyen tahsilat + tahmini vergi + önceki elle girilenler.

    Bilanço tarihi (haftanın pazarı) bugünden önceyse hesap bakiyeleri o günün
    sonuna geri hesaplanır (güncel bakiye − sonraki hareketler), cüzdanlar o
    güne kadar girilmiş son bakiyedir ve kurlar o günün kuruyla kilitlenir;
    değilse güncel bakiye ve son kur. include_intercompany: şirketin kendi
    kayıtlarındaki net transferler (bilanço tarihine kadar) şirketler arası
    alacak / borç satırı olur.
    """
    if week_date.isoweekday() != 1:
        raise ValueError("Hafta pazartesi olmalı")
    end = week_end(week_date)
    past = end < today
    as_of = end if past else None
    year_first = date(end.year, 1, 1)
    companies = store.load_companies(conn)
    ids = list(company_ids or [c.id for c in sorted(companies, key=lambda c: c.sort)])
    existing = store.load_snapshots(conn)
    taken = [s.company_id for s in existing if s.week_date == week_date and s.company_id in ids]
    if taken:
        raise WeekExists(taken)
    accounts_ = store.load_accounts(conn)
    planned = store.load_planned(conn)
    fx_table = load_fx(conn, None if include_intercompany else year_first - timedelta(days=7))
    fx_rates = fx_table.latest_rates(day=as_of)
    # Yıl başından bugüne: geri hesap, yıl başı net varlık ve vergi için
    txs = store.load_transactions(conn, year_first - timedelta(days=1), None)
    ic_txs = store.load_transactions(conn, None, end) if include_intercompany else []
    names = {c.id: c.name for c in companies}
    # ABD vergisi yalnız ABD şirketine (LLC); AŞ / GmbH bilançosuna girmez
    us = {c.id for c in companies if c.country == "US"}
    wallets = store.load_wallets(conn)
    receivables = store.load_receivables(conn)
    tax_settings = {c: store.load_tax_settings(conn, c) for c in ids}
    created: list[int] = []
    for company_id in ids:
        previous: list[FinSnapshotLine] = []
        if copy_previous:
            prev = previous_snapshot(existing, company_id, week_date)
            if prev and prev.id is not None:
                previous = store.load_snapshot_lines(conn, [prev.id])
        ic = intercompany_balances(company_id, ic_txs, fx_table, end, names)
        own = [t for t in txs if t.company_id == company_id]
        balances = (
            {a.id: tax.balance_on(a, own, end) for a in accounts_ if a.company_id == company_id and a.id is not None}
            if past
            else None
        )
        snapshot_id = store.insert_snapshot(conn, company_id, week_date, fx_rates)
        lines = build_week_lines(
            company_id,
            accounts_,
            planned,
            previous,
            week_date,
            ic,
            wallets=tax.wallets_on(wallets, company_id, as_of),
            receivables=receivables_on(receivables, company_id, as_of),
            tax_due=(
                tax_due(tax_settings[company_id], accounts_, wallets, own, fx_table, end, as_of)
                if company_id in us
                else []
            ),
            balances=balances,
        )
        for line in lines:
            store.insert_line(conn, snapshot_id, line.__dict__)
        created.append(snapshot_id)
    return created


def receivables_on(rows: Sequence[FinReceivable], company_id: str, day: date | None) -> list[FinReceivable]:
    """Kaynak başına 'day'e kadarki son değer; day None ise en son."""
    latest: dict[str, FinReceivable] = {}
    for r in rows:
        if r.company_id == company_id and (day is None or r.as_of <= day):
            if r.source not in latest or r.as_of >= latest[r.source].as_of:
                latest[r.source] = r
    return list(latest.values())


def auto_companies(conn: psycopg.Connection) -> list[str]:
    """Otomatik bilanço: aktif hesabı olan şirketler (hesapsız şirkete boş bilanço açılmaz)."""
    active = {a.company_id for a in store.load_accounts(conn) if a.active}
    return [c.id for c in sorted(store.load_companies(conn), key=lambda c: c.sort) if c.id in active]


def create_closed_week(conn: psycopg.Connection, today: date, week_date: date | None = None) -> dict[str, object]:
    """Biten haftanın bilançosu; o hafta için bilançosu olan şirket atlanır (tekrar çalışınca bir şey yapmaz)."""
    week = week_date or last_closed_week(today)
    have = {s.company_id for s in store.load_snapshots(conn) if s.week_date == week}
    todo = [c for c in auto_companies(conn) if c not in have]
    ids = create_week(conn, week, today, todo) if todo else []
    return {"week_date": week, "created": todo, "ids": ids, "skipped": sorted(have)}


def tax_due(
    settings: FinTaxSettings,
    accounts_: list[FinAccount],
    wallets: list[FinWallet],
    txs: list[FinTransaction],
    fx_table: FxTable,
    end: date,
    as_of: date | None,
) -> list[tuple[str, Decimal]]:
    """Vergi satırları (USD): ödenmemiş geçen yıl vergisi + bugünkü yapıyla
    yıl başından bilanço tarihine kârın tahmini vergisi.

    Yıl başı net varlık bilinmiyorsa (girilmemiş, geçmiş eksik) yalnız geçen
    yıl vergisi. FDII payı girilmediyse 0. as_of None: güncel bakiyeler.
    """
    company_id = settings.company_id
    assets = tax.net_assets(company_id, accounts_, wallets, fx_table, txs=txs, day=as_of)
    out = []
    unpaid = tax.prior_tax_unpaid(txs, company_id, fx_table, end)
    if unpaid > 0:
        out.append((f"{end.year - 1} ABD vergisi (ödenecek)", unpaid))
    begin = tax.year_start(company_id, end.year, settings, accounts_, wallets, txs, fx_table)
    prof = tax.profit(assets.net, begin.net if begin else None, settings, end)
    if prof is not None:
        due = tax.structure_tax(prof.ytd, settings, settings.fdii_share or Decimal("0"))
        out.append((f"Tahmini {end.year} ABD vergisi ({tax.STRUCTURE_LABEL[settings.structure]})", due))
    return out
