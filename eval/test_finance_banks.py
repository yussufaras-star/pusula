"""Kuveyt Türk ve Garanti adaptörleri: sahte HTTP yanıtlarıyla (ağ yok)."""

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from pusula.db.models import FinAccount
from pusula.finance.providers import garanti, kuveytturk
from pusula.finance.providers.base import ProviderError

TZ = ZoneInfo("Europe/Istanbul")
D = Decimal


def _key_pair():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return key, pem


def _account(provider: str, pid: str) -> FinAccount:
    return FinAccount(id=1, company_id="tr", name="Hesap", institution=provider, currency="TRY",
                      provider=provider, credential_key="TR", provider_account_id=pid)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Kuveyt Türk -------------------------------------------------------------------


@pytest.fixture
def kt_env(monkeypatch):
    key, pem = _key_pair()
    monkeypatch.setenv("KUVEYT_TURK_IDENTITY_URL", "https://id.example/")
    monkeypatch.setenv("KUVEYT_TURK_GATEWAY_URL", "https://gw.example")
    monkeypatch.setenv("KUVEYT_TURK_CLIENT_ID", "cid")
    monkeypatch.setenv("KUVEYT_TURK_CLIENT_SECRET", "csecret")
    # tek satırlık '\n' biçimi de kabul edilir
    monkeypatch.setenv("KUVEYT_TURK_PRIVATE_KEY", pem.replace("\n", "\\n"))
    kuveytturk._token_cache.clear()
    return key


def _verify(key, signature: str, content: str) -> None:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    key.public_key().verify(base64.b64decode(signature), content.encode(), padding.PKCS1v15(), hashes.SHA256())


def test_kuveyt_windows_split_29_days() -> None:
    parts = kuveytturk.windows(date(2026, 6, 26), date(2026, 8, 31))
    assert parts == [
        (date(2026, 6, 26), date(2026, 7, 24)),
        (date(2026, 7, 25), date(2026, 8, 22)),
        (date(2026, 8, 23), date(2026, 8, 31)),
    ]


