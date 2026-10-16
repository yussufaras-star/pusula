"""Garanti BBVA: hesap bakiyesi + hareketler (açık bankacılık, yalnız okuma).

Ortam değişkenleri: GARANTI_API_URL, GARANTI_CLIENT_ID, GARANTI_CLIENT_SECRET,
GARANTI_CALLBACK_URL, GARANTI_CONSENT_ID (bankanın verdiği erişim onayı),
GARANTI_UNIT_NUMBER, GARANTI_ACCOUNT_NUMBER, GARANTI_IBAN. Erişim onaya bağlı
tek hesapla sınırlıdır; provider_account_id serbest (ör. hesap numarası).

Hareketlerde tutar işaretsiz gelir; yön txnCreditDebitIndicator'dan:
A = alacak (giriş), B = borç (çıkış). Sayfa başına en fazla 1500 kayıt;
toplam sayfa bilinmediği için 1500'den az gelene kadar sorgulanır.
pageIndex/pageSize gönderilmezse banka 500 döner.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from pusula.db.models import FinAccount
from pusula.finance.providers.base import (
    AdapterResult,
    NormTx,
    ProviderError,
    local_day,
    num,
    request_json,
    require_env,
)
from pusula.finance.providers.kuveytturk import windows

PAGE = 1500
MAX_PAGES = 50
TIMEOUT = 45.0
BALANCE_TYPE = "Balance"  # hesap bakiyesi; diğerleri kullanılabilir, bloke, limit


def access_token(client: httpx.Client) -> str:
    base = require_env("GARANTI_API_URL").rstrip("/")
    data = request_json(
        client,
        "Garanti",
        "POST",
        f"{base}/auth/oauth/v2/token",
        params={
            "grant_type": "client_credentials",
            "client_id": require_env("GARANTI_CLIENT_ID"),
            "client_secret": require_env("GARANTI_CLIENT_SECRET"),
            "redirect_uri": require_env("GARANTI_CALLBACK_URL"),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise ProviderError("Garanti: access_token alınamadı")
    return str(token)


def _post(client: httpx.Client, token: str, path: str, body: dict[str, Any]) -> Any:
    base = require_env("GARANTI_API_URL").rstrip("/")
    return request_json(
        client,
        "Garanti",
        "POST",
        f"{base}{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )


def find_balance(data: Any) -> Decimal | None:
    """Cevabın içinde type == 'Balance' olan bakiyeleri toplar (birden çok hesap/kalem)."""
    found: list[Decimal] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if str(node.get("type", "")).lower() == BALANCE_TYPE.lower():
                for key in ("amount", "value", "balance"):
                    if node.get(key) is not None:
                        found.append(num(node[key]))
                        break
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return sum(found, Decimal("0")) if found else None


def _rows(data: Any) -> list[dict[str, Any]]:
    """Hareket listesi: cevaptaki transactionReferenceId taşıyan kayıtlar."""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "transactionReferenceId" in node and "amount" in node:
                out.append(node)
                return
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return out


def signed_amount(row: dict[str, Any]) -> Decimal:
    amount = abs(num(row.get("amount")))
    indicator = str(row.get("txnCreditDebitIndicator") or "").upper()
    if indicator == "B":
        return -amount
    if indicator == "A":
        return amount
    raise ProviderError(f"Garanti: bilinmeyen borç/alacak işareti: {indicator[:5] or 'boş'}")


def fetch(account: FinAccount, since: datetime, client: httpx.Client) -> AdapterResult:
    consent = require_env("GARANTI_CONSENT_ID")
    token = access_token(client)
    detail = _post(
        client,
        token,
        "/balancesandmovements/accountinformation/account/v1/",
        {
            "consentId": consent,
            "unitNum": require_env("GARANTI_UNIT_NUMBER"),
            "accountNum": require_env("GARANTI_ACCOUNT_NUMBER"),
            "IBAN": require_env("GARANTI_IBAN"),
        },
    )
    balance = find_balance(detail)
    if balance is None:
        raise ProviderError("Garanti: hesap bakiyesi okunamadı")

    today = datetime.now(since.tzinfo).date()
    txs: list[NormTx] = []
    for begin, end in windows(since.date(), today):
        for page in range(1, MAX_PAGES + 1):
            data = _post(
                client,
                token,
                "/balancesandmovements/accountinformation/transaction/v1/gettransactions",
                {
                    "consentId": consent,
                    "startDate": f"{begin.isoformat()}T00:00:00.000",
                    "endDate": f"{end.isoformat()}T23:59:59.999",
                    "pageIndex": str(page),
                    "pageSize": str(PAGE),
                },
            )
            rows = _rows(data)
            for row in rows:
                when = row.get("activityDate") or row.get("valueDate")
                if not when or not row.get("transactionReferenceId"):
                    continue
                txs.append(
                    NormTx(
                        external_id=f"gb:{row['transactionReferenceId']}",
                        booked_at=local_day(str(when)),
                        amount=signed_amount(row),
                        description=" ".join(str(row.get("explanation") or "").split()),
                        # customerName hesap sahibinin kendisi (Rexven); karşı taraf değil
                        currency=(str(row["currencyCode"]).upper().replace("TL", "TRY") if row.get("currencyCode") else None),
                    )
                )
            if len(rows) < PAGE:
                break
    return AdapterResult(balance=balance, transactions=txs)
