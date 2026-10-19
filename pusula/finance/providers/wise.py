"""Wise: provider_account_id = '<profileId>:<balanceId>'.

Token ortam değişkeninden: WISE_API_TOKEN (şirkete özel WISE_TOKEN_<KEY>
önceliklidir).

AB/UK profillerinde bakiye ekstresi Güçlü Müşteri Doğrulaması (SCA) ister:
API 403 + 'x-2fa-approval' başlığı döner. Bu durumda hesap düşmez; bakiye
güncellenir, hareket alınmaz ve uyarı yazılır. Hareketler için ekstre
yükleme (CSV) kullanılabilir.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from pusula.db.models import FinAccount
from pusula.finance.providers.base import (
    AdapterResult,
    NormTx,
    ProviderError,
    ProviderHttpError,
    local_day,
    num,
    provider_id,
    provider_secret,
    request_json,
)

BASE = "https://api.wise.com"
SCA_WARNING = "Wise: hareketler için SCA gerekli, yalnız bakiye güncellendi"

# Giden transferin iade bacağı: referans + bu ek (çıkış bacağı referansın kendisi).
REFUND_SUFFIX = ":iade"

def fetch(account: FinAccount, since: datetime, client: httpx.Client) -> AdapterResult:
    token = provider_secret(account, "WISE_TOKEN", "WISE_API_TOKEN")
    profile_id, _, balance_id = provider_id(account).partition(":")
    if not profile_id or not balance_id:
        raise ProviderError("Wise: provider_account_id '<profileId>:<balanceId>' olmalı")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    balances = request_json(
        client,
        "Wise",
        "GET",
        f"{BASE}/v4/profiles/{profile_id}/balances",
        headers=headers,
        params={"types": "STANDARD"},
    )
    found = next((b for b in balances or [] if str(b.get("id")) == balance_id), None)
    if found is None:
        raise ProviderError("Wise: balance bulunamadı")
    balance = num((found.get("amount") or {}).get("value"))

    try:
        statement = request_json(
            client,
            "Wise",
            "GET",
            f"{BASE}/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.json",
            headers=headers,
            params={
                "currency": account.currency,
                "intervalStart": since.isoformat(),
                "intervalEnd": datetime.now(since.tzinfo).isoformat(),
                "type": "COMPACT",
            },
        )
    except ProviderHttpError as exc:
        if exc.status == 403 and "x-2fa-approval" in exc.headers:
            return AdapterResult(balance=balance, transactions=[], warning=SCA_WARNING)
        raise

    txs: list[NormTx] = []
    for row in statement.get("transactions") or []:
        amount = row.get("amount") or {}
        if not row.get("referenceNumber") or not row.get("date") or amount.get("value") is None:
            continue
        details = row.get("details") or {}
        counterparty = (
            details.get("senderName")
            or (details.get("recipient") or {}).get("name")
            or (details.get("merchant") or {}).get("name")
        )
        value = num(amount["value"])  # işaretli
        external_id = str(row["referenceNumber"])
        if value > 0 and details.get("recipient"):
            # Giden transferin iadesi (iptal / geri dönüş): Wise aynı referansı
            # verir; ayrı kimlik yoksa çıkış ya da iade bacağı tekil kısıta takılır.
            external_id += REFUND_SUFFIX
        txs.append(
            NormTx(
                external_id=external_id,
                booked_at=local_day(row["date"]),
                amount=value,
                description=details.get("description") or counterparty or details.get("type") or "",
                counterparty=counterparty,
                currency=amount.get("currency"),
            )
        )
    return AdapterResult(balance=balance, transactions=txs)
