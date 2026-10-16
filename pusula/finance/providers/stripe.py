"""Stripe: bakiye + balance_transactions.

Anahtar ortam değişkeninden: STRIPE_RESTRICTED_KEY (şirkete özel
STRIPE_KEY_<KEY> önceliklidir).

Restricted key önerilir: Balance ve Balance transactions okuma. Hesap
anahtardan belirlenir; provider_account_id serbest (ör. 'default').
Tutarlar küçük birimdedir (kuruş/cent). Bakiye = available + pending;
hareketler oluşturulma tarihinde (net, komisyon düşülmüş) yazılır.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import httpx

from pusula.db.models import FinAccount
from pusula.finance.providers.base import (
    AdapterResult,
    NormTx,
    local_day,
    num,
    provider_secret,
    request_json,
)

BASE = "https://api.stripe.com/v1"
MAX_PAGES = 100
# Ondalıksız para birimleri (100'e bölünmez).
ZERO_DECIMAL = frozenset(
    {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}
)


def to_major(minor: Decimal, currency: str) -> Decimal:
    return minor if currency.lower() in ZERO_DECIMAL else minor / 100


def fetch(account: FinAccount, since: datetime, client: httpx.Client) -> AdapterResult:
    key = provider_secret(account, "STRIPE_KEY", "STRIPE_RESTRICTED_KEY")
    headers = {"Authorization": f"Bearer {key}"}
    currency = account.currency.lower()

    data = request_json(client, "Stripe", "GET", f"{BASE}/balance", headers=headers)
    minor = sum(
        (
            num(part.get("amount"))
            for part in [*(data.get("available") or []), *(data.get("pending") or [])]
            if str(part.get("currency", "")).lower() == currency
        ),
        Decimal("0"),
    )
    balance = to_major(minor, currency)

    txs: list[NormTx] = []
    params: dict[str, str] = {"limit": "100", "created[gte]": str(int(since.timestamp()))}
    for _ in range(MAX_PAGES):
        page = request_json(client, "Stripe", "GET", f"{BASE}/balance_transactions", headers=headers, params=params)
        rows = page.get("data") or []
        for row in rows:
            row_currency = str(row.get("currency", "")).lower()
            if row_currency != currency:
                continue
            txs.append(
                NormTx(
                    external_id=str(row["id"]),
                    booked_at=local_day(int(row["created"])),
                    amount=to_major(num(row.get("net")), row_currency),
                    description=row.get("description") or row.get("type") or "",
                    # Karşı taraf hep Stripe: satış kuralı yakalar, payout aktarım eşleşir
                    counterparty="Stripe",
                    currency=row_currency.upper(),
                )
            )
        if not page.get("has_more") or not rows:
            break
        params = {**params, "starting_after": str(rows[-1]["id"])}
    return AdapterResult(balance=balance, transactions=txs)
