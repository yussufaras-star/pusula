"""Düzenli ödemeler: gruplama, sıklık, aylık maliyet, iade eşleşmesi, artış / durma işareti."""

from datetime import date, timedelta
from decimal import Decimal

from pusula.db.models import FinTransaction
from pusula.finance.fx import FxTable
from pusula.finance.recurring import cadence_of, recurring

D = Decimal
FX = FxTable([])
KINDS = {"yazilim": "out", "satis": "in", "hesap_arasi": "transfer", "cuzdan": "in", "ortak": "out"}
START, END = date(2026, 4, 20), date(2026, 10, 19)
_ids = iter(range(1, 10_000))


def tx(day, amount, cp, category=None, account_id=1):
    return FinTransaction(
        id=next(_ids), account_id=account_id, company_id="llc", booked_at=day, amount=D(amount), currency="USD",
        description=cp, counterparty=cp, category_id=category, external_id=str(day) + cp + str(amount), source="api",
    )


def monthly(cp, amounts, first=date(2026, 7, 29), category="yazilim"):
    return [tx(first + timedelta(days=30 * i), -a, cp, category) for i, a in enumerate(amounts)]


def test_cadence_names() -> None:
    assert [cadence_of(g) for g in (2, 14, 30, 91, 365, 50)] == ["sık", "iki haftalık", "aylık", "üç aylık", "yıllık", "düzensiz"]


def test_monthly_subscription_and_span_from_data_start() -> None:
    rows = monthly("Zoho Corporation", [100, 100, 100])
    [r] = recurring(rows, FX, KINDS, START, END)
    assert (r.label, r.category_id, r.count, r.months, r.cadence) == ("Zoho Corporation", "yazilim", 3, 3, "aylık")
    assert r.total_usd == D("300")
    # veri 29 Tem'de başlıyor: 6 aylık aralığa değil ~2,8 aya bölünür
    assert D("105") < r.monthly_usd < D("115")
    assert r.next_due == date(2026, 9, 27) + timedelta(days=30)
    assert not r.rising and not r.stopped


def test_single_month_transfers_and_wallet_are_excluded() -> None:
    rows = [
        tx(date(2026, 9, 1), "-50", "Tek ay"), tx(date(2026, 9, 20), "-50", "Tek ay"),
        *[tx(date(2026, m, 5), "-900", "Mercury Credit", "hesap_arasi") for m in (8, 9, 10)],
        *[tx(date(2026, m, 5), "-30", "Stripe", "satis") for m in (8, 9, 10)],  # iade (giriş kategorisi)
    ]
    assert recurring(rows, FX, KINDS, START, END) == []


def test_refund_only_offsets_a_matching_payment() -> None:
    rows = monthly("Murat Fatih YILMAZ", [2000, 97458, 2000], category="ortak")
    rows.append(tx(date(2026, 9, 2), "97458", "Murat Fatih YILMAZ", "ortak"))  # hatalı gönderim iadesi
    [r] = recurring(rows, FX, KINDS, START, END)
    assert r.total_usd == D("4000")

    rows = monthly("güzelsoy", [6000, 6000, 6000], category="ortak")
    rows.append(tx(date(2026, 8, 1), "56019", "güzelsoy", "ortak"))  # ilgisiz büyük giriş ödemeleri silmez
    [r] = recurring(rows, FX, KINDS, START, END)
    assert r.total_usd == D("18000")


def test_rising_and_stopped_flags() -> None:
    [r] = recurring(monthly("Shipstation", [100, 100, 100, 150], first=date(2026, 6, 29)), FX, KINDS, START, END)
    assert r.rising and r.last_usd == D("150")
    [s] = recurring(monthly("Eski", [40, 40], first=date(2026, 6, 1)), FX, KINDS, START, END)
    assert s.stopped
