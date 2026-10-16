"""Finans hesapları: akış, projeksiyon, tekrar, şirketler arası, bakiye seyri."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pusula.db.models import (
    FinAccount,
    FinCategory,
    FinCompany,
    FinFxRate,
    FinPlannedItem,
    FinTransaction,
)
from pusula.finance.analytics import (
    add_month,
    auto_intercompany,
    balance_history,
    cash_usd,
    category_totals,
    expand_planned,
    first_below,
    intercompany_matrix,
    lowest_week,
    match_company,
    monthly_flows,
    overview_alerts,
    pair_positions,
    projection,
    unmatched_transfers,
    week_start,
)
from pusula.finance.fx import FxTable

TZ = ZoneInfo("Europe/Istanbul")
D = Decimal

CATEGORIES = [
    FinCategory(id="satis", name="Satış / Platform geliri", kind="in"),
    FinCategory(id="maas", name="Maaş + SGK + Vergi", kind="out"),
    FinCategory(id="ic_transfer", name="Şirketler arası transfer", kind="transfer"),
    FinCategory(id="hesap_arasi", name="Kendi hesapları arası", kind="transfer"),
]
KINDS = {c.id: c.kind for c in CATEGORIES}
COMPANIES = [
    FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", match_patterns=["%rexven llc%"]),
    FinCompany(id="gmbh", name="Rexven GmbH", country="DE", base_currency="EUR", match_patterns=["%rexven gmbh%"]),
    FinCompany(
        id="tr",
        name="Rexven TR",
        country="TR",
        base_currency="TRY",
        match_patterns=["%rexven%teknoloji%", "%rexven%a.ş%"],
    ),
]


def _fx() -> FxTable:
    return FxTable(
        [
            FinFxRate(date=date(2026, 1, 1), currency="EUR", usd_rate=D("1.10")),
            FinFxRate(date=date(2026, 9, 1), currency="EUR", usd_rate=D("1.20")),
            FinFxRate(date=date(2026, 1, 1), currency="TRY", usd_rate=D("0.025")),
        ]
    )


_seq = iter(range(1, 10_000))


def _tx(company: str, day: date, amount: str, currency: str = "USD", **kw) -> FinTransaction:
    n = next(_seq)
    return FinTransaction(
        id=n,
        account_id=kw.pop("account_id", 1),
        company_id=company,
        booked_at=day,
        amount=D(amount),
        currency=currency,
        external_id=f"x{n}",
        source="api",
        **kw,
    )


def _item(**kw) -> FinPlannedItem:
    base = dict(
        company_id="llc",
        direction="out",
        title="Kalem",
        amount=D("100"),
        currency="USD",
        due_date=date(2026, 10, 20),
    )
    base.update(kw)
    return FinPlannedItem(**base)


# --- tarih yardımcıları ----------------------------------------------------------


def test_week_start_is_monday() -> None:
    assert week_start(date(2026, 10, 16)) == date(2026, 10, 12)
    assert week_start(date(2026, 10, 12)) == date(2026, 10, 12)


def test_add_month_clamps_to_month_end() -> None:
    assert add_month(date(2026, 1, 31), 31) == date(2026, 2, 28)
    assert add_month(date(2028, 1, 31), 31) == date(2028, 2, 29)
    assert add_month(date(2026, 2, 28), 31) == date(2026, 3, 31)
    assert add_month(date(2026, 12, 15), 15) == date(2027, 1, 15)


# --- akış ------------------------------------------------------------------------


def test_transfers_excluded_from_monthly_flow() -> None:
    txs = [
        _tx("llc", date(2026, 9, 3), "1000", category_id="satis"),
        _tx("llc", date(2026, 9, 10), "-300", category_id="maas"),
        _tx("llc", date(2026, 9, 12), "-5000", counterparty_company_id="tr", category_id="ic_transfer"),
        _tx("tr", date(2026, 9, 13), "200000", "TRY", counterparty_company_id="llc"),
        _tx("llc", date(2026, 9, 14), "-700", category_id="hesap_arasi"),
        _tx("llc", date(2026, 10, 1), "50"),
    ]
    flows = monthly_flows(txs, _fx(), KINDS)
    assert [(f.month, f.inflow, f.outflow, f.net) for f in flows] == [
        ("2026-09", D("1000"), D("300"), D("700")),
        ("2026-10", D("50"), D("0"), D("50")),
    ]


def test_monthly_flow_uses_rate_on_transaction_date() -> None:
    txs = [
        _tx("gmbh", date(2026, 8, 31), "100", "EUR"),
        _tx("gmbh", date(2026, 9, 1), "100", "EUR"),
    ]
    flows = monthly_flows(txs, _fx(), KINDS)
    assert flows[0].inflow == D("110.00")
    assert flows[1].inflow == D("120.00")


def test_category_totals_skip_transfers_and_name_uncategorized() -> None:
    txs = [
        _tx("llc", date(2026, 9, 3), "1000", category_id="satis"),
        _tx("llc", date(2026, 9, 4), "-40"),
        _tx("llc", date(2026, 9, 5), "-5000", category_id="ic_transfer"),
    ]
    totals = category_totals(txs, _fx(), CATEGORIES)
    assert [(t.name, t.inflow, t.outflow, t.count) for t in totals] == [
        ("Satış / Platform geliri", D("1000"), D("0"), 1),
        ("Kategorisiz", D("0"), D("40"), 1),
    ]


# --- projeksiyon -----------------------------------------------------------------


def test_monthly_recurrence_keeps_day_and_clamps() -> None:
    item = _item(due_date=date(2026, 1, 31), recurrence="monthly")
    days = [o.day for o in expand_planned([item], date(2026, 1, 1), date(2026, 5, 31))]
    assert days == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]


def test_recurrence_respects_until_and_start() -> None:
    weekly = _item(due_date=date(2026, 9, 1), recurrence="weekly", recur_until=date(2026, 10, 1))
    days = [o.day for o in expand_planned([weekly], date(2026, 9, 10), date(2026, 12, 31))]
    assert days == [date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29)]


def test_overdue_one_off_lands_on_start_and_closed_items_skip() -> None:
    items = [
        _item(due_date=date(2026, 9, 1)),
        _item(due_date=date(2026, 9, 1), status="done"),
        _item(due_date=date(2027, 9, 1)),
    ]
    occ = expand_planned(items, date(2026, 10, 16), date(2026, 12, 31))
    assert [(o.day, o.overdue) for o in occ] == [(date(2026, 10, 16), True)]


def test_projection_closings_weighted_and_full() -> None:
    today = date(2026, 10, 14)  # çarşamba; ilk hafta 12 Eki
    items = [
        _item(direction="in", amount=D("1000"), due_date=date(2026, 10, 15), probability=D("0.5")),
        _item(direction="out", amount=D("300"), due_date=date(2026, 10, 21)),
        _item(direction="out", amount=D("100"), currency="EUR", due_date=date(2026, 10, 28)),
    ]
    weighted = projection(D("2000"), items, _fx(), today, weeks=3)
    assert [r.week for r in weighted] == [date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)]
    assert [r.close for r in weighted] == [D("2500.0"), D("2200.0"), D("2080.00")]
    full = projection(D("2000"), items, _fx(), today, weeks=3, weighted=False)
    assert [r.close for r in full] == [D("3000"), D("2700"), D("2580.00")]
    assert weighted[2].items[0].usd == D("-120.00")


def test_projection_low_point_and_floor() -> None:
    items = [
        _item(amount=D("1500"), due_date=date(2026, 10, 20)),
        _item(direction="in", amount=D("2000"), due_date=date(2026, 10, 27)),
    ]
    rows = projection(D("1000"), items, _fx(), date(2026, 10, 14), weeks=3)
    low = lowest_week(rows)
    assert low is not None and low.week == date(2026, 10, 19) and low.close == D("-500")
    assert first_below(rows, D("0")) is low
    assert first_below(rows, D("-1000")) is None


def test_projection_has_13_and_26_week_horizons() -> None:
    assert len(projection(D("0"), [], _fx(), date(2026, 10, 14))) == 13
    assert len(projection(D("0"), [], _fx(), date(2026, 10, 14), weeks=26)) == 26


# --- nakit ve bakiye seyri -------------------------------------------------------


def _account(**kw) -> FinAccount:
    base = dict(id=1, company_id="llc", name="Ops", institution="Mercury", currency="USD")
    base.update(kw)
    return FinAccount(**base)


def test_cash_uses_latest_rate_and_active_accounts() -> None:
    accounts = [
        _account(current_balance=D("1000")),
        _account(id=2, company_id="gmbh", currency="EUR", current_balance=D("100")),
        _account(id=3, current_balance=D("999"), active=False),
    ]
    assert cash_usd(accounts, _fx()) == D("1120.00")


def test_balance_history_rewinds_from_current_balance() -> None:
    account = _account(current_balance=D("1000"))
    txs = [
        _tx("llc", date(2026, 10, 6), "200"),
        _tx("llc", date(2026, 10, 13), "-50"),
    ]
    points = balance_history([account], txs, _fx(), date(2026, 10, 1), date(2026, 10, 16), ["llc"])
    assert [(p.day, p.by_company["llc"]) for p in points] == [
        (date(2026, 9, 28), D("850")),
        (date(2026, 10, 5), D("850")),
        (date(2026, 10, 12), D("1050")),
        (date(2026, 10, 16), D("1000")),
    ]


# --- şirketler arası -------------------------------------------------------------


def test_match_company_by_pattern_case_insensitive_turkish() -> None:
    assert match_company("SWIFT REXVEN TEKNOLOJİ A.Ş.", "llc", COMPANIES) == "tr"
    assert match_company("Transfer from Rexven LLC", "gmbh", COMPANIES) == "llc"
    # kendi adı karşı şirket sayılmaz
    assert match_company("Rexven LLC payroll", "llc", COMPANIES) is None
    assert match_company("Amazon payout", "llc", COMPANIES) is None


def test_auto_intercompany_never_overrides_user_edit() -> None:
    tx = _tx("llc", date(2026, 9, 1), "-100", description="to Rexven GmbH")
    assert auto_intercompany(tx, COMPANIES) == ("gmbh", "ic_transfer")
    edited = tx.model_copy(update={"user_edited_at": datetime(2026, 9, 2, tzinfo=TZ)})
    assert auto_intercompany(edited, COMPANIES) is None


def test_intercompany_matrix_and_net_position() -> None:
    txs = [
        _tx("llc", date(2026, 9, 1), "-1000", counterparty_company_id="tr"),
        _tx("tr", date(2026, 9, 2), "40000", "TRY", counterparty_company_id="llc"),
        _tx("tr", date(2026, 9, 20), "-8000", "TRY", counterparty_company_id="llc"),
        _tx("gmbh", date(2026, 9, 5), "-100", "EUR", counterparty_company_id="llc"),
    ]
    matrix = intercompany_matrix(txs, _fx())
    assert matrix == {("llc", "tr"): D("1000"), ("tr", "llc"): D("200.000"), ("gmbh", "llc"): D("120.00")}
    pairs = {(p.a, p.b): p for p in pair_positions(matrix, ["llc", "gmbh", "tr"])}
    assert pairs[("llc", "tr")].net == D("800.000")
    assert pairs[("llc", "gmbh")].net == D("-120.00")
    assert pairs[("gmbh", "tr")].net == D("0")


def test_unmatched_transfers_window_tolerance_one_to_one() -> None:
    out_ok = _tx("llc", date(2026, 9, 1), "-1000", counterparty_company_id="tr")
    in_ok = _tx("tr", date(2026, 9, 4), "39500", "TRY", counterparty_company_id="llc")  # 987.5 USD, %1.25
    out_dup = _tx("llc", date(2026, 9, 2), "-1000", counterparty_company_id="tr")  # aynı girişi paylaşamaz
    out_late = _tx("llc", date(2026, 9, 10), "-500", counterparty_company_id="tr")
    in_late = _tx("tr", date(2026, 9, 16), "20000", "TRY", counterparty_company_id="llc")  # 6 gün
    out_far = _tx("llc", date(2026, 9, 20), "-1000", counterparty_company_id="tr")
    in_far = _tx("tr", date(2026, 9, 20), "38000", "TRY", counterparty_company_id="llc")  # %5 fark
    result = unmatched_transfers(
        [out_ok, in_ok, out_dup, out_late, in_late, out_far, in_far], _fx()
    )
    assert [t.id for t in result.outgoing] == [out_dup.id, out_late.id, out_far.id]
    assert [t.id for t in result.incoming] == [in_late.id, in_far.id]


# --- uyarılar --------------------------------------------------------------------


def test_overview_alerts() -> None:
    now = datetime(2026, 10, 16, 9, 0, tzinfo=TZ)
    accounts = [
        _account(provider="mercury", credential_key="LLC", last_synced_at=now - timedelta(hours=10)),
        _account(id=2, provider="wise", credential_key="LLC", sync_error="401"),
        _account(id=3, provider="stripe", credential_key="LLC", last_synced_at=now - timedelta(days=3)),
        _account(id=4, provider="csv", last_synced_at=now - timedelta(days=5)),
        _account(id=5, provider="csv", last_synced_at=None),
        _account(id=6, provider="manual"),
    ]
    planned = [
        _item(due_date=date(2026, 10, 1), amount=D("50")),
        _item(due_date=date(2026, 10, 1), recurrence="monthly"),
        _item(due_date=date(2026, 10, 30)),
    ]
    proj = projection(D("100"), planned, _fx(), now.date(), weeks=4)
    alerts = overview_alerts(accounts, planned, proj, _fx(), now)
    assert [a.id for a in alerts.sync_errors] == [2]
    assert [a.id for a in alerts.stale] == [3, 5]
    assert len(alerts.overdue) == 1 and alerts.overdue_usd == D("50")
    assert alerts.cash_gap is not None and alerts.cash_gap.week == date(2026, 10, 26)


# --- kendi hesapları arası -------------------------------------------------------


def test_own_transfers_direct_and_paired() -> None:
    from pusula.finance.analytics import own_transfers

    accounts = [
        _account(id=1, name="Mercury ••4259"),
        _account(id=2, name="Mercury ••6808"),
        _account(id=3, name="Wise USD", provider="wise", credential_key="LLC"),
        _account(id=4, company_id="gmbh", name="Wise EUR", currency="EUR", provider="wise", credential_key="LLC"),
    ]
    direct_out = _tx("llc", date(2026, 9, 3), "-500", account_id=2, description="Mercury Checking ••4259")
    direct_in = _tx("llc", date(2026, 9, 3), "500", account_id=1, description="Mercury Checking ••6808")
    own_digits = _tx("llc", date(2026, 9, 3), "-20", account_id=1, description="Fee ••4259")  # kendi hesabı, aktarım değil
    big_out = _tx("llc", date(2026, 9, 10), "-550000", account_id=1, counterparty="Neshe LLC")
    big_in = _tx("llc", date(2026, 9, 11), "549000", account_id=3, counterparty="REXVEN LLC")  # %0,18 ücret
    # tesadüfi aynı tutar: kanıt yok → eşleşmez
    cust_in = _tx("llc", date(2026, 9, 15), "1000", account_id=1, counterparty="Müşteri A")
    supp_out = _tx("llc", date(2026, 9, 15), "-1000", account_id=3, counterparty="Tedarikçi B")
    # başka şirket, ic işaretli, kullanıcı düzeltmesi → aday değil
    other_co = _tx("gmbh", date(2026, 9, 11), "549000", "EUR", account_id=4, counterparty="REXVEN LLC")
    ic = _tx("llc", date(2026, 9, 12), "-300", account_id=3, counterparty="Rexven GmbH", counterparty_company_id="gmbh")
    edited = _tx("llc", date(2026, 9, 3), "-1", account_id=2, description="Mercury Checking ••4259",
                 user_edited_at=datetime(2026, 9, 4, tzinfo=TZ))
    marked = own_transfers(
        [direct_out, direct_in, own_digits, big_out, big_in, cust_in, supp_out, other_co, ic, edited],
        accounts,
        COMPANIES,
    )
    assert marked == {direct_out.id, direct_in.id, big_out.id, big_in.id}


def test_own_transfer_window_and_tolerance() -> None:
    from pusula.finance.analytics import own_transfers

    accounts = [_account(id=1, name="Mercury ••4259"), _account(id=3, name="Wise USD", provider="wise", credential_key="LLC")]
    late_out = _tx("llc", date(2026, 9, 1), "-1000", account_id=1)
    late_in = _tx("llc", date(2026, 9, 5), "1000", account_id=3, counterparty="REXVEN LLC")  # 4 gün
    far_out = _tx("llc", date(2026, 9, 10), "-1000", account_id=1)
    far_in = _tx("llc", date(2026, 9, 10), "980", account_id=3, counterparty="REXVEN LLC")  # %2 fark
    assert own_transfers([late_out, late_in, far_out, far_in], accounts, COMPANIES) == set()


def test_own_transfers_leave_monthly_flow() -> None:
    from pusula.finance.analytics import own_transfers

    accounts = [_account(id=1, name="Mercury ••4259"), _account(id=3, name="Wise USD", provider="wise", credential_key="LLC")]
    out = _tx("llc", date(2026, 9, 10), "-5000", account_id=1)
    inc = _tx("llc", date(2026, 9, 10), "5000", account_id=3, counterparty="REXVEN LLC")
    sale = _tx("llc", date(2026, 9, 10), "700", account_id=3, counterparty="Müşteri")
    ids = own_transfers([out, inc, sale], accounts, COMPANIES)
    tagged = [t.model_copy(update={"category_id": "hesap_arasi"}) if t.id in ids else t for t in (out, inc, sale)]
    [month] = monthly_flows(tagged, _fx(), KINDS)
    assert (month.inflow, month.outflow) == (D("700"), D("0"))
