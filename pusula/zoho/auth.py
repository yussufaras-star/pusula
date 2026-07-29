"""Zoho OAuth access token yönetimi.

Refresh token ile access token alır ve bellekte cache'ler (55 dakika
TTL). Thread-safe'tir: eş zamanlı çağrılarda tek yenileme yapılır.
Token asla loglanmaz ve exception mesajlarına konmaz.

Gerekli ortam değişkenleri: ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET,
ZOHO_REFRESH_TOKEN, ZOHO_ACCOUNTS_DOMAIN, ZOHO_API_DOMAIN.
"""

import os
import threading
import time

import httpx

# 55 dakika: Zoho token'ları 60 dakika geçerli, 5 dakika pay bırakıyoruz.
_TOKEN_TTL_SECONDS = 55 * 60

_REQUIRED_ENV_VARS = (
    "ZOHO_CLIENT_ID",
    "ZOHO_CLIENT_SECRET",
    "ZOHO_REFRESH_TOKEN",
    "ZOHO_ACCOUNTS_DOMAIN",
    "ZOHO_API_DOMAIN",
)

_lock = threading.Lock()
_cached_token: str | None = None
# monotonic saat: sistem saati değişse bile TTL doğru işler.
_expires_at_monotonic: float = 0.0


class ZohoAuthError(RuntimeError):
    """Access token alınamadığında fırlatılır. Mesajı token içermez."""


def get_api_domain() -> str:
    """API taban adresini döner (örn. https://www.zohoapis.com)."""
    domain = os.environ.get("ZOHO_API_DOMAIN")
    if not domain:
        raise ZohoAuthError("ZOHO_API_DOMAIN ortam değişkeni tanımlı değil")
    return domain.rstrip("/")


def get_access_token(force_refresh: bool = False) -> str:
    """Geçerli bir access token döner.

    Cache'te taze bir token varsa onu kullanır; yoksa (veya
    force_refresh=True ise) refresh token ile yenisini alır. Lock tüm
    fonksiyonu kapsar: eş zamanlı çağrılar tek yenileme yapar, diğerleri
    cache'lenen sonucu alır.
    """
    global _cached_token, _expires_at_monotonic
    with _lock:
        if (
            not force_refresh
            and _cached_token is not None
            and time.monotonic() < _expires_at_monotonic
        ):
            return _cached_token
        token = _refresh_access_token()
        _cached_token = token
        _expires_at_monotonic = time.monotonic() + _TOKEN_TTL_SECONDS
        return token


def _refresh_access_token() -> str:
    """Zoho accounts sunucusundan yeni access token alır.

    Başarısızlıkta ZohoAuthError fırlatır; asla None dönmez. Hata
    mesajlarına token veya client secret konmaz.
    """
    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise ZohoAuthError(f"Eksik ortam değişkenleri: {', '.join(missing)}")

    url = os.environ["ZOHO_ACCOUNTS_DOMAIN"].rstrip("/") + "/oauth/v2/token"
    try:
        response = httpx.post(
            url,
            data={
                "grant_type": "refresh_token",
                "client_id": os.environ["ZOHO_CLIENT_ID"],
                "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
                "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise ZohoAuthError(f"Zoho accounts sunucusuna ulaşılamadı: {exc}") from exc

    if response.status_code != 200:
        # Gövde token içerebileceği için mesaja sadece durum kodu konur.
        raise ZohoAuthError(f"Token yenileme başarısız, HTTP {response.status_code}")

    payload = response.json()
    # Zoho hatalarda da 200 dönebilir; gövdede "error" alanı olur.
    if "error" in payload:
        raise ZohoAuthError(f"Token yenileme reddedildi: {payload['error']}")
    token = payload.get("access_token")
    if not token:
        raise ZohoAuthError("Token yanıtında access_token alanı yok")
    return token
