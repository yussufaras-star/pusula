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
    recent = tx(date(2026, 11, 25), "-9000", "Üretici LLC", "tedarikci")  # bekleme yok: hemen uyarı
    tl = tx(date(2026, 10, 30), "-100000", "ÜRETİCİ AŞ", "uretici", currency="TRY")
    payouts = [payout(date(2026, 11, 10), "5000"), payout(date(2026, 10, 30), "100000.00", "TRY"), payout(date(2026, 11, 12), "3000", "TRY")]
    found, matched = detect([paid, missing, recent, tl], payouts)
    assert set(found) == {("uretici", missing.id), ("uretici", recent.id)}  # para birimi farklı Rexven kaydı eşleşmez
    assert matched == {paid.id, tl.id}


def test_producer_check_waits_for_rexven_scan() -> None:
    missing = tx(date(2026, 11, 12), "-3000", "Üretici LLC", "tedarikci")
    assert detect([missing], [], scanned=None)[0] == {}
    # tarama banka gününe ulaşmadı; ulaşınca hemen uyarı
    assert detect([missing], [], scanned=date(2026, 11, 11))[0] == {}
    assert ("uretici", missing.id) in detect([missing], [], scanned=date(2026, 11, 12))[0]


def test_each_payout_matches_one_payment() -> None:
    a = tx(date(2026, 11, 3), "-7000", "Üretici LLC", "tedarikci")
    b = tx(date(2026, 11, 5), "-7000", "Üretici LLC", "tedarikci")
    found, matched = detect([a, b], [payout(date(2026, 11, 3), "7000")])
    assert matched == {a.id} and set(found) == {("uretici", b.id)}


def test_notice_card_groups_by_kind_and_links() -> None:
    base = {"account": "Wise USD", "description": "", "currency": "USD"}
    producer = base | {"kind": "uretici", "status": "riskli", "booked_at": date(2026, 11, 12), "counterparty": "Üretici LLC",
                       "amount": D("-3000"), "reason": "Bankadan çıktı, Rexven'de üretici cüzdanından düşülmedi"}
    unusual = base | {"kind": "olagandisi", "status": "acik", "booked_at": date(2026, 11, 13), "counterparty": "Yeni Alıcı",
                      "amount": D("-30000"), "reason": "Yeni alıcıya ilk ödeme (30.000 $); Tek seferde büyük çıkış (30.000 $)"}
    now = datetime(2026, 11, 27, 9, tzinfo=timezone.utc)
    text, card, slides = alerts.notice_card([producer] * 3 + [unusual] * 7, "https://panel.example/", now)
    assert text == "*10 finans uyarısı karar bekliyor* · [Güven / Riskli seç](https://panel.example/#/finans/uyarilar)"
    assert card["title"] == "Finans uyarıları · 27.11.2026"
    assert [s.get("title") for s in slides] == ["Üretici ödemesi · 3", "Olağan dışı transfer · 5", None]
    assert slides[0]["data"]["rows"][0] == {"Tarih": "12.11", "Kime": "Üretici LLC", "Tutar": "3.000,00 USD", "Neden": "Hatırlatma · Rexven'de düşülmemiş"}
    assert slides[1]["data"]["rows"][0]["Neden"] == "Yeni alıcıya ilk ödeme · Tek seferde büyük çıkış"
    assert slides[2] == {"type": "text", "data": "…ve 2 uyarı daha panelde."}


def test_due_includes_expired_reminders() -> None:
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    assert alerts.due({"status": "acik", "remind_at": None}, now)
    assert alerts.due({"status": "riskli", "remind_at": now - timedelta(minutes=1)}, now)
    assert not alerts.due({"status": "riskli", "remind_at": now + timedelta(days=1)}, now)
    assert not alerts.due({"status": "guven", "remind_at": None}, now)
