"""Haftalık bilanço: taslak satırlar, kilitli kurla toplam, mail özeti."""

from datetime import date
from decimal import Decimal

from pusula.db.models import (
    FinAccount,
    FinCompany,
    FinDecision,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
)
from pusula.finance.snapshot import (
    build_week_lines,
    fmt_usd,
    mail_summary,
    previous_snapshot,
    snapshot_totals,
)

D = Decimal


def _account(**kw) -> FinAccount:
    base = dict(id=1, company_id="tr", name="Garanti TL", institution="Garanti", currency="TRY", provider="csv")
    base.update(kw)
    return FinAccount(**base)


def _line(**kw) -> FinSnapshotLine:
    base = dict(snapshot_id=1, side="liability", group_name="Tedarikçiler", label="Kumaşçı", amount=D("100"), currency="TRY")
    base.update(kw)
    return FinSnapshotLine(**base)


def test_week_lines_from_accounts_planned_and_previous_manual_liabilities() -> None:
    accounts = [
        _account(current_balance=D("50000")),
        _account(id=2, name="Bonus kart", type="card", current_balance=D("-7000")),
        _account(id=3, company_id="llc", name="Mercury", currency="USD", current_balance=D("9")),
        _account(id=4, name="Kapalı", current_balance=D("1"), active=False),
    ]
    planned = [
        FinPlannedItem(company_id="tr", direction="in", title="Trendyol hakediş", amount=D("20000"), currency="TRY", due_date=date(2026, 10, 20)),
        FinPlannedItem(company_id="tr", direction="out", title="Kira", amount=D("30000"), currency="TRY", due_date=date(2026, 11, 1), recurrence="monthly"),
        FinPlannedItem(company_id="tr", direction="out", title="SGK", amount=D("15000"), currency="TRY", due_date=date(2026, 10, 26)),
    ]
    previous = [
        _line(label="Kumaşçı", amount=D("12000"), origin="manual", sort=0),
        _line(label="SGK", group_name="Vadeli borçlar", amount=D("14000"), origin="manual", sort=1),
        _line(label="Eski kart", group_name="Kredi kartları / Krediler", origin="account", sort=2),
        _line(side="asset", label="Depozito", origin="manual", sort=3),
    ]
    lines = build_week_lines("tr", accounts, planned, previous)
    assert [(l.side, l.group_name, l.label, l.amount, l.origin) for l in lines] == [
        ("asset", "Banka hesapları", "Garanti TL", D("50000"), "account"),
        ("liability", "Kredi kartları / Krediler", "Bonus kart", D("7000"), "account"),
        ("asset", "Vadeli alacaklar", "Trendyol hakediş", D("20000"), "planned"),
        ("liability", "Vadeli borçlar", "SGK", D("15000"), "planned"),
        ("liability", "Tedarikçiler", "Kumaşçı", D("12000"), "manual"),
    ]
    assert [l.sort for l in lines] == [0, 1, 2, 3, 4]


def test_previous_snapshot_is_latest_earlier_week_of_same_company() -> None:
    snaps = [
        FinSnapshot(id=1, company_id="tr", week_date=date(2026, 9, 28)),
        FinSnapshot(id=2, company_id="tr", week_date=date(2026, 10, 5)),
        FinSnapshot(id=3, company_id="llc", week_date=date(2026, 10, 12)),
        FinSnapshot(id=4, company_id="tr", week_date=date(2026, 10, 12)),
    ]
    prev = previous_snapshot(snaps, "tr", date(2026, 10, 12))
    assert prev is not None and prev.id == 2


def test_totals_use_locked_fx_and_report_missing() -> None:
    lines = [
        _line(side="asset", amount=D("40000"), currency="TRY"),
        _line(side="asset", amount=D("100"), currency="USD"),
        _line(amount=D("10"), currency="EUR"),
        _line(amount=D("10"), currency="GBP"),
    ]
    totals = snapshot_totals(lines, {"TRY": D("0.025"), "EUR": D("1.2")})
    assert totals.assets == D("1100.000")
    assert totals.liabilities == D("12.0")
    assert totals.net == D("1088.000")
    assert totals.missing_fx == {"GBP"}


def test_fmt_usd() -> None:
    assert fmt_usd(D("1234567.4")) == "$1.234.567"
    assert fmt_usd(D("-12400.5")) == "−$12.401"
    assert fmt_usd(D("0")) == "$0"


def test_mail_summary_has_no_greeting_or_signature() -> None:
    companies = [
        FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", sort=1),
        FinCompany(id="tr", name="Rexven TR", country="TR", base_currency="TRY", sort=3),
    ]
    week = date(2026, 10, 12)
    snaps = [
        FinSnapshot(id=2, company_id="tr", week_date=week, fx={"TRY": D("0.025")}, notes="SGK ödendi"),
        FinSnapshot(id=1, company_id="llc", week_date=week, locked=True),
    ]
    lines = {
        1: [FinSnapshotLine(snapshot_id=1, side="asset", group_name="Banka hesapları", label="Mercury", amount=D("10000"), currency="USD")],
        2: [
            FinSnapshotLine(snapshot_id=2, side="asset", group_name="Banka hesapları", label="Garanti", amount=D("40000"), currency="TRY"),
            FinSnapshotLine(snapshot_id=2, side="liability", group_name="Tedarikçiler", label="Kumaşçı", amount=D("8000"), currency="TRY", sort=1),
        ],
    }
    decisions = [
        FinDecision(title="Stripe GmbH'ye taşınsın", owner="Yusuf", status="aksiyon", due_date=date(2026, 10, 30)),
        FinDecision(title="Eski karar", status="tamam"),
    ]
    text = mail_summary(week, companies, snaps, lines, decisions)
    assert text.startswith("12 Eki 2026 haftalık finans özeti\n")
    assert "Aktifler: $11.000\nPasifler: $200\nNet: $10.800" in text
    assert text.index("REXVEN LLC") < text.index("REXVEN TR")
    assert "• P Tedarikçiler: $200" in text
    assert "Not: SGK ödendi" in text
    assert "• Stripe GmbH'ye taşınsın (Yusuf), 30 Eki 2026" in text
    assert "Eski karar" not in text
    # yalnız kesinleşmemiş şirkette not düşer
    assert text.count("Kesinleşmedi.") == 1
    for word in ("Selam", "görüşmek", "!"):
        assert word not in text


def test_mail_summary_without_snapshots() -> None:
    assert mail_summary(date(2026, 10, 12), [], [], {}) == "12 Eki 2026 haftası için bilanço yok."
