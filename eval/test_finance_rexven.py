"""Rexven internal API adaptörü: sahte HTTP yanıtları, token deposu bellekte (ağ / DB yok)."""

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from pusula.db.models import FinAccount
from pusula.finance.providers import rexven
from pusula.finance.providers.base import ProviderError, ProviderHttpError

TZ = ZoneInfo("Europe/Istanbul")
D = Decimal
BASE = "https://rexapi-internal.rexven.com/api/internal-api/v1"


@pytest.fixture
def store(monkeypatch):
    """fin_provider_tokens yerine sözlük; rotate_refresh_token'ın akışını taklit eder."""
    saved = {"token": "rxir_eski"}

    def rotate(exchange):
        candidates = [t for t in dict.fromkeys((saved.get("token"), rexven.os.environ.get("REXVEN_API_REFRESH_TOKEN"))) if t]
        for i, token in enumerate(candidates):
            try:
                data = exchange(token)
                break
            except ProviderHttpError as exc:
                if exc.status not in (400, 401, 403) or i == len(candidates) - 1:
                    raise ProviderError("reddedildi") from exc
        saved["token"] = data["refreshToken"]
        return data

    monkeypatch.setattr(rexven, "rotate_refresh_token", rotate)
    monkeypatch.setattr(rexven, "MIN_GAP", 0)
    monkeypatch.setattr(rexven.time, "sleep", lambda s: None)
    rexven._access.update(token=None, until=0.0)
    return saved


def _client(routes, calls=None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/internal-api/v1")
        if calls is not None:
            calls.append((request.method, path, dict(request.url.params)))
        if path == "/token":
            body = json.loads(request.content)
            n = int(body["refreshToken"].rsplit("_", 1)[-1]) if body["refreshToken"][-1].isdigit() else 0
            return httpx.Response(200, json={"accessToken": f"rxia_{n + 1}", "accessTokenExpiresIn": 60,
                                             "refreshToken": f"rxir_{n + 1}", "refreshTokenExpiresAt": "2026-11-07T00:00:00Z"})
        return routes(request, path)
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE)


def _account(pid: str) -> FinAccount:
    return FinAccount(id=12, company_id="tr", name="KuveytTürk Ana Hesap", institution="KuveytTürk",
                      currency="TRY", provider="rexven", credential_key="REXVEN", provider_account_id=pid)


ACCOUNTS = {"value": {"accountList": [
    {"name": "Rexven Ana Hesap", "suffix": 1, "balance": 940262.84, "fxId": 0},
    {"name": "Cüzdan Hesabı", "suffix": 2, "balance": 452929.96, "fxId": 0},
]}, "success": True}


def test_kuveyt_hesabi_bakiye_ve_hareketler(store):
    calls = []

    def routes(request, path):
        assert request.headers["authorization"].startswith("Bearer rxia_")
        if path == "/kuveyt-turk/accounts":
            return httpx.Response(200, json=ACCOUNTS)
        assert path == "/kuveyt-turk/accounts/1/transactions"
        return httpx.Response(200, json={"value": {"accountActivities": [
            {"suffix": 1, "date": "2026-11-04T09:42:20.21", "amount": 78712.98, "fxCode": "TL",
             "description": ", Gelen Para Transferi, Açıklama= REXVEN Gönderen= LİDİO ÖDEME HİZMETLERİ ANONİM ŞİRKETİ ,",
             "transactionReference": "abc="},
            {"suffix": 1, "date": "2026-11-03T10:00:00", "amount": None, "transactionReference": "bos"},
        ]}})

    result = rexven.fetch(_account("1"), datetime(2026, 10, 1, tzinfo=TZ), _client(routes, calls))
    assert result.balance == D("940262.84")
    assert [t.external_id for t in result.transactions] == ["kt:abc=", "kt:abc="]  # iki pencere, aynı sahte yanıt
    tx = result.transactions[0]
    assert tx.booked_at == date(2026, 11, 4) and tx.amount == D("78712.98") and tx.currency == "TRY"
    windows = [c[2] for c in calls if c[1].endswith("/transactions")]
    assert windows[0] == {"startDate": "2026-10-01", "endDate": "2026-10-29"}
    # access token 60 sn geçerli: tek token isteği, refresh token yenisiyle değişti
    assert [c[1] for c in calls].count("/token") == 1
    assert store["token"] == "rxir_1"


