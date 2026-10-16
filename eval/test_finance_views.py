"""Finans ekran yanıtları: kapsam, genel bakış, akış filtreleri, şirketler arası, bilanço, kararlar."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from pusula.db.models import (
    FinAccount,
    FinCategory,
    FinCompany,
    FinDecision,
    FinFxRate,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
    FinTransaction,
)
from pusula.finance import views
from pusula.finance.fx import FxTable

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=TZ)
TODAY = NOW.date()
D = Decimal

COMPANIES = [
    FinCompany(id="tr", name="Rexven TR", country="TR", base_currency="TRY", sort=3),
    FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", sort=1),
    FinCompany(id="gmbh", name="Rexven GmbH", country="DE", base_currency="EUR", sort=2),
]
CATEGORIES = [
    FinCategory(id="satis", name="Satış", kind="in", sort=1),
    FinCategory(id="maas", name="Maaş", kind="out", sort=12),
    FinCategory(id="ic_transfer", name="Şirketler arası transfer", kind="transfer", sort=20),
]


def _fx(with_try: bool = True) -> FxTable:
    rows = [FinFxRate(date=date(2026, 1, 1), currency="EUR", usd_rate=D("1.20"))]
    if with_try:
        rows.append(FinFxRate(date=date(2026, 1, 1), currency="TRY", usd_rate=D("0.025")))
    return FxTable(rows)


_ids = iter(range(1, 10_000))


def _tx(company: str, day: date, amount: str, currency: str = "USD", account_id: int = 1, **kw) -> FinTransaction:
    n = next(_ids)
    return FinTransaction(
        id=n, account_id=account_id, company_id=company, booked_at=day, amount=D(amount),
        currency=currency, external_id=f"e{n}", source="api", **kw,
    )


def _bundle(**kw) -> views.Bundle:
    base = dict(
        companies=COMPANIES,
        categories=CATEGORIES,
        accounts=[
            FinAccount(id=1, company_id="llc", name="Mercury", institution="Mercury", currency="USD",
                       provider="mercury", credential_key="LLC", current_balance=D("10000"),
                       last_synced_at=NOW - timedelta(hours=3)),
            FinAccount(id=2, company_id="gmbh", name="Wise EUR", institution="Wise", currency="EUR",
                       provider="wise", credential_key="GMBH", current_balance=D("1000"),
                       sync_warning="Wise: hareketler için SCA gerekli", last_synced_at=NOW),
            FinAccount(id=3, company_id="tr", name="Garanti", institution="Garanti", currency="TRY",
                       provider="csv", current_balance=D("400000"), sync_error="Dosya okunamadı"),
        ],
        fx=_fx(),
    )
    base.update(kw)
    return views.Bundle(**base)


def test_scope_ids() -> None:
    assert views.scope_ids("all", COMPANIES) == ["llc", "gmbh", "tr"]
    assert views.scope_ids("gmbh", COMPANIES) == ["gmbh"]
    with pytest.raises(ValueError):
        views.scope_ids("ltd", COMPANIES)


def test_overview_cards_in_usd_and_local() -> None:
    data = views.overview_view(_bundle(), ["llc", "gmbh", "tr"], date(2026, 10, 1), NOW)
    assert data["cash_usd"] == 21200.0  # 10000 + 1200 + 10000
    cards = {c["id"]: c for c in data["companies"]}
    assert [c["id"] for c in data["companies"]] == ["llc", "gmbh", "tr"]
    assert cards["gmbh"]["cash_local"] == 1000.0 and cards["gmbh"]["currency"] == "EUR"
    assert cards["tr"]["cash_local"] == 400000.0
    assert data["alerts"]["sync_errors"] == [{"id": 3, "name": "Garanti", "error": "Dosya okunamadı"}]
    assert data["alerts"]["warnings"][0]["id"] == 2
    assert data["missing_fx"] == []
    assert len(data["projection"]) == 13


def test_overview_reports_missing_fx_instead_of_guessing() -> None:
    data = views.overview_view(_bundle(fx=_fx(with_try=False)), ["tr"], date(2026, 10, 1), NOW)
    assert data["cash_usd"] == 0.0
    assert data["companies"][0]["cash_local"] is None
    assert data["missing_fx"] == ["TRY"]


def test_overview_scope_single_company() -> None:
    data = views.overview_view(_bundle(), ["gmbh"], date(2026, 10, 1), NOW)
    assert data["cash_usd"] == 1200.0
    assert [c["id"] for c in data["companies"]] == ["gmbh"]
    assert data["alerts"]["sync_errors"] == []


def test_flows_filters_and_pagination() -> None:
    txs = [
        _tx("llc", date(2026, 9, 2), "500", category_id="satis", description="Amazon payout"),
        _tx("llc", date(2026, 9, 3), "-200", category_id="maas"),
        _tx("llc", date(2026, 9, 4), "-50", description="Kargo İADE"),
        _tx("llc", date(2026, 9, 5), "-1000", counterparty_company_id="tr", category_id="ic_transfer"),
        _tx("tr", date(2026, 9, 6), "40000", "TRY", account_id=3, counterparty_company_id="llc"),
        _tx("llc", date(2026, 7, 1), "999"),  # aralık dışı
    ]
    bundle = _bundle(transactions=txs)
    scope = ["llc", "gmbh", "tr"]
    data = views.flows_view(bundle, scope, date(2026, 9, 1), date(2026, 9, 30))
    assert data["totals"] == {"in": 500.0, "out": 250.0, "net": 250.0}
    assert data["total"] == 5
    assert [t["booked_at"] for t in data["transactions"]][:2] == [date(2026, 9, 6), date(2026, 9, 5)]
    assert data["transactions"][0]["account"] == "Garanti" and data["transactions"][0]["amount_usd"] == 1000.0

    def ids(**kw) -> list[str]:
        out = views.flows_view(bundle, scope, date(2026, 9, 1), date(2026, 9, 30), **kw)
        return [t["description"] or t["category_id"] or "" for t in out["transactions"]]

    assert len(views.flows_view(bundle, scope, date(2026, 9, 1), date(2026, 9, 30), kind="transfer")["transactions"]) == 2
    assert ids(kind="uncategorized") == ["Kargo İADE"]
    assert ids(kind="in") == ["Amazon payout"]
    assert ids(query="kargo iade") == ["Kargo İADE"]
    assert ids(category="_none", kind="out") == ["Kargo İADE"]
    page = views.flows_view(bundle, scope, date(2026, 9, 1), date(2026, 9, 30), limit=2, offset=2)
    assert page["total"] == 5 and len(page["transactions"]) == 2


def test_projection_view_floor_and_items() -> None:
    planned = [
        FinPlannedItem(id=1, company_id="llc", direction="out", title="Vergi", amount=D("15000"), currency="USD",
                       due_date=date(2026, 10, 28)),
        FinPlannedItem(id=2, company_id="llc", direction="out", title="Eski", amount=D("100"), currency="USD",
                       due_date=date(2026, 9, 1)),
        FinPlannedItem(id=3, company_id="gmbh", direction="in", title="Alacak", amount=D("1000"), currency="EUR",
                       due_date=date(2026, 10, 20), probability=D("0.5")),
    ]
    data = views.projection_view(_bundle(planned=planned), ["llc"], TODAY, floor=D("5000"))
    assert data["start_cash_usd"] == 10000.0
    assert data["first_below"] == {"week": date(2026, 10, 26), "close": -5100.0}
    assert [i["title"] for i in data["items"]] == ["Eski", "Vergi"]
    assert data["items"][0]["overdue"] is True
    full = views.projection_view(_bundle(planned=planned), ["gmbh"], TODAY, weighted=False)
    assert full["weeks"][1]["in"] == 1200.0


def test_intercompany_view_scope() -> None:
    txs = [
        _tx("llc", date(2026, 9, 1), "-1000", counterparty_company_id="tr"),
        _tx("tr", date(2026, 9, 2), "40000", "TRY", account_id=3, counterparty_company_id="llc"),
        _tx("gmbh", date(2026, 9, 3), "-100", "EUR", account_id=2, counterparty_company_id="llc"),
    ]
    bundle = _bundle(transactions=txs)
    all_ = views.intercompany_view(bundle, ["llc", "gmbh", "tr"], date(2026, 9, 1), date(2026, 9, 30))
    assert {(m["from"], m["to"]): m["usd"] for m in all_["matrix"]} == {("gmbh", "llc"): 120.0, ("llc", "tr"): 1000.0}
    assert [t["company_id"] for t in all_["unmatched_outgoing"]] == ["gmbh"]
    gmbh = views.intercompany_view(bundle, ["gmbh"], date(2026, 9, 1), date(2026, 9, 30))
    assert [(p["a"], p["b"]) for p in gmbh["pairs"]] == [("llc", "gmbh"), ("gmbh", "tr")]
    assert {(m["from"], m["to"]) for m in gmbh["matrix"]} == {("gmbh", "llc")}
    assert len(gmbh["transactions"]) == 1


def test_intercompany_match_across_range_edge() -> None:
    # Gönderim aralığın içinde, karşılığı aralığın 2 gün dışında: eşleşmiş sayılır
    txs = [
        _tx("llc", date(2026, 9, 29), "-1000", counterparty_company_id="tr"),
        _tx("tr", date(2026, 10, 2), "40000", "TRY", account_id=3, counterparty_company_id="llc"),
    ]
    data = views.intercompany_view(_bundle(transactions=txs), ["llc", "gmbh", "tr"], date(2026, 9, 1), date(2026, 9, 30))
    assert data["unmatched_outgoing"] == [] and data["unmatched_incoming"] == []
    assert len(data["transactions"]) == 1  # aralık dışındaki karşılık listelenmez


def test_snapshots_view_structure_previous_and_consolidation() -> None:
    snaps = [
        FinSnapshot(id=1, company_id="tr", week_date=date(2026, 10, 5), fx={"TRY": D("0.025")}),
        FinSnapshot(id=2, company_id="tr", week_date=date(2026, 10, 12), fx={"TRY": D("0.025")}, locked=True),
        FinSnapshot(id=3, company_id="llc", week_date=date(2026, 10, 12)),
    ]

    def line(sid: int, section: str, group: str, label: str, amount: str, currency: str = "TRY") -> FinSnapshotLine:
        side = "asset" if "assets" in section else "liability"
        return FinSnapshotLine(snapshot_id=sid, side=side, section=section, group_name=group, label=label,
                               amount=D(amount), currency=currency)

    lines = [
        line(1, "current_assets", "Hazır değerler", "Garanti", "20000"),
        line(2, "current_assets", "Hazır değerler", "Garanti", "40000"),
        line(2, "current_liabilities", "Ticari borçlar", "X", "4000"),
        line(2, "current_liabilities", "Şirketler arası borçlar", "Rexven LLC", "300", "USD"),
        line(3, "current_assets", "Hazır değerler", "Mercury", "500", "USD"),
        line(3, "current_assets", "Şirketler arası alacaklar", "Rexven TR", "300", "USD"),
        # eski satır: bölüm boş → kısa vadeli
        FinSnapshotLine(snapshot_id=3, side="liability", group_name="Tedarikçiler", label="Ajans", amount=D("50"), currency="USD"),
    ]
    data = views.snapshots_view(snaps, lines, ["llc", "gmbh", "tr"], None)
    assert data["weeks"] == [date(2026, 10, 12), date(2026, 10, 5)]
    assert [s["company_id"] for s in data["snapshots"]] == ["llc", "tr"]
    llc, tr = data["snapshots"]
    assert llc["sheet"]["assets_usd"] == 800.0 and llc["sheet"]["debts_usd"] == 50.0
    assert llc["lines"][2]["section"] == "current_liabilities"
    assert tr["previous"] == {"week": date(2026, 10, 5), "assets_usd": 500.0, "debts_usd": 0.0, "equity_usd": 500.0}
    assert llc["previous"] is None
    cons = data["consolidated"]
    assert cons["assets_usd"] == 1500.0 and cons["debts_usd"] == 150.0  # 300 alacak / borç elimine
    assert cons["eliminated_receivables_usd"] == 300.0 and cons["elimination_gap_usd"] == 0.0
    assert data["totals"] == {"assets_usd": 1500.0, "debts_usd": 150.0, "equity_usd": 1350.0}
    assert data["previous_consolidated"]["week"] == date(2026, 10, 5)
    assert [s["key"] for s in cons["sections"]] == ["current_assets", "noncurrent_assets", "current_liabilities", "longterm_liabilities", "equity"]
    assert cons["ratios"]["current"] == 10.0
    assert [c["title"] for c in data["catalog"]][-1] == "Özkaynaklar"

    only_tr = views.snapshots_view(snaps, lines, ["tr"], date(2026, 10, 5))
    assert only_tr["week"] == date(2026, 10, 5) and only_tr["consolidated"] is None
    assert only_tr["snapshots"][0]["sheet"]["assets_usd"] == 500.0


def test_decisions_view_order_and_stats() -> None:
    rows = [
        FinDecision(id=1, title="Biten", status="tamam", priority="yuksek"),
        FinDecision(id=2, title="Düşük", status="acik", priority="dusuk", impact_usd=D("1000")),
        FinDecision(id=3, title="Geciken", status="aksiyon", priority="yuksek", due_date=date(2026, 10, 1),
                    impact_usd=D("-500")),
        FinDecision(id=4, title="Başka şirket", company_id="tr", status="acik"),
        FinDecision(id=5, title="GmbH", company_id="gmbh", status="devam", priority="orta"),
    ]
    data = views.decisions_view(rows, ["gmbh"], TODAY)
    assert [d["title"] for d in data["decisions"]] == ["Geciken", "GmbH", "Düşük", "Biten"]
    assert data["decisions"][0]["overdue"] is True
    assert data["stats"] == {"open": 3, "action": 1, "overdue": 1, "impact_usd": 500.0}
