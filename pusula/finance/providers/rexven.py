"""Rexven internal API: AŞ'nin Kuveyt Türk hesapları ve platform cüzdanları (yalnız okuma).

Adres yalnız NetBird ağından erişilir. GitHub senkronu çalışırken ağa katılır
ve REXVEN_NETBIRD=1 verir (finance-sync.yml); bu değişken olmayan ortamda
(ör. Vercel'deki panel butonu) Rexven hesapları ve cüzdanlar atlanır.

Kimlik: refresh token (rxir_, 3 gün) POST /token ile access token'a (rxia_,
60 sn) çevrilir. Yanıt yeni bir refresh token da döner ve eskisi geçersizleşir;
bu yüzden tek kopya fin_provider_tokens'tadır. Kayıtlı token reddedilirse
REXVEN_API_REFRESH_TOKEN denenir: rexadmin → Finans → API Erişimi'nden yeni
token üretilip bu değişkene yazılınca zincir yeniden başlar.

provider_account_id = Kuveyt Türk hesap ek numarası (ana 1, cüzdan 2, döviz 101).
Sınır dakikada 60 istek: istekler arasında en az MIN_GAP saniye beklenir.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from pusula.db.models import FinAccount
from pusula.finance.providers.base import (
    AdapterResult,
    ProviderError,
    ProviderHttpError,
    num,
    provider_id,
    request_json,
)
from pusula.finance.providers.kuveytturk import activities, windows

PROVIDER = "Rexven"
BASE = "https://rexapi-internal.rexven.com/api/internal-api/v1"
MIN_GAP = 1.1
TOKEN_MARGIN = 15.0  # access token bitmeden bu kadar önce yenilenir
PAGE_LIMIT = 1000
MAX_PAGES = 200
# Cüzdan para birimi → şirket: dolar cüzdanlarına LLC hesaplarından, TL
# cüzdanlarına AŞ'nin Kuveyt cüzdan hesabından para girer.
WALLET_COMPANY = {"USD": "llc", "TRY": "tr"}
WALLET_KIND = {"seller": "seller", "supplier": "producer"}

_access: dict[str, Any] = {"token": None, "until": 0.0}
_last_call = [0.0]


def reachable() -> bool:
    return os.environ.get("REXVEN_NETBIRD", "").strip() == "1"


def _base() -> str:
    return (os.environ.get("REXVEN_API_URL", "").strip() or BASE).rstrip("/")


def _throttle() -> None:
    wait = _last_call[0] + MIN_GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    """429'da Retry-After kadar bekleyip en fazla 3 kez yeniden dener."""
    for attempt in range(4):
        _throttle()
        try:
            return request_json(client, PROVIDER, method, f"{_base()}{path}", **kwargs)
        except ProviderHttpError as exc:
            if exc.status != 429 or attempt == 3:
                raise
            time.sleep(min(int(exc.headers.get("retry-after") or 15), 60))
    raise ProviderError(f"{PROVIDER}: istek sınırı")


def rotate_refresh_token(exchange: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """Kayıtlı refresh token'ı satır kilidiyle okur, exchange ile yeniler ve
    dönen yeni token'ı aynı işlemde yazar. Kayıtlı token reddedilirse ortam
    değişkenindeki denenir."""
    from pusula.panel_data import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT refresh_token FROM fin_provider_tokens WHERE provider = 'rexven' FOR UPDATE"
        ).fetchone()
        stored = row[0] if row else None
        candidates = [t for t in dict.fromkeys((stored, os.environ.get("REXVEN_API_REFRESH_TOKEN", "").strip())) if t]
        if not candidates:
            raise ProviderError("Eksik ortam değişkeni: REXVEN_API_REFRESH_TOKEN")
        data: dict[str, Any] | None = None
        for i, token in enumerate(candidates):
            try:
                data = exchange(token)
                break
            except ProviderHttpError as exc:
                if exc.status not in (400, 401, 403):
                    raise
                if i == len(candidates) - 1:
                    raise ProviderError(
                        f"{PROVIDER}: refresh token reddedildi; rexadmin → Finans → API Erişimi'nden yenisini"
                        " üretip REXVEN_API_REFRESH_TOKEN'a yazın"
                    ) from exc
        assert data is not None
        conn.execute(
            "INSERT INTO fin_provider_tokens (provider, refresh_token, expires_at) VALUES ('rexven', %s, %s)"
            " ON CONFLICT (provider) DO UPDATE SET refresh_token = EXCLUDED.refresh_token,"
            " expires_at = EXCLUDED.expires_at, updated_at = now()",
            (data["refreshToken"], data.get("refreshTokenExpiresAt")),
        )
    return data


