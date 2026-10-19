"""LLC cüzdan ve vergi: REX referansı, yükleme sınıflaması, matrah, senaryo, bilanço satırları."""

from contextlib import nullcontext
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pusula.db.models import FinAccount, FinCompany, FinTaxSettings, FinTransaction, FinWallet, TaxPartner
from pusula.finance import tax
from pusula.finance.analytics import own_transfers
from pusula.finance.fx import FxTable
from pusula.finance.snapshot import build_week_lines
from pusula.panel_auth import AuthUser
from pusula.web import finance as web_finance
from pusula.web.app import app
from pusula.web.auth import encode_session

D = Decimal
FX = FxTable([])
TODAY = date(2026, 10, 18)


def tx(id_, amount, description="", account_id=1, category_id=None, rule=None, edited=None, company="llc", day=TODAY,
       counterparty=None, cp_company=None):
    return FinTransaction(
        id=id_, account_id=account_id, company_id=company, booked_at=day, amount=D(amount), currency="USD",
        description=description, counterparty=counterparty, category_id=category_id, category_rule_id=rule,
        user_edited_at=edited, counterparty_company_id=cp_company, external_id=str(id_), source="api",
    )


def account(id_, name, balance, institution="Mercury", type_="fintech", company="llc"):
    return FinAccount(
        id=id_, company_id=company, name=name, institution=institution, type=type_, currency="USD",
        provider="manual", current_balance=D(balance),
    )


def wallet(kind, name, balance, company="llc", active=True):
    return FinWallet(company_id=company, kind=kind, name=name, balance=D(balance), as_of=TODAY, active=active)


@pytest.mark.parametrize(
    "text,ref",
    [
        ("Received money from Betul Coban with reference REX-48040", "REX-48040"),
        ("SUPHIYE EREN GUNES; REX-158115; REXVEN LLC", "REX-158115"),
        ("Rex-155265", "REX-155265"),
        ("BRIDGE ARX; REX 198445; Rexven LLC", "REX-198445"),
        ("Received money from Nalan Gezen with reference REX64976", "REX-64976"),
        ("REX-0923", "REX-923"),
        ("ALVEAY GLOBAL LL; MERCURYACH; Rexven LLC", None),
        ("CAREX-12 payment", None),
        ("", None),
    ],
)
def test_wallet_ref(text, ref) -> None:
    assert tax.wallet_ref(text) == ref


def test_wallet_deposit_candidates() -> None:
    assert tax.is_wallet_deposit(tx(1, "100", "REX-1"))
    assert tax.is_wallet_deposit(tx(2, "100", "REX-1", category_id="satis", rule=5))  # kuralla verilmiş
    assert not tax.is_wallet_deposit(tx(3, "-100", "REX-1"))  # çıkış
    assert not tax.is_wallet_deposit(tx(4, "100", "REX-1", category_id="satis"))  # elle verilmiş
    assert not tax.is_wallet_deposit(tx(5, "100", "REX-1", edited=datetime(2026, 10, 1)))
    assert not tax.is_wallet_deposit(tx(6, "100", "REX-1", company="tr"))  # yalnız LLC
    assert not tax.is_wallet_deposit(tx(7, "100", "Rexven LLC"))
    assert not tax.is_wallet_deposit(tx(8, "100", "REX-1", category_id="cuzdan", rule=None))


def test_rex_deposit_is_never_own_transfer() -> None:
    """Mercury → Wise aktarımı ile aynı tutarlı müşteri yüklemesi eşleşmemeli."""
    accounts = [account(2, "Mercury ••4259", 0), account(9, "Wise USD", 0, institution="Wise")]
    companies = [FinCompany(id="llc", name="Rexven LLC", country="US", base_currency="USD", match_patterns=["%rexven llc%"])]
    out = tx(1, "-1000", "Send to Rexven LLC", account_id=2)
    deposit = tx(2, "1000", "Received money from SOVEREIGN SPARKLE with reference REX-139777", account_id=9)
    real = tx(3, "1000", "Received money from Rexven LLC", account_id=9, day=date(2026, 10, 19))
    assert own_transfers([out, deposit, real], accounts, companies) == {1, 3}


def test_deposits_by_channel_and_customer() -> None:
    accounts = [account(2, "Mercury ••4259", 0), account(11, "Stripe", 0, institution="Stripe", type_="payment")]
    rows = [
        tx(1, "100", "REX-5", account_id=2, category_id="cuzdan", counterparty="Ali", day=date(2026, 10, 1)),
        tx(2, "250", "From X via mercury.com REX-5", account_id=2, category_id="cuzdan", counterparty="Ali Veli"),
        tx(3, "300", "charge", account_id=11, category_id="cuzdan", counterparty="Stripe"),
        tx(4, "-20", "REFUND FOR CHARGE", account_id=11, category_id="cuzdan"),
        tx(5, "999", "REX-9", account_id=2, category_id="satis"),
    ]
    dep = tax.deposits(rows, accounts, FX)
    assert (dep.usd, dep.count, dep.refunds_usd, dep.without_ref) == (D("650"), 3, D("20"), 1)
    assert [(c.name, c.usd) for c in dep.channels] == [("Mercury ••4259", D("350")), ("Stripe", D("300"))]
    [ali] = dep.customers
    assert (ali.ref, ali.name, ali.usd, ali.count, ali.last) == ("REX-5", "Ali Veli", D("350"), 2, TODAY)


