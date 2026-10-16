"""Haftalık bilanço: bölüm yapısı, taslak satırlar, denklik, oranlar, eliminasyon, mail özeti."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pusula.db.models import (
    FinAccount,
    FinCompany,
    FinDecision,
    FinFxRate,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
    FinTransaction,
)
from pusula.finance.fx import FxTable
from pusula.finance.snapshot import (
    SECTIONS,
    balance_sheet,
    build_week_lines,
    fmt_usd,
    intercompany_balances,
    mail_summary,
    previous_snapshot,
    section_of,
    snapshot_sheet,
)

D = Decimal
WEEK = date(2026, 10, 12)  # pazartesi; bilanço tarihi 18 Eki
TRY = {"TRY": D("0.025")}


def _account(**kw) -> FinAccount:
    base = dict(id=1, company_id="tr", name="Garanti TL", institution="Garanti", currency="TRY", provider="csv")
    base.update(kw)
    return FinAccount(**base)


def _planned(title: str, direction: str, amount: str, due: date, **kw) -> FinPlannedItem:
    return FinPlannedItem(company_id="tr", direction=direction, title=title, amount=D(amount), currency="TRY", due_date=due, **kw)


def _line(section: str, group: str, label: str, amount: str, currency: str = "TRY", **kw) -> FinSnapshotLine:
    side = "asset" if section in ("current_assets", "noncurrent_assets") else "liability"
    return FinSnapshotLine(snapshot_id=kw.pop("snapshot_id", 1), side=side, section=section, group_name=group,
                           label=label, amount=D(amount), currency=currency, **kw)


def test_catalog_follows_uniform_chart_headings() -> None:
    assert [s.title for s in SECTIONS] == [
        "Dönen varlıklar",
        "Duran varlıklar",
        "Kısa vadeli yabancı kaynaklar",
        "Uzun vadeli yabancı kaynaklar",
        "Özkaynaklar",
    ]
    assert [s.side for s in SECTIONS] == ["asset", "asset", "liability", "liability", "liability"]


def test_week_lines_place_accounts_and_planned_in_sections() -> None:
    accounts = [
        _account(current_balance=D("50000")),
        _account(id=2, name="Bonus kart", type="card", current_balance=D("-7000")),
        _account(id=3, name="Kargo bakiyesi", type="shipping", current_balance=D("1200")),
        _account(id=4, company_id="llc", name="Mercury", currency="USD", current_balance=D("9")),
        _account(id=5, name="Kapalı", current_balance=D("1"), active=False),
    ]
    planned = [
        _planned("Trendyol hakediş", "in", "20000", date(2026, 10, 20)),
        _planned("Uzun vadeli alacak", "in", "5000", date(2028, 1, 1)),
        _planned("SGK", "out", "15000", date(2026, 10, 26), category_id="maas"),
        _planned("Tedarikçi", "out", "9000", date(2026, 11, 2), category_id="tedarikci"),
        _planned("Makine taksiti", "out", "80000", date(2027, 12, 1)),
        _planned("Kira", "out", "30000", date(2026, 11, 1), recurrence="monthly"),  # gelecek gider, borç değil
        _planned("Ödendi", "out", "1", date(2026, 10, 1), status="done"),
    ]
    lines = build_week_lines("tr", accounts, planned, week=WEEK)
    assert [(l.section, l.group_name, l.label, l.amount, l.origin) for l in lines] == [
        ("current_assets", "Hazır değerler", "Garanti TL", D("50000"), "account"),
        ("current_liabilities", "Mali borçlar", "Bonus kart", D("7000"), "account"),
        ("current_assets", "Verilen avanslar ve peşin giderler", "Kargo bakiyesi", D("1200"), "account"),
        ("current_assets", "Ticari alacaklar", "Trendyol hakediş", D("20000"), "planned"),
        ("noncurrent_assets", "Uzun vadeli alacaklar", "Uzun vadeli alacak", D("5000"), "planned"),
        ("current_liabilities", "Ödenecek vergi, SGK ve maaş", "SGK", D("15000"), "planned"),
        ("current_liabilities", "Ticari borçlar", "Tedarikçi", D("9000"), "planned"),
        ("longterm_liabilities", "Uzun vadeli ticari borçlar", "Makine taksiti", D("80000"), "planned"),
    ]
    assert all(l.side == ("asset" if "assets" in l.section else "liability") for l in lines)
    assert [l.sort for l in lines] == list(range(len(lines)))


def test_previous_manual_lines_copied_on_both_sides() -> None:
    previous = [
        _line("current_assets", "Stoklar", "Ambalaj stoğu", "300000", origin="manual", sort=0),
        _line("noncurrent_assets", "Maddi duran varlıklar", "Demirbaş", "150000", origin="manual", sort=1),
        _line("equity", "Ödenmiş sermaye", "Sermaye", "100000", origin="manual", sort=2),
        _line("current_liabilities", "Ticari borçlar", "Tedarikçi", "1", origin="manual", sort=3),  # otomatikle çakışır
        _line("current_liabilities", "Mali borçlar", "Eski kart", "5", origin="account", sort=4),
        _line("current_assets", "Şirketler arası alacaklar", "Rexven LLC", "5", currency="USD", origin="intercompany", sort=5),
    ]
    planned = [_planned("Tedarikçi", "out", "9000", date(2026, 11, 2))]
    lines = build_week_lines("tr", [], planned, previous, week=WEEK)
    assert [(l.section, l.label, l.origin) for l in lines] == [
        ("current_liabilities", "Tedarikçi", "planned"),
        ("current_assets", "Ambalaj stoğu", "manual"),
        ("noncurrent_assets", "Demirbaş", "manual"),
        ("equity", "Sermaye", "manual"),
    ]
    assert lines[3].side == "liability"


def test_intercompany_balances_from_own_records() -> None:
    fx = FxTable([FinFxRate(date=date(2026, 1, 1), currency="TRY", usd_rate=D("0.025"))])

    def tx(company: str, day: date, amount: str, currency: str, cp: str) -> FinTransaction:
        return FinTransaction(account_id=1, company_id=company, booked_at=day, amount=D(amount), currency=currency,
                              external_id=f"{company}{day}{amount}", source="api", counterparty_company_id=cp)

    txs = [
        tx("llc", date(2026, 9, 1), "-25000", "USD", "tr"),  # LLC → TR 25.000 gönderdi
        tx("llc", date(2026, 9, 20), "4000", "USD", "tr"),  # TR'den 4.000 döndü
        tx("llc", date(2026, 10, 1), "3000", "USD", "gmbh"),  # GmbH'den geldi → borç
        tx("llc", date(2026, 10, 30), "-999", "USD", "tr"),  # bilanço tarihinden sonra
        tx("tr", date(2026, 9, 2), "1000000", "TRY", "llc"),  # başka şirketin kaydı
    ]
    names = {"tr": "Rexven TR", "gmbh": "Rexven GmbH"}
    result = intercompany_balances("llc", txs, fx, date(2026, 10, 18), names)
    assert result == [("gmbh", "Rexven GmbH", D("-3000.00")), ("tr", "Rexven TR", D("21000.00"))]
    lines = build_week_lines("llc", [], [], week=WEEK, intercompany=result)
    assert [(l.section, l.group_name, l.label, l.amount, l.currency, l.origin) for l in lines] == [
        ("current_liabilities", "Şirketler arası borçlar", "Rexven GmbH", D("3000.00"), "USD", "intercompany"),
        ("current_assets", "Şirketler arası alacaklar", "Rexven TR", D("21000.00"), "USD", "intercompany"),
    ]


def test_balance_sheet_computes_equity_when_not_entered() -> None:
    lines = [
        _line("current_assets", "Hazır değerler", "Banka", "400000"),  # 10.000
        _line("current_assets", "Stoklar", "Stok", "200000"),  # 5.000
        _line("noncurrent_assets", "Maddi duran varlıklar", "Demirbaş", "5000", currency="USD"),
        _line("current_liabilities", "Ticari borçlar", "Tedarikçi", "240000"),  # 6.000
        _line("longterm_liabilities", "Uzun vadeli mali borçlar", "Kredi", "4000", currency="USD"),
    ]
    sheet = snapshot_sheet(lines, TRY)
    assert sheet.sections["current_assets"].usd == D("15000.000")
    assert sheet.assets == D("20000.000")
    assert sheet.debts == D("10000.000")
    assert not sheet.has_equity and sheet.equity == D("10000.000") and sheet.difference == 0
    assert sheet.liabilities_and_equity == sheet.assets and sheet.balanced
    r = sheet.ratios
    q = D("0.0001")
    assert r.current == D("2.5")
    assert r.acid_test.quantize(q) == D("1.6667") and r.cash.quantize(q) == D("1.6667")
    assert r.working_capital == D("9000.000") and r.debt_to_equity == D("1") and r.equity_ratio == D("0.5")
    # katalogdaki grup sırası korunur
    assert [g.name for g in sheet.sections["current_assets"].groups][:3] == ["Hazır değerler", "Menkul kıymetler", "Ticari alacaklar"]


def test_entered_equity_shows_balance_difference() -> None:
    lines = [
        _line("current_assets", "Hazır değerler", "Banka", "1000", currency="USD"),
        _line("current_liabilities", "Ticari borçlar", "Tedarikçi", "300", currency="USD"),
        _line("equity", "Ödenmiş sermaye", "Sermaye", "500", currency="USD"),
    ]
    sheet = snapshot_sheet(lines, {})
    assert sheet.has_equity and sheet.equity == D("500")
    assert sheet.difference == D("200") and not sheet.balanced
    balanced = snapshot_sheet([*lines, _line("equity", "Dönem net kâr / zararı", "Dönem kârı", "200", currency="USD")], {})
    assert balanced.difference == 0 and balanced.balanced


def test_ratios_without_short_term_debt_are_none_and_missing_fx_reported() -> None:
    sheet = snapshot_sheet([_line("current_assets", "Hazır değerler", "Wise", "100", currency="GBP")], TRY)
    assert sheet.assets == 0 and sheet.missing_fx == {"GBP"}
    assert sheet.ratios.current is None and sheet.ratios.cash is None and sheet.ratios.equity_ratio is None


def test_consolidation_eliminates_intercompany_and_reports_gap() -> None:
    llc = [
        _line("current_assets", "Hazır değerler", "Mercury", "10000", currency="USD"),
        _line("current_assets", "Şirketler arası alacaklar", "Rexven TR", "21000", currency="USD"),
    ]
    tr = [
        _line("current_assets", "Hazır değerler", "Garanti", "400000", snapshot_id=2),
        _line("current_liabilities", "Şirketler arası borçlar", "Rexven LLC", "20500", currency="USD", snapshot_id=2),
    ]
    items = [(l, {}) for l in llc] + [(l, TRY) for l in tr]
    separate = balance_sheet(items)
    assert separate.assets == D("41000.000") and separate.debts == D("20500")
    consolidated = balance_sheet(items, eliminate_intercompany=True)
    assert consolidated.assets == D("20000.000") and consolidated.debts == 0
    assert consolidated.eliminated_receivables == D("21000") and consolidated.eliminated_payables == D("20500")
    assert consolidated.elimination_gap == D("500")
    # özkaynak şirket başına: LLC 31.000 + TR (10.000 − 20.500); tutmayan 500 denklik farkı olur
    assert consolidated.computed_equity == D("20500.000") and consolidated.difference == D("-500.000")
    assert not consolidated.balanced and separate.balanced


def test_consolidated_equity_mixes_entered_and_computed() -> None:
    llc = [
        _line("current_assets", "Hazır değerler", "Mercury", "1000", currency="USD", snapshot_id=1),
        _line("equity", "Ödenmiş sermaye", "Sermaye", "1000", currency="USD", snapshot_id=1),
    ]
    tr = [_line("current_assets", "Hazır değerler", "Garanti", "40000", snapshot_id=2)]  # özkaynak girilmemiş
    sheet = balance_sheet([(l, {}) for l in llc] + [(l, TRY) for l in tr], eliminate_intercompany=True)
    assert sheet.equity_entered == D("1000") and sheet.computed_equity == D("1000.000")
    assert sheet.liabilities_and_equity == sheet.assets and sheet.balanced


def test_line_section_must_match_side() -> None:
    with pytest.raises(ValidationError):
        FinSnapshotLine(snapshot_id=1, side="asset", section="equity", group_name="x", label="y", amount=D("1"), currency="USD")
    old = FinSnapshotLine(snapshot_id=1, side="liability", group_name="x", label="y", amount=D("1"), currency="USD")
    assert section_of(old) == "current_liabilities"


def test_previous_snapshot_is_latest_earlier_week_of_same_company() -> None:
    snaps = [
        FinSnapshot(id=1, company_id="tr", week_date=date(2026, 9, 28)),
        FinSnapshot(id=2, company_id="tr", week_date=date(2026, 10, 5)),
        FinSnapshot(id=3, company_id="llc", week_date=date(2026, 10, 12)),
        FinSnapshot(id=4, company_id="tr", week_date=date(2026, 10, 12)),
    ]
    prev = previous_snapshot(snaps, "tr", date(2026, 10, 12))
    assert prev is not None and prev.id == 2


def test_fmt_usd() -> None:
    assert fmt_usd(D("1234567.4")) == "$1.234.567"
    assert fmt_usd(D("-12400.5")) == "−$12.401"
    assert fmt_usd(D("0")) == "$0"


def test_mail_summary_has_structure_ratios_and_no_greeting() -> None:
    companies = [
        FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", sort=1),
        FinCompany(id="tr", name="Rexven TR", country="TR", base_currency="TRY", sort=3),
    ]
    snaps = [
        FinSnapshot(id=2, company_id="tr", week_date=WEEK, fx=TRY, notes="SGK ödendi"),
        FinSnapshot(id=1, company_id="llc", week_date=WEEK, locked=True),
    ]
    lines = {
        1: [
            _line("current_assets", "Hazır değerler", "Mercury", "10000", currency="USD", snapshot_id=1),
            _line("current_assets", "Şirketler arası alacaklar", "Rexven TR", "1000", currency="USD", snapshot_id=1),
        ],
        2: [
            _line("current_assets", "Hazır değerler", "Garanti", "40000", snapshot_id=2),
            _line("current_liabilities", "Ticari borçlar", "Kumaşçı", "8000", snapshot_id=2),
            _line("current_liabilities", "Şirketler arası borçlar", "Rexven LLC", "1000", currency="USD", snapshot_id=2),
        ],
    }
    decisions = [
        FinDecision(title="Stripe GmbH'ye taşınsın", owner="Yusuf", status="aksiyon", due_date=date(2026, 10, 30)),
        FinDecision(title="Eski karar", status="tamam"),
    ]
    text = mail_summary(WEEK, companies, snaps, lines, decisions)
    assert text.startswith("12 Eki 2026 haftalık finans özeti\n\nKONSOLİDE\n")
    # konsolide: şirketler arası 1.000 elimine
    assert "Aktif $11.000 · Yabancı kaynak $200 · Özkaynak $10.800" in text
    assert "Şirketler arası elimine: alacak $1.000, borç $1.000" in text
    assert "Cari oran 55,00" in text
    assert text.index("REXVEN LLC") < text.index("REXVEN TR")
    assert "   – Ticari borçlar: $200" in text
    assert "Not: SGK ödendi" in text and text.count("Kesinleşmedi.") == 1
    assert "• Stripe GmbH'ye taşınsın (Yusuf), 30 Eki 2026" in text and "Eski karar" not in text
    for word in ("Selam", "görüşmek", "!"):
        assert word not in text


def test_mail_summary_without_snapshots() -> None:
    assert mail_summary(WEEK, [], [], {}) == "12 Eki 2026 haftası için bilanço yok."
