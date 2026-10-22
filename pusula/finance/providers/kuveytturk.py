"""Kuveyt Türk: hesap bakiyesi + hareketler (yalnız okuma).

Ortam değişkenleri: KUVEYT_TURK_IDENTITY_URL (token sunucusu),
KUVEYT_TURK_GATEWAY_URL (hesap/hareket API), KUVEYT_TURK_CLIENT_ID,
KUVEYT_TURK_CLIENT_SECRET, KUVEYT_TURK_PRIVATE_KEY (RSA PEM; tek satırda
'\\n' ile de verilebilir). provider_account_id = hesap ek numarası (suffix),
ör. ana hesap '1', cüzdan '2', döviz '101'.

Akış: client_credentials ile token (scope=accounts), her Gateway isteğinde
Signature başlığı = base64(RSA-SHA256(access_token + sorgu metni)). İmzalanan
sorgu metni gönderilenle birebir aynı olmalı; bu yüzden URL elle kurulur.
"""

from __future__ import annotations

import base64
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import httpx

from pusula.db.models import FinAccount
from pusula.finance.providers.base import (
    AdapterResult,
    NormTx,
    ProviderError,
    local_day,
    num,
    provider_id,
    request_json,
    require_env,
)
from pusula.finance.statement import bank_counterparty

WINDOW_DAYS = 29  # uzun aralıklar parçalara bölünür
TOKEN_TTL = 3500.0
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


def _pem(raw: str) -> bytes:
    return raw.replace("\\n", "\n").strip().encode("utf-8")


def sign(access_token: str, query: str, private_key_pem: str) -> str:
    """Signature başlığı: base64(RSA-SHA256(access_token + query))."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:
        raise ProviderError("Kuveyt Türk: cryptography paketi kurulu değil") from exc
    try:
        key = serialization.load_pem_private_key(_pem(private_key_pem), password=None)
    except (ValueError, TypeError) as exc:
        raise ProviderError("Kuveyt Türk: özel anahtar okunamadı (PEM olmalı)") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ProviderError("Kuveyt Türk: özel anahtar RSA olmalı")
    signature = key.sign((access_token + query).encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


def access_token(client: httpx.Client) -> str:
    identity = require_env("KUVEYT_TURK_IDENTITY_URL").rstrip("/")
    client_id = require_env("KUVEYT_TURK_CLIENT_ID")
    cached = _token_cache.get((identity, client_id))
    if cached and cached[1] > time.monotonic():
        return cached[0]
    data = request_json(
        client,
        "Kuveyt Türk",
        "POST",
        f"{identity}/connect/token",
        data={
            "grant_type": "client_credentials",
            "scope": "accounts",
            "client_id": client_id,
            "client_secret": require_env("KUVEYT_TURK_CLIENT_SECRET"),
        },
    )
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise ProviderError("Kuveyt Türk: access_token alınamadı")
    _token_cache[(identity, client_id)] = (str(token), time.monotonic() + TOKEN_TTL)
    return str(token)


def _get(client: httpx.Client, path: str, query: str = "") -> object:
    """İmzalı GET. query '?A=1&B=2' biçiminde, isteğe aynen eklenir."""
    gateway = require_env("KUVEYT_TURK_GATEWAY_URL").rstrip("/")
    token = access_token(client)
    headers = {
        "Authorization": f"Bearer {token}",
        "Signature": sign(token, query, require_env("KUVEYT_TURK_PRIVATE_KEY")),
        "Accept": "application/json",
    }
    return request_json(client, "Kuveyt Türk", "GET", f"{gateway}{path}{query}", headers=headers)


def _value(data: object) -> object:
    return data.get("value") if isinstance(data, dict) else None


def windows(start: date, end: date, size: int = WINDOW_DAYS) -> list[tuple[date, date]]:
    """[start, end] aralığını en fazla size günlük parçalara böler."""
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=size - 1), end)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def fetch(account: FinAccount, since: datetime, client: httpx.Client) -> AdapterResult:
    suffix = quote(provider_id(account), safe="")
    detail = _value(_get(client, f"/v3/accounts/{suffix}"))
    if not isinstance(detail, dict) or detail.get("balance") is None:
        raise ProviderError("Kuveyt Türk: hesap bakiyesi okunamadı")
    balance = num(detail.get("balance"))

    today = datetime.now(since.tzinfo).date()
    txs: list[NormTx] = []
    for begin, end in windows(since.date(), today):
        query = f"?BeginDate={begin.isoformat()}&EndDate={end.isoformat()}"
        value = _value(_get(client, f"/v3/accounts/{suffix}/transactions", query))
        rows = value.get("accountActivities") if isinstance(value, dict) else None
        for row in rows or []:
            reference = row.get("transactionReference")
            if not reference or not row.get("date") or row.get("amount") is None:
                continue
            amount = num(row["amount"])
            description = " ".join(str(row.get("description") or "").split())
            txs.append(
                NormTx(
                    external_id=f"kt:{reference}",
                    booked_at=local_day(str(row["date"])),
                    amount=amount,
                    description=description,
                    counterparty=bank_counterparty(description, amount),
                    currency=_currency(row.get("fxCode")),
                )
            )
    return AdapterResult(balance=balance, transactions=txs)


def _currency(code: object) -> str | None:
    """Banka 'TL' yazar; sistem ISO kodu kullanır."""
    if not code:
        return None
    text = str(code).upper().strip()
    return "TRY" if text in ("TL", "TRL") else text