def settings(**kw):
    base = dict(
        company_id="llc",
        year_start_net_assets=D("100000"),
        partners=[TaxPartner(name="Ana", share=D("0.805")), TaxPartner(name="Ali", share=D("0.06")),
                  TaxPartner(name="Veli", share=D("0.06")), TaxPartner(name="Can", share=D("0.03")),
                  TaxPartner(name="Cem", share=D("0.03")), TaxPartner(name="Efe", share=D("0.015"))],
    )
    return FinTaxSettings(**{**base, **kw})


def test_net_assets_and_payments() -> None:
    accounts = [account(2, "Mercury", "700000"), account(3, "Card", "-10000", type_="card"), account(4, "Off", "5", company="tr")]
    wallets = [
        wallet("seller", "Toplam", "300000"),
        wallet("producer", "ASPOL", "120000"),
        wallet("producer", "Atolye", "-20000"),  # avans: borcu azaltır
        wallet("producer", "Eski", "50000", active=False),
    ]
    assets = tax.net_assets("llc", accounts, wallets, FX)
    assert (assets.cash_usd, assets.seller_usd, assets.producer_usd, assets.net) == (
        D("690000"), D("300000"), D("100000"), D("290000"))
    rows = [
        tx(1, "-5000", category_id="ortak", day=date(2026, 3, 31)),
        tx(2, "1000", category_id="ortak", day=date(2026, 4, 2)),  # hatalı gönderim iadesi netleşir
        tx(3, "-9000", category_id="ortak", day=date(2025, 12, 30)),  # geçen yıl
    ]
    assert tax.outflow_total(rows, "llc", "ortak", date(2026, 1, 1), FX) == D("4000")


def test_profit_needs_year_start_and_annualizes() -> None:
    assert tax.profit(D("500000"), FinTaxSettings(company_id="llc"), TODAY) is None
    prof = tax.profit(D("500000"), settings(distributions=D("20000")), date(2026, 7, 2))  # 183. gün
    assert prof.ytd == D("420000")
    assert prof.elapsed_days == 183
    assert prof.projected.quantize(D("1")) == D("837705")


def test_individual_tax_2026_brackets() -> None:
    assert tax.individual_tax(D("0")) == 0
    assert tax.individual_tax(D("12400")) == D("1240")
    assert tax.individual_tax(D("36000")) == D("4072")
    assert tax.individual_tax(D("483000")) == D("137819.25")
    assert tax.individual_tax(D("700000")) == tax.individual_tax(D("640600")) + D("59400") * D("0.37")


def test_five_scenarios() -> None:
    by = {s.key: s for s in tax.scenarios(D("600000"), settings(dividend_ratio=D("0.5")), D("0.6"))}
    assert by["none"].tax == 0
    part = by["partnership"]
    assert [p.name for p in part.partners] == ["Ana", "Ali", "Veli", "Can", "Cem", "Efe"]
    assert part.partners[0].income == D("483000.00") and part.partners[0].tax == D("137819.25")
    assert part.tax == sum(p.tax for p in part.partners)
    assert by["ccorp"].tax == D("126000.00")
    # %60 yurtdışı: 360k × %14 + 240k × %21
    assert by["ccorp_fdii"].tax == D("100800.00")
    # vergi sonrası 499.200'ün yarısı dağıtılır, %20 kesinti
    assert by["ccorp_dividend"].tax == D("100800.00") + D("49920.00")
    assert all(s.tax == 0 for s in tax.scenarios(D("-5000"), settings(), D("0")))


def test_unassigned_share_is_shown() -> None:
    rows = tax.partner_rows(settings(partners=[TaxPartner(name="Ana", share=D("0.8"))]))
    assert rows == [("Ana", D("0.8")), ("Atanmamış pay", D("0.2"))]
    with pytest.raises(ValueError):
        settings(partners=[TaxPartner(name="A", share=D("0.7")), TaxPartner(name="B", share=D("0.4"))])


def test_structure_tax_follows_current_structure() -> None:
    st = settings()
    assert tax.structure_tax(D("600000"), st, D("0")) == next(
        s.tax for s in tax.scenarios(D("600000"), st, D("0")) if s.key == "partnership")
    assert tax.structure_tax(D("600000"), settings(structure="ccorp"), D("0")) == D("126000.00")
    assert tax.structure_tax(D("600000"), settings(structure="none"), D("0")) == 0