def test_kuveyt_signed_requests_balance_and_transactions(kt_env, monkeypatch) -> None:
    key = kt_env
    token_calls: list[dict] = []
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "id.example":
            assert request.url.path == "/connect/token"
            token_calls.append(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        query = ("?" + request.url.query.decode()) if request.url.query else ""
        # imzalanan metin = token + istekteki sorgu metni (birebir)
        _verify(key, request.headers["Signature"], "tok" + query)
        seen.append(request.url.path + query)
        if request.url.path == "/v3/accounts/1":
            return httpx.Response(200, json={"value": {"suffix": 1, "balance": 2124703.23, "fxCode": "TL"}})
        return httpx.Response(200, json={"value": {"accountActivities": [
            {"date": "2026-07-01T10:15:00", "amount": -40000.0, "balance": 2084703.23, "fxCode": "TL",
             "description": "Kira  ödemesi", "transactionReference": "R1"},
            {"date": "2026-07-02T09:00:00", "amount": 10000.0, "fxCode": "TL",
             "description": "REX-139777 - Gönderen:TUĞBA", "transactionReference": "R2"},
            {"date": "2026-07-02", "amount": 5, "transactionReference": None},  # kimliksiz → atlanır
        ]}})

    since = datetime(2026, 6, 26, tzinfo=TZ)
    result = kuveytturk.fetch(_account("kuveytturk", "1"), since, _client(handler))
    assert result.balance == D("2124703.23")
    assert token_calls == [{"grant_type": "client_credentials", "scope": "accounts", "client_id": "cid", "client_secret": "csecret"}]
    assert seen[0] == "/v3/accounts/1"
    assert seen[1] == "/v3/accounts/1/transactions?BeginDate=2026-06-26&EndDate=2026-07-24"
    assert [(t.external_id, t.booked_at, t.amount, t.currency, t.description) for t in result.transactions[:2]] == [
        ("kt:R1", date(2026, 7, 1), D("-40000.0"), "TRY", "Kira ödemesi"),
        ("kt:R2", date(2026, 7, 2), D("10000.0"), "TRY", "REX-139777 - Gönderen:TUĞBA"),
    ]


def test_kuveyt_missing_settings_and_bad_key(monkeypatch) -> None:
    kuveytturk._token_cache.clear()
    for name in ("IDENTITY_URL", "GATEWAY_URL", "CLIENT_ID", "CLIENT_SECRET", "PRIVATE_KEY"):
        monkeypatch.delenv(f"KUVEYT_TURK_{name}", raising=False)
    with pytest.raises(ProviderError, match="Eksik ortam değişkeni: KUVEYT_TURK_"):
        kuveytturk.fetch(_account("kuveytturk", "1"), datetime(2026, 7, 1, tzinfo=TZ), _client(lambda r: httpx.Response(500)))
    pytest.importorskip("cryptography")
    with pytest.raises(ProviderError, match="PEM"):
        kuveytturk.sign("tok", "", "not a key")


# --- Garanti --------------------------------------------------------------------------


@pytest.fixture
def gb_env(monkeypatch):
    for name, value in {
        "GARANTI_API_URL": "https://gb.example",
        "GARANTI_CLIENT_ID": "gid",
        "GARANTI_CLIENT_SECRET": "gsecret",
        "GARANTI_CALLBACK_URL": "https://cb.example/x",
        "GARANTI_CONSENT_ID": "consent-1",
        "GARANTI_UNIT_NUMBER": "123",
        "GARANTI_ACCOUNT_NUMBER": "456",
        "GARANTI_IBAN": "TR00",
    }.items():
        monkeypatch.setenv(name, value)


def test_garanti_balance_pages_and_direction(gb_env, monkeypatch) -> None:
    monkeypatch.setattr(garanti, "PAGE", 2)
    bodies: list[dict] = []

    def row(ref: str, amount: float, ind: str, day: str = "2026-07-01T10:00:00") -> dict:
        return {"activityDate": day, "amount": amount, "txnCreditDebitIndicator": ind, "explanation": f"x {ref}",
                "currencyCode": "TL", "transactionReferenceId": ref, "customerName": "REXVEN TEKNOLOJI A.S."}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/oauth/v2/token":
            params = dict(request.url.params)
            assert params == {"grant_type": "client_credentials", "client_id": "gid", "client_secret": "gsecret",
                              "redirect_uri": "https://cb.example/x"}
            return httpx.Response(200, json={"access_token": "gtok"})
        assert request.headers["Authorization"] == "Bearer gtok"
        body = json.loads(request.content)
        bodies.append(body)
        if request.url.path.endswith("/account/v1/"):
            return httpx.Response(200, json={"data": {"accounts": [{"balances": [
                {"type": "AvailableBalance", "amount": "999"},
                {"type": "Balance", "amount": "15250.75"},
            ]}]}})
        page = int(body["pageIndex"])
        rows = [row("G1", 100, "A"), row("G2", 40, "B")] if page == 1 else [row("G3", 7.5, "A", "2026-07-03")]
        return httpx.Response(200, json={"transactions": rows})

    result = garanti.fetch(_account("garanti", "456"), datetime(2026, 7, 1, tzinfo=TZ), _client(handler))
    assert result.balance == D("15250.75")
    assert bodies[0] == {"consentId": "consent-1", "unitNum": "123", "accountNum": "456", "IBAN": "TR00"}
    first = bodies[1]
    assert first["pageIndex"] == "1" and first["pageSize"] == "2" and first["startDate"] == "2026-07-01T00:00:00.000"
    assert [(t.external_id, t.amount, t.currency, t.counterparty) for t in result.transactions[:3]] == [
        ("gb:G1", D("100"), "TRY", None),
        ("gb:G2", D("-40"), "TRY", None),
        ("gb:G3", D("7.5"), "TRY", None),
    ]


def test_garanti_unknown_indicator_is_error() -> None:
    with pytest.raises(ProviderError, match="borç/alacak"):
        garanti.signed_amount({"amount": 5, "txnCreditDebitIndicator": "X"})
    assert garanti.find_balance({"x": [{"type": "Blocked", "amount": 1}]}) is None
