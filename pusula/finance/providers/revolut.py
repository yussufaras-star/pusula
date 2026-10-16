"""Revolut Business: refresh token + RS256 client assertion ile erişim token'ı.

Ortam değişkenleri (<KEY> = credential_key):
REVOLUT_CLIENT_ID_<KEY>, REVOLUT_PRIVATE_KEY_<KEY> (PKCS8 PEM; tek satırda
'\\n' ile de verilebilir), REVOLUT_REFRESH_TOKEN_<KEY>, REVOLUT_ISS_<KEY>
(OAuth redirect alan adı, https:// olmadan).
provider_account_id = Revolut hesap id'si (para birimine göre).
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from urllib.parse import quote

import httpx

from pusula.db.models import FinAccount
from pusula.finance.providers.base import (
    AdapterResult,
    NormTx,
    ProviderError,
    cred_key,
    local_day,
    num,
    provider_id,
    request_json,
    require_env,
)

BASE = "https://b2b.revolut.com/api/1.0"
AUDIENCE = "https://revolut.com"
ASSERTION_SECONDS = 300
PAGE = 1000
MAX_PAGES = 50


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def client_assertion(client_id: str, issuer: str, pem: str, now: float | None = None) -> str:
    """RS256 imzalı JWT (iss, sub=client_id, aud=revolut, exp)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:
        raise ProviderError("Revolut: cryptography paketi kurulu değil") from exc
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise ProviderError("Revolut: özel anahtar okunamadı (PKCS8 PEM olmalı)") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ProviderError("Revolut: özel anahtar RSA olmalı")
    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iss": issuer, "sub": client_id, "aud": AUDIENCE, "exp": issued + ASSERTION_SECONDS}
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode("utf-8")) for part in (header, payload)
    )
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def access_token(key: str, client: httpx.Client) -> str:
    client_id = require_env(f"REVOLUT_CLIENT_ID_{key}")
    # Secret'ta satır sonları '\n' olarak saklanmış olabilir.
    pem = require_env(f"REVOLUT_PRIVATE_KEY_{key}").replace("\\n", "\n")
    refresh = require_env(f"REVOLUT_REFRESH_TOKEN_{key}")
    issuer = require_env(f"REVOLUT_ISS_{key}")
    data = request_json(
        client,
        "Revolut",
        "POST",
        f"{BASE}/auth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": client_assertion(client_id, issuer, pem),
        },
    )
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise ProviderError("Revolut: access_token alınamadı")
    return str(token)


def fetch(account: FinAccount, since: datetime, client: httpx.Client) -> AdapterResult:
    token = access_token(cred_key(account), client)
    account_id = provider_id(account)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    info = request_json(client, "Revolut", "GET", f"{BASE}/accounts/{quote(account_id, safe='')}", headers=headers)
    balance = num(info.get("balance"))

    txs: list[NormTx] = []
    # Sonuçlar yeniden eskiye; 'to' ile önceki sayfanın en eski kaydına kadar.
    to: str | None = None
    for _ in range(MAX_PAGES):
        params = {"account": account_id, "from": since.date().isoformat(), "count": str(PAGE)}
        if to:
            params["to"] = to
        rows = request_json(client, "Revolut", "GET", f"{BASE}/transactions", headers=headers, params=params)
        for row in rows or []:
            if str(row.get("state") or "").lower() != "completed":
                continue
            when = row.get("completed_at") or row["created_at"]
            for leg in row.get("legs") or []:
                if leg.get("account_id") != account_id:
                    continue
                txs.append(
                    NormTx(
                        external_id=f"{row['id']}:{leg['leg_id']}",
                        booked_at=local_day(when),
                        amount=num(leg.get("amount")),  # işaretli
                        description=leg.get("description") or row.get("reference") or "",
                        counterparty=(leg.get("counterparty") or {}).get("name")
                        or (row.get("merchant") or {}).get("name"),
                        currency=leg.get("currency"),
                    )
                )
        if len(rows or []) < PAGE:
            break
        oldest = rows[-1].get("created_at")
        if not oldest or oldest == to:
            break
        to = oldest
    return AdapterResult(balance=balance, transactions=txs)