def test_foreign_share_estimate_excludes_us_entities() -> None:
    dep = tax.Deposits(customers=[
        tax.Customer("REX-1", "Betul Coban", D("300")),
        tax.Customer("REX-2", "WEVRA LLC", D("100")),
        tax.Customer("REX-3", "Lumesh Inc.", D("100")),
        tax.Customer("REX-4", "Real Famous Ltd", D("500")),
    ])
    assert tax.foreign_share_estimate(dep) == D("0.8")
    assert tax.foreign_share_estimate(tax.Deposits()) is None


def test_week_lines_include_wallets_and_tax() -> None:
    lines = build_week_lines(
        "llc",
        [account(2, "Mercury", "1000")],
        [],
        week=date(2026, 10, 12),
        wallets=[wallet("seller", "Toplam", "300"), wallet("producer", "ASPOL", "200"), wallet("producer", "Atolye", "-50"),
                 wallet("producer", "Sıfır", "0"), wallet("seller", "TR", "9", company="tr")],
        tax_due=[("Tahmini ABD vergisi (Federal %21)", D("80")), ("Sıfır vergi", D("0"))],
    )
    got = [(l.section, l.group_name, l.label, l.amount, l.origin) for l in lines]
    assert got == [
        ("current_assets", "Hazır değerler", "Mercury", D("1000"), "account"),
        ("current_liabilities", "Alınan avanslar", "Satıcı cüzdanı · Toplam", D("300"), "wallet"),
        ("current_liabilities", "Ticari borçlar", "Üretici · ASPOL", D("200"), "wallet"),
        ("current_assets", "Verilen avanslar ve peşin giderler", "Üretici · Atolye", D("50"), "wallet"),
        ("current_liabilities", "Ödenecek vergi, SGK ve maaş", "Tahmini ABD vergisi (Federal %21)", D("80"), "tax"),
    ]


# --- API -------------------------------------------------------------------------

SECRET = "test-session-secret-that-is-longer-than-32-characters"
ADMIN = AuthUser(email="yusuf.aras@rexven.com", role="admin", rep_id=None, full_name=None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PUSULA_SESSION_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL_POOLED", "postgres://test")
    monkeypatch.delenv("PUSULA_FINANCE_EMAILS", raising=False)
    monkeypatch.setattr(web_finance, "connect", lambda: nullcontext(object()))
    inserts: list = []
    updates: list = []
    monkeypatch.setattr(web_finance.store, "insert_row", lambda conn, table, values: inserts.append((table, values)) or 5)
    monkeypatch.setattr(web_finance.store, "update_row", lambda conn, table, i, values: updates.append((table, values)) or True)
    existing = wallet("producer", "ASPOL", "10").model_dump()
    monkeypatch.setattr(web_finance.store, "load_row", lambda conn, table, i: existing if table == "fin_wallets" else None)
    c = TestClient(app)
    c.cookies.set("pusula_session", encode_session(ADMIN))
    c.inserts, c.updates = inserts, updates  # type: ignore[attr-defined]
    return c


def test_wallet_create_defaults_date_and_validates(client) -> None:
    r = client.post("/api/finance/wallets", json={"company_id": "llc", "kind": "producer", "name": "ASPOL", "balance": "1200.5"})
    assert r.status_code == 200
    table, values = client.inserts[-1]
    assert table == "fin_wallets"
    assert values["as_of"] == web_finance._now().date()
    assert values["balance"] == D("1200.5")
    assert client.post("/api/finance/wallets", json={"company_id": "llc", "kind": "buyer", "name": "x"}).status_code == 400
    assert client.post("/api/finance/wallets", json={"company_id": "llc", "kind": "seller", "name": "x", "id": 3}).status_code == 400


def test_wallet_balance_patch_restamps_date(client) -> None:
    assert client.patch("/api/finance/wallets/1", json={"balance": "50"}).status_code == 200
    _, values = client.updates[-1]
    assert set(values) == {"balance", "as_of"}
    assert client.patch("/api/finance/wallets/1", json={"name": "Aspol Print"}).status_code == 200
    assert set(client.updates[-1][1]) == {"name"}


def test_tax_settings_put_validates(client, monkeypatch) -> None:
    saved: list = []
    monkeypatch.setattr(web_finance.store, "save_tax_settings", lambda conn, st: saved.append(st))
    ok = client.put("/api/finance/tax-settings/llc", json={
        "year_start_net_assets": "250000", "structure": "partnership",
        "partners": [{"name": "Ana", "share": "0.805"}, {"name": "Ali", "share": "0.06"}],
    })
    assert ok.status_code == 200
    assert saved[-1].company_id == "llc" and saved[-1].partners[0].share == D("0.805")
    over = client.put("/api/finance/tax-settings/llc", json={"partners": [{"name": "A", "share": "0.9"}, {"name": "B", "share": "0.2"}]})
    assert over.status_code == 400
    assert client.put("/api/finance/tax-settings/llc", json={"structure": "scorp"}).status_code == 400
    assert client.put("/api/finance/tax-settings/xx", json={}).status_code == 422
    assert client.put("/api/finance/tax-settings/llc", json={"company_id": "tr"}).status_code == 400
