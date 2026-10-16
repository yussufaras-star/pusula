"""Kur tablosu: o gün veya öncesindeki en yakın kur, eksik kurda uydurma yok."""

from datetime import date
from decimal import Decimal

from pusula.db.models import FinFxRate
from pusula.finance.fx import FxTable, parse_frankfurter


def _fx() -> FxTable:
    return FxTable(
        [
            FinFxRate(date=date(2026, 10, 1), currency="EUR", usd_rate=Decimal("1.10")),
            FinFxRate(date=date(2026, 10, 5), currency="EUR", usd_rate=Decimal("1.20")),
            FinFxRate(date=date(2026, 10, 2), currency="TRY", usd_rate=Decimal("0.03")),
        ]
    )


def test_usd_is_always_one() -> None:
    assert _fx().rate("USD", date(2000, 1, 1)) == Decimal("1")


def test_rate_on_or_before_date() -> None:
    fx = _fx()
    assert fx.rate("EUR", date(2026, 10, 1)) == Decimal("1.10")
    # hafta sonu / tatil: önceki en yakın kur
    assert fx.rate("EUR", date(2026, 10, 4)) == Decimal("1.10")
    assert fx.rate("EUR", date(2026, 10, 5)) == Decimal("1.20")
    assert fx.rate("EUR", date(2026, 12, 31)) == Decimal("1.20")


def test_before_first_rate_uses_first() -> None:
    assert _fx().rate("EUR", date(2026, 9, 1)) == Decimal("1.10")


def test_latest_rate_without_date() -> None:
    assert _fx().rate("EUR") == Decimal("1.20")
    assert _fx().latest_date() == date(2026, 10, 5)


def test_missing_currency_is_reported_not_invented() -> None:
    fx = _fx()
    assert fx.rate("GBP") is None
    assert fx.to_usd(Decimal("100"), "GBP") == Decimal("0")
    assert fx.missing == {"GBP"}


def test_to_usd_uses_transaction_date() -> None:
    fx = _fx()
    assert fx.to_usd(Decimal("100"), "EUR", date(2026, 10, 3)) == Decimal("110.00")
    assert fx.to_usd(Decimal("-100"), "EUR", date(2026, 10, 6)) == Decimal("-120.00")


def test_parse_frankfurter_latest_inverts_to_usd_rate() -> None:
    payload = {"base": "USD", "date": "2026-10-15", "rates": {"EUR": 0.8, "TRY": 40, "JPY": 150}}
    rates = {r.currency: r for r in parse_frankfurter(payload)}
    assert set(rates) == {"EUR", "TRY"}
    assert rates["EUR"].usd_rate == Decimal("1.25000000")
    assert rates["TRY"].usd_rate == Decimal("0.02500000")
    assert rates["EUR"].date == date(2026, 10, 15)


def test_parse_frankfurter_range() -> None:
    payload = {
        "base": "USD",
        "start_date": "2026-10-01",
        "rates": {"2026-10-01": {"GBP": 0.8}, "2026-10-02": {"GBP": 0.5}},
    }
    rates = parse_frankfurter(payload)
    assert [(r.date.day, r.usd_rate) for r in rates] == [
        (1, Decimal("1.25000000")),
        (2, Decimal("2.00000000")),
    ]
