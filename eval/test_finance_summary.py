"""Finans yönetici özeti: biçim, dönem etiketi, gün sonu nakit (veritabanısız)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from pusula import finance_card
from pusula import finance_summary as fs


class Fx:
    missing: set[str] = set()

    def to_usd(self, amount, currency, day=None):
        return amount / Decimal("40") if currency == "TRY" else amount


def test_usd_format_uses_bin_and_mn() -> None:
    assert fs.usd(882_500) == "$882,5 bin"
    assert fs.usd(1_234_000) == "$1,23 Mn"
    assert fs.usd(-3_365, sign=True) == "−$3,4 bin" and fs.usd(25, sign=True) == "+$25"


def test_period_label() -> None:
    assert fs.period_label({"first": "2026-10-22", "last": "2026-10-22"}) == "22 Ekim Perşembe"
    assert fs.period_label({"first": "2026-10-24", "last": "2026-10-25"}) == "24 Ekim – 25 Ekim"


def test_cash_on_rolls_back_later_transactions() -> None:
    accounts = [
        SimpleNamespace(id=1, company_id="llc", currency="USD", current_balance=Decimal("1000"), active=True),
        SimpleNamespace(id=2, company_id="tr", currency="TRY", current_balance=Decimal("4000"), active=True),
    ]
    txs = [
        SimpleNamespace(account_id=1, booked_at=date(2026, 10, 22), amount=Decimal("300")),
        SimpleNamespace(account_id=1, booked_at=date(2026, 10, 20), amount=Decimal("50")),
    ]
    out = fs.cash_on(accounts, txs, Fx(), date(2026, 10, 21))
    assert out == {"llc": Decimal("700"), "tr": Decimal("100")}


def test_card_shows_stale_company_and_warnings() -> None:  # ve hesapsız şirket
    s = {
        "first": "2026-10-22", "last": "2026-10-22", "nakit": 882_500.0, "nakit_degisim": 44_900.0,
        "giris": 33_800.0, "cikis": 3_500.0, "hareketler": [],
        "ay": {"ad": "Ekim", "giris": 850_800.0, "cikis": 695_800.0, "net": 155_000.0},
        "sirketler": [{"id": "tr", "ad": "Rexven TR", "para": "TRY", "usd": 48_173.0, "yerel": 2_390_761.0,
                       "degisim": -25.0, "eski_veri": "2026-05-25"},
                      {"id": "gmbh", "ad": "Rexven GmbH", "para": "EUR", "usd": None, "yerel": None,
                       "degisim": None, "eski_veri": None, "hesap_yok": True}],
        "projeksiyon": {"en_dusuk_hafta": "2026-10-19", "en_dusuk": 882_500.0, "acik_hafta": None},
        "uyarilar": {"geciken": 2, "geciken_usd": 1_500.0, "hata": [], "eski": ["KuveytTürk Ana Hesap"], "aksiyon": 0},
        "kur_eksik": [],
    }
    html = finance_card.render_html(s)
    assert "Finans Operasyonu Yönetici Özeti" in html and "veri 2026-05-25 tarihli" in html
    assert "2 gecikmiş planlı kalem" in html and "Eski veri: KuveytTürk Ana Hesap" in html
    assert "Rexven GmbH" in html and "bağlı hesap yok" in html
    assert "En büyük hareketler" not in html  # hareket yoksa bölüm yok