def test_bilinmeyen_ek_hata(store):
    with pytest.raises(ProviderError, match="ek 101"):
        rexven.fetch(_account("101"), datetime(2026, 11, 1, tzinfo=TZ),
                     _client(lambda r, p: httpx.Response(200, json=ACCOUNTS)))


def test_reddedilen_token_ortam_degiskeniyle_yeniden_baslar(store, monkeypatch):
    monkeypatch.setenv("REXVEN_API_REFRESH_TOKEN", "rxir_yeni_5")
    store["token"] = "rxir_bitmis"

    def handler(request):
        path = request.url.path.removeprefix("/api/internal-api/v1")
        if path == "/token":
            if json.loads(request.content)["refreshToken"] == "rxir_bitmis":
                return httpx.Response(401, json={"name": "UNAUTHORIZED", "message": "Unauthorized"})
            return httpx.Response(200, json={"accessToken": "rxia_6", "accessTokenExpiresIn": 60, "refreshToken": "rxir_6"})
        return httpx.Response(200, json=ACCOUNTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert rexven.access_token(client) == "rxia_6"
    assert store["token"] == "rxir_6"


def test_suresi_dolan_access_token_bir_kez_yenilenir(store):
    seen = []

    def routes(request, path):
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(200, json=ACCOUNTS)

    rexven.fetch(_account("2"), datetime.now(TZ), _client(routes))
    assert seen[:2] == ["Bearer rxia_1", "Bearer rxia_2"]


def test_istek_siniri_beklenip_tekrar_denenir(store):
    hits = []

    def routes(request, path):
        hits.append(path)
        if len(hits) == 1:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, json=ACCOUNTS)

    result = rexven.fetch(_account("2"), datetime.now(TZ), _client(routes))
    assert result.balance == D("452929.96")


def test_cuzdan_toplamlari_sayfalanir(store):
    pages = {
        "1": [
            {"type": "supplier", "currency": "USD", "balance": "285769.33"},
            {"type": "seller", "currency": "USD", "balance": "10.47"},
            {"type": "seller", "currency": "TRY", "balance": "0.00"},
        ],
        "2": [
            {"type": "seller", "currency": "TRY", "balance": "-0.40"},
            {"type": "seller", "currency": "EUR", "balance": "5"},
            {"type": "supplier", "currency": "TRY", "balance": "100"},
        ],
    }

    def routes(request, path):
        assert path == "/wallets" and request.url.params["limit"] == "1000"
        page = request.url.params["page"]
        return httpx.Response(200, json={"wallets": pages[page], "pagination": {"page": int(page), "limit": 1000, "total": 1500}})

    totals, skipped = rexven.wallet_totals(_client(routes))
    got = {(t.company_id, t.kind, t.currency): (t.balance, t.count) for t in totals}
    assert got == {
        ("llc", "seller", "USD"): (D("10.47"), 1),
        ("llc", "producer", "USD"): (D("285769.33"), 1),
        ("tr", "seller", "TRY"): (D("-0.40"), 1),
        ("tr", "producer", "TRY"): (D("100"), 1),
    }
    assert skipped == 1


def test_netbird_yoksa_erisilemez(monkeypatch):
    monkeypatch.delenv("REXVEN_NETBIRD", raising=False)
    assert not rexven.reachable()
    monkeypatch.setenv("REXVEN_NETBIRD", "1")
    assert rexven.reachable()
