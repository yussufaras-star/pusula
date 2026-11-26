"""Finans uyarıları: yeni abonelik, olağan dışı transfer, Rexven'e işlenmemiş üretici ödemesi."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pusula.db.models import FinAccount, FinTransaction
from pusula.finance import alerts
from pusula.finance.fx import FxTable
from pusula.finance.providers.rexven import Payout

D = Decimal
FX = FxTable([])
KINDS = {"yazilim": "out", "uretici": "out", "tedarikci": "out", "hesap_arasi": "transfer", "cuzdan": "in", "maas": "out"}
TODAY = date(2026, 11, 26)
ACCOUNTS = [
    FinAccount(id=1, company_id="llc", name="Wise USD", institution="Wise", type="fintech", currency="USD"),
    FinAccount(id=2, company_id="llc", name="Mercury IO kart", institution="Mercury", type="card", currency="USD"),
]
_ids = iter(range(1, 10_000))


def tx(day, amount, cp, category=None, account_id=1, currency="USD"):
    return FinTransaction(
        id=next(_ids), account_id=account_id, company_id="llc", booked_at=day, amount=D(amount), currency=currency,
        description=cp, counterparty=cp, category_id=category, external_id=f"{day}{cp}{amount}{next(_ids)}", source="api",
    )


def payout(day, amount, currency="USD"):
    return Payout(next(_ids), 2, 500, currency, D(amount), datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc), "")


def detect(rows, payouts=(), scanned=TODAY):
    found, matched = alerts.detect(rows, ACCOUNTS, KINDS, FX, TODAY, payouts, scanned)
    return {(c.kind, c.tx_id): c.reason for c in found}, matched


def test_first_subscription_charge_alerts_once() -> None:
    old = tx(date(2026, 6, 1), "-20", "Zoho", "yazilim")
    again = tx(date(2026, 11, 20), "-20", "Zoho", "yazilim")
    new = tx(date(2026, 11, 21), "-49", "Runpod", "yazilim")
    card = tx(date(2026, 11, 22), "-9", "Unknown SaaS", None, account_id=2)
    found, _ = detect([old, again, new, card])
    assert set(found) == {("abonelik", new.id), ("abonelik", card.id)}


def test_unusual_transfers() -> None:
    history = [tx(date(2026, m, 5), "-2000", "Yüklenici A", "maas") for m in range(5, 11)]
    big_known = tx(date(2026, 11, 20), "-7000", "Yüklenici A", "maas")
    new_payee = tx(date(2026, 11, 21), "-1500", "Acme Ltd")
    small_new = tx(date(2026, 11, 21), "-300", "Small Co")
    huge = tx(date(2026, 11, 22), "-30000", "Yüklenici A", "maas")
    own = tx(date(2026, 11, 22), "-90000", "Mercury Credit", "hesap_arasi")
    old_new = tx(date(2026, 10, 1), "-5000", "Eski Alıcı")  # pencere dışı
    found, _ = detect([*history, big_known, new_payee, small_new, huge, own, old_new])
    assert set(found) == {("olagandisi", big_known.id), ("olagandisi", new_payee.id), ("olagandisi", huge.id)}
    assert "3.5 katı" in found[("olagandisi", big_known.id)]
    assert "Yeni alıcı" in found[("olagandisi", new_payee.id)]
    assert "Tek seferde büyük" in found[("olagandisi", huge.id)]


def test_producer_payment_must_be_deducted_in_rexven() -> None:
    paid = tx(date(2026, 11, 9), "-5000", "Üretici LLC", "tedarikci")
    missing = tx(date(2026, 11, 12), "-3000", "Üretici LLC", "tedarikci")
    recent = tx(date(2026, 11, 25), "-9000", "Üretici LLC", "tedarikci")  # Rexven'e işlenmesi için süre var
    tl = tx(date(2026, 10, 30), "-100000", "ÜRETİCİ AŞ", "uretici", currency="TRY")
    payouts = [payout(date(2026, 11, 10), "5000"), payout(date(2026, 10, 30), "100000.00", "TRY"), payout(date(2026, 11, 12), "3000", "TRY")]
    found, matched = detect([paid, missing, recent, tl], payouts)
    assert set(found) == {("uretici", missing.id)}  # para birimi farklı Rexven kaydı eşleşmez
    assert matched == {paid.id, tl.id}


def test_producer_check_waits_for_rexven_scan() -> None:
    missing = tx(date(2026, 11, 12), "-3000", "Üretici LLC", "tedarikci")
    assert detect([missing], [], scanned=None)[0] == {}
    # tarama bankadan 3 gün sonrasını kapsamıyor
    assert detect([missing], [], scanned=date(2026, 11, 14))[0] == {}


def test_each_payout_matches_one_payment() -> None:
    a = tx(date(2026, 11, 3), "-7000", "Üretici LLC", "tedarikci")
    b = tx(date(2026, 11, 5), "-7000", "Üretici LLC", "tedarikci")
    found, matched = detect([a, b], [payout(date(2026, 11, 3), "7000")])
    assert matched == {a.id} and set(found) == {("uretici", b.id)}


def test_notice_text_lists_and_links() -> None:
    row = {"kind": "uretici", "status": "riskli", "booked_at": date(2026, 11, 12), "account": "Wise USD", "counterparty": "Üretici LLC",
           "description": "", "amount": D("-3000"), "currency": "USD", "reason": "Rexven'de düşülmedi"}
    text = alerts.notice_text([row] * 10, "https://panel.example/")
    assert text.startswith("Finans uyarısı: 10 hareket")
    assert "Üretici ödemesi (hatırlatma): 12.11 Wise USD → Üretici LLC, 3.000,00 USD" in text
    assert "…ve 2 uyarı daha" in text
    assert text.endswith("https://panel.example/#/finans/uyarilar")


def test_due_includes_expired_reminders() -> None:
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    assert alerts.due({"status": "acik", "remind_at": None}, now)
    assert alerts.due({"status": "riskli", "remind_at": now - timedelta(minutes=1)}, now)
    assert not alerts.due({"status": "riskli", "remind_at": now + timedelta(days=1)}, now)
    assert not alerts.due({"status": "guven", "remind_at": None}, now)
