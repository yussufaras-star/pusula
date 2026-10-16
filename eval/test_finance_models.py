"""Finans pydantic modelleri DB CHECK kısıtlarıyla aynı kuralları uygular."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pusula.db.models import (
    FinAccount,
    FinFxRate,
    FinPlannedItem,
    FinSnapshot,
    FinTransaction,
)


def test_api_account_needs_credential_key() -> None:
    with pytest.raises(ValidationError):
        FinAccount(company_id="llc", name="Ops", institution="Mercury", currency="USD", provider="mercury")
    account = FinAccount(
        company_id="llc",
        name="Ops",
        institution="Mercury",
        currency="USD",
        provider="mercury",
        credential_key="LLC",
    )
    assert account.current_balance == Decimal("0")


def test_csv_account_without_credential_is_fine() -> None:
    FinAccount(company_id="tr", name="Garanti TL", institution="Garanti", currency="TRY", provider="csv")


def test_credential_key_is_env_suffix_only() -> None:
    with pytest.raises(ValidationError):
        FinAccount(
            company_id="llc",
            name="Ops",
            institution="Mercury",
            currency="USD",
            provider="mercury",
            credential_key="secret-token-value",
        )


def test_transaction_amount_is_signed_two_decimals() -> None:
    tx = FinTransaction(
        account_id=1,
        company_id="gmbh",
        booked_at=date(2026, 10, 1),
        amount=Decimal("-45.10"),
        currency="EUR",
        external_id="x",
        source="csv",
    )
    assert tx.amount == Decimal("-45.10")
    with pytest.raises(ValidationError):
        FinTransaction(
            account_id=1,
            company_id="gmbh",
            booked_at=date(2026, 10, 1),
            amount=Decimal("1.234"),
            currency="EUR",
            external_id="x",
            source="csv",
        )


def test_unknown_company_or_currency_rejected() -> None:
    with pytest.raises(ValidationError):
        FinFxRate(date=date(2026, 10, 1), currency="try", usd_rate=Decimal("0.03"))
    with pytest.raises(ValidationError):
        FinFxRate(date=date(2026, 10, 1), currency="TRY", usd_rate=Decimal("0"))
    with pytest.raises(ValidationError):
        FinSnapshot(company_id="ltd", week_date=date(2026, 10, 12))


def test_planned_item_rules() -> None:
    with pytest.raises(ValidationError):
        FinPlannedItem(
            company_id="tr",
            direction="out",
            title="Kira",
            amount=Decimal("-10"),
            currency="TRY",
            due_date=date(2026, 10, 1),
        )
    with pytest.raises(ValidationError):
        FinPlannedItem(
            company_id="tr",
            direction="out",
            title="Kira",
            amount=Decimal("10"),
            currency="TRY",
            due_date=date(2026, 10, 1),
            recurrence="monthly",
            recur_until=date(2026, 9, 1),
        )
    with pytest.raises(ValidationError):
        FinPlannedItem(
            company_id="tr",
            direction="in",
            title="Alacak",
            amount=Decimal("10"),
            currency="TRY",
            due_date=date(2026, 10, 1),
            probability=Decimal("1.5"),
        )


def test_snapshot_week_is_monday() -> None:
    FinSnapshot(company_id="llc", week_date=date(2026, 10, 12))
    with pytest.raises(ValidationError):
        FinSnapshot(company_id="llc", week_date=date(2026, 10, 14))