def access_token(client: httpx.Client) -> str:
    if _access["token"] and _access["until"] > time.monotonic():
        return str(_access["token"])

    def exchange(refresh: str) -> dict[str, Any]:
        data = _request(client, "POST", "/token", json={"refreshToken": refresh})
        if not isinstance(data, dict) or not data.get("accessToken") or not data.get("refreshToken"):
            raise ProviderError(f"{PROVIDER}: token yanıtı eksik")
        return data

    data = rotate_refresh_token(exchange)
    ttl = float(data.get("accessTokenExpiresIn") or 60)
    _access.update(token=str(data["accessToken"]), until=time.monotonic() + max(ttl - TOKEN_MARGIN, 5))
    return str(data["accessToken"])


def _get(client: httpx.Client, path: str, **params: Any) -> Any:
    """Access token süresi beklenenden önce biterse (401) bir kez yeniler."""
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {access_token(client)}", "Accept": "application/json"}
        try:
            return _request(client, "GET", path, headers=headers, params=params or None)
        except ProviderHttpError as exc:
            if exc.status != 401 or attempt:
                raise
            _access["token"] = None
    raise ProviderError(f"{PROVIDER}: yetkisiz")


def fetch(account: FinAccount, since: datetime, client: httpx.Client) -> AdapterResult:
    """Kuveyt Türk hesabı: bakiye hesap listesinden, hareketler 29 günlük parçalarla."""
    suffix = provider_id(account)
    data = _get(client, "/kuveyt-turk/accounts")
    rows = ((data.get("value") or {}).get("accountList") if isinstance(data, dict) else None) or []
    match = next((a for a in rows if str(a.get("suffix")) == suffix), None)
    if match is None or match.get("balance") is None:
        raise ProviderError(f"{PROVIDER}: Kuveyt Türk hesabı bulunamadı (ek {suffix})")

    today = datetime.now(since.tzinfo).date()
    txs = []
    for begin, end in windows(since.date(), today):
        page = _get(
            client,
            f"/kuveyt-turk/accounts/{suffix}/transactions",
            startDate=begin.isoformat(),
            endDate=end.isoformat(),
        )
        value = page.get("value") if isinstance(page, dict) else None
        txs += activities(value.get("accountActivities") if isinstance(value, dict) else None)
    return AdapterResult(balance=num(match["balance"]), transactions=txs)


@dataclass(frozen=True)
class WalletTotal:
    company_id: str
    kind: str  # seller | producer
    currency: str
    balance: Decimal
    count: int  # bakiyesi sıfır olmayan cüzdan


def wallet_totals(client: httpx.Client) -> tuple[list[WalletTotal], int]:
    """Tüm platform cüzdanları şirket / tür / para birimine göre toplanır.

    Her bileşim sıfır olsa da döner (bir önceki günün bakiyesi ezilsin).
    İkinci değer: tanınmayan tür / para birimindeki cüzdan sayısı.
    """
    sums = {(c, k, cur): [Decimal("0"), 0] for cur, c in WALLET_COMPANY.items() for k in WALLET_KIND.values()}
    skipped = 0
    for page in range(1, MAX_PAGES + 1):
        data = _get(client, "/wallets", page=page, limit=PAGE_LIMIT)
        wallets = data.get("wallets") if isinstance(data, dict) else None
        if not isinstance(wallets, list):
            raise ProviderError(f"{PROVIDER}: cüzdan listesi okunamadı")
        for w in wallets:
            currency = str(w.get("currency") or "").upper()
            key = (WALLET_COMPANY.get(currency), WALLET_KIND.get(str(w.get("type"))), currency)
            balance = num(w.get("balance"))
            if key not in sums:
                skipped += balance != 0
                continue
            sums[key][0] += balance
            sums[key][1] += balance != 0
        total = int((data.get("pagination") or {}).get("total") or 0)
        if not wallets or page * PAGE_LIMIT >= total:
            break
    else:
        raise ProviderError(f"{PROVIDER}: cüzdan sayfası sınırı aşıldı")
    totals = [WalletTotal(c, k, cur, bal, n) for (c, k, cur), (bal, n) in sums.items()]
    return totals, skipped
