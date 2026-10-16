"""Sağlayıcı adaptörlerinin ortak tipleri ve yardımcıları.

Kimlik bilgileri yalnız ortam değişkeninden okunur; hata mesajlarında
token, anahtar veya istek başlığı yer almaz, yalnız değişken adı geçer.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from pusula.db.models import FinAccount
from pusula.metrics import TZ

HTTP_TIMEOUT = 20.0


@dataclass(frozen=True)
class NormTx:
    """Normalize hareket: işaretli, ana birimde (kuruş değil)."""

    external_id: str
    booked_at: date
    amount: Decimal
    description: str
    counterparty: str | None = None
    # Sağlayıcı veriyorsa hareketin para birimi; hesabınkiyle uyuşmazsa atılır.
    currency: str | None = None


@dataclass(frozen=True)
class AdapterResult:
    balance: Decimal
    transactions: list[NormTx] = field(default_factory=list)
    # Hesabı düşürmeyen uyarı (sync_warning'e yazılır), ör. Wise SCA.
    warning: str | None = None


Adapter = Callable[[FinAccount, datetime, httpx.Client], AdapterResult]


class ProviderError(Exception):
    """Kısa, kullanıcıya gösterilebilir sağlayıcı hatası."""


class ProviderHttpError(ProviderError):
    def __init__(self, provider: str, status: int, headers: httpx.Headers, detail: str) -> None:
        self.provider = provider
        self.status = status
        self.headers = headers
        super().__init__(f"{provider}: HTTP {status}" + (f" – {detail}" if detail else ""))


def short_detail(text: str) -> str:
    """Hata gövdesinden tek satırlık özet."""
    message: Any = text
    try:
        body = json.loads(text)
        if isinstance(body, dict):
            error = body.get("error")
            message = (
                body.get("message")
                or (error.get("message") if isinstance(error, dict) else error)
                or body.get("error_description")
                or ((body.get("errors") or [{}])[0].get("message") if isinstance(body.get("errors"), list) else None)
                or text
            )
    except (ValueError, AttributeError, IndexError):
        pass
    if not isinstance(message, str):
        message = json.dumps(message, ensure_ascii=False)
    return re.sub(r"\s+", " ", message).strip()[:120]


def request_json(
    client: httpx.Client, provider: str, method: str, url: str, **kwargs: Any
) -> Any:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ProviderError(f"{provider}: zaman aşımı") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"{provider}: bağlantı hatası") from exc
    if response.status_code >= 400:
        raise ProviderHttpError(provider, response.status_code, response.headers, short_detail(response.text))
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(f"{provider}: geçersiz JSON yanıtı") from exc


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProviderError(f"Eksik ortam değişkeni: {name}")
    return value


def provider_secret(account: FinAccount, scoped_prefix: str, global_name: str) -> str:
    """Sağlayıcı anahtarı yalnız ortam değişkeninden: önce şirkete özel
    <scoped_prefix>_<KEY> (ör. ikinci bir şirketin ayrı token'ı), yoksa
    ortak ad (ör. MERCURY_API_TOKEN). Değer koda veya DB'ye yazılmaz."""
    scoped = f"{scoped_prefix}_{cred_key(account)}"
    for name in (scoped, global_name):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ProviderError(f"Eksik ortam değişkeni: {global_name}")


def cred_key(account: FinAccount) -> str:
    if not account.credential_key:
        raise ProviderError("credential_key tanımlı değil")
    return account.credential_key.upper()


def provider_id(account: FinAccount) -> str:
    if not account.provider_account_id:
        raise ProviderError("provider_account_id tanımlı değil")
    return account.provider_account_id


def num(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ProviderError(f"Geçersiz tutar: {value!r}")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ProviderError(f"Geçersiz tutar: {value!r}") from exc


def local_day(value: str | int | float) -> date:
    """ISO zaman damgası veya unix saniyesi → İstanbul tarihi."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, TZ).date()
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderError(f"Geçersiz tarih: {text[:40]}") from exc
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(TZ).date()
