"""Banka adaptörleri: sahte HTTP yanıtlarıyla (ağ yok)."""

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from pusula.db.models import FinAccount
from pusula.finance.providers import ADAPTERS
from pusula.finance.providers import mercury as mercury_mod
from pusula.finance.providers import revolut as revolut_mod
from pusula.finance.providers.base import ProviderError, local_day
from pusula.finance.providers.wise import SCA_WARNING

mercury = ADAPTERS["mercury"]
wise = ADAPTERS["wise"]
stripe = ADAPTERS["stripe"]
revolut = ADAPTERS["revolut"]

TZ = ZoneInfo("Europe/Istanbul")
SINCE = datetime(2026, 9, 1, tzinfo=TZ)
D = Decimal


def _account(provider: str, **kw) -> FinAccount:
    base = dict(
        id=1,
        company_id="llc",
        name="Hesap",
        institution=provider,
        currency="USD",
        provider=provider,
        credential_key="LLC",
        provider_account_id="acc-1",
    )
    base.update(kw)
    return FinAccount(**base)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_local_day_uses_istanbul() -> None:
    assert local_day("2026-10-15T22:30:00Z") == date(2026, 10, 16)
    assert local_day("2026-10-15") == date(2026, 10, 15)
    assert local_day(1_792_000_000) == datetime.fromtimestamp(1_792_000_000, TZ).date()


def test_missing_token_names_variable_only(monkeypatch) -> None:
    monkeypatch.delenv("MERCURY_TOKEN_LLC", raising=False)
    monkeypatch.delenv("MERCURY_API_TOKEN", raising=False)
    with pytest.raises(ProviderError, match="MERCURY_API_TOKEN"):
        mercury(_account("mercury"), SINCE, _client(lambda r: httpx.Response(500)))


@pytest.mark.parametrize(
    ("provider", "global_name", "scoped_name"),
    [
        ("mercury", "MERCURY_API_TOKEN", "MERCURY_TOKEN_LLC"),
        ("wise", "WISE_API_TOKEN", "WISE_TOKEN_LLC"),
        ("stripe", "STRIPE_RESTRICTED_KEY", "STRIPE_KEY_LLC"),
    ],
)
def test_keys_come_from_env_global_or_company_scoped(monkeypatch, provider, global_name, scoped_name) -> None:
    from pusula.finance.providers.base import provider_secret

    account = _account(provider)
    monkeypatch.delenv(scoped_name, raising=False)
    monkeypatch.setenv(global_name, "ortak")
    assert provider_secret(account, scoped_name.rsplit("_", 1)[0], global_name) == "ortak"
    monkeypatch.setenv(scoped_name, "sirkete-ozel")
    assert provider_secret(account, scoped_name.rsplit("_", 1)[0], global_name) == "sirkete-ozel"


def test_no_key_literals_in_provider_code() -> None:
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[1] / "pusula" / "finance" / "providers"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"secret-token:mercury|sk_live_|rk_live_|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text), path.name


def test_mercury_balance_pages_and_skips_pending(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOKEN_LLC", "tok-secret")
    monkeypatch.setattr(mercury_mod, "PAGE", 2)
    pages = {
        "0": [
            {"id": "t1", "amount": -12.5, "status": "sent", "postedAt": "2026-10-15T22:30:00Z", "bankDescription": "AWS"},
            {"id": "t2", "amount": 99, "status": "pending", "postedAt": None},
        ],
        "2": [{"id": "t3", "amount": 1000, "status": "sent", "postedAt": "2026-10-01T10:00:00Z", "counterpartyName": "Rexven GmbH"}],
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok-secret"
        if request.url.path.endswith("/transactions"):
            seen.append(request.url.params["offset"])
            assert request.url.params["start"] == "2026-09-01"
            return httpx.Response(200, json={"transactions": pages[request.url.params["offset"]]})
        return httpx.Response(200, json={"currentBalance": 5432.1, "availableBalance": 1})

    result = mercury(_account("mercury"), SINCE, _client(handler))
    assert result.balance == D("5432.1")
    assert seen == ["0", "2"]
    assert [(t.external_id, t.booked_at, t.amount, t.description) for t in result.transactions] == [
        ("t1", date(2026, 10, 16), D("-12.5"), "AWS"),
        ("t3", date(2026, 10, 1), D("1000"), "Rexven GmbH"),
    ]
    assert result.transactions[1].counterparty == "Rexven GmbH"


def test_mercury_credit_card_balance_from_credit_list(monkeypatch) -> None:
    """IO kartı: GET /account/{id} yok (404); bakiye /credit listesinden, hareketler aynı uçtan."""
    monkeypatch.setenv("MERCURY_TOKEN_LLC", "tok-secret")
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/credit"):
            return httpx.Response(200, json={"accounts": [
                {"id": "old", "status": "closed", "currentBalance": -1},
                {"id": "cr-1", "status": "active", "currentBalance": -22968.62, "availableBalance": -22968.62},
            ]})
        if request.url.path.endswith("/account/cr-1/transactions"):
            return httpx.Response(200, json={"transactions": [
                {"id": "c1", "kind": "creditCardTransaction", "amount": -50.0, "status": "sent",
                 "postedAt": "2026-10-19T03:26:11Z", "counterpartyName": "RunPod", "bankDescription": "RUNPOD.IO"},
            ]})
        return httpx.Response(404)

    card = _account("mercury", type="card", provider_account_id="credit")
    result = mercury(card, SINCE, _client(handler))
    assert result.balance == D("-22968.62")
    assert [(t.amount, t.counterparty, t.description) for t in result.transactions] == [(D("-50.0"), "RunPod", "RUNPOD.IO")]
    assert not any(p.endswith("/account/cr-1") for p in paths)

    with pytest.raises(ProviderError, match="kredi hesabı bulunamadı"):
        mercury(_account("mercury", type="card", provider_account_id="nope"), SINCE, _client(handler))


def test_http_error_message_is_short_and_has_no_token(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOKEN_LLC", "tok-secret")
    handler = lambda r: httpx.Response(401, json={"errors": {"message": "bad"}, "message": "Unauthorized token"})
    with pytest.raises(ProviderError) as info:
        mercury(_account("mercury"), SINCE, _client(handler))
    assert str(info.value) == "Mercury: HTTP 401 – Unauthorized token"
    assert "tok-secret" not in str(info.value)


def _wise_handler(statement: httpx.Response):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/balances" in request.url.path:
            return httpx.Response(200, json=[{"id": 77, "currency": "EUR", "amount": {"value": 1500.25}}])
        return statement

    return handler


def test_wise_statement(monkeypatch) -> None:
    monkeypatch.setenv("WISE_TOKEN_GMBH", "w")
    statement = httpx.Response(
        200,
        json={
            "transactions": [
                {
                    "referenceNumber": "TRANSFER-1",
                    "date": "2026-10-02T09:00:00Z",
                    "amount": {"value": -200, "currency": "EUR"},
                    "details": {"type": "TRANSFER", "description": "Sent money to Rexven LLC", "recipient": {"name": "Rexven LLC"}},
                },
                {"referenceNumber": None, "date": "2026-10-02", "amount": {"value": 1}},
            ]
        },
    )
    account = _account("wise", company_id="gmbh", currency="EUR", credential_key="GMBH", provider_account_id="12:77")
    result = wise(account, SINCE, _client(_wise_handler(statement)))
    assert result.balance == D("1500.25") and result.warning is None
    [tx] = result.transactions
    assert (tx.external_id, tx.amount, tx.counterparty, tx.currency) == ("TRANSFER-1", D("-200"), "Rexven LLC", "EUR")


def test_wise_sca_keeps_balance_and_warns(monkeypatch) -> None:
    monkeypatch.setenv("WISE_TOKEN_GMBH", "w")
    account = _account("wise", currency="EUR", credential_key="GMBH", provider_account_id="12:77")
    sca = httpx.Response(403, headers={"x-2fa-approval": "abc"}, json={"error": "sca"})
    result = wise(account, SINCE, _client(_wise_handler(sca)))
    assert result.balance == D("1500.25")
    assert result.transactions == [] and result.warning == SCA_WARNING
    with pytest.raises(ProviderError, match="HTTP 403"):
        wise(account, SINCE, _client(_wise_handler(httpx.Response(403, json={"error": "nope"}))))


def test_wise_bad_account_id(monkeypatch) -> None:
    monkeypatch.setenv("WISE_TOKEN_LLC", "w")
    with pytest.raises(ProviderError, match="profileId"):
        wise(_account("wise", provider_account_id="12"), SINCE, _client(lambda r: httpx.Response(200)))


def test_stripe_minor_units_pagination_and_currency(monkeypatch) -> None:
    monkeypatch.setenv("STRIPE_KEY_LLC", "rk")
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/balance":
            return httpx.Response(
                200,
                json={
                    "available": [{"amount": 150000, "currency": "usd"}, {"amount": 900, "currency": "eur"}],
                    "pending": [{"amount": 2550, "currency": "usd"}],
                },
            )
        params = dict(request.url.params)
        calls.append(params)
        if "starting_after" not in params:
            return httpx.Response(
                200,
                json={
                    "has_more": True,
                    "data": [
                        {"id": "txn_1", "net": 9710, "currency": "usd", "created": 1_791_000_000, "type": "charge"},
                        {"id": "txn_2", "net": 500, "currency": "eur", "created": 1_791_000_000},
                    ],
                },
            )
        return httpx.Response(
            200,
            json={"has_more": False, "data": [{"id": "txn_3", "net": -150000, "currency": "usd", "created": 1_791_100_000, "description": "Payout"}]},
        )

    result = stripe(_account("stripe"), SINCE, _client(handler))
    assert result.balance == D("1525.5")
    assert [(t.external_id, t.amount, t.description) for t in result.transactions] == [
        ("txn_1", D("97.1"), "charge"),
        ("txn_3", D("-1500"), "Payout"),
    ]
    assert {t.counterparty for t in result.transactions} == {"Stripe"}
    assert calls[0]["created[gte]"] == str(int(SINCE.timestamp()))
    assert calls[1]["starting_after"] == "txn_2"


def test_revolut_legs_and_token_exchange(monkeypatch) -> None:
    for name, value in {
        "REVOLUT_CLIENT_ID_GMBH": "cid",
        "REVOLUT_PRIVATE_KEY_GMBH": "pem",
        "REVOLUT_REFRESH_TOKEN_GMBH": "oa_prod_x",
        "REVOLUT_ISS_GMBH": "finans.rexven.com",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(revolut_mod, "client_assertion", lambda cid, iss, pem: f"jwt:{cid}:{iss}")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/token"):
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "refresh_token" and form["client_assertion"] == "jwt:cid:finans.rexven.com"
            return httpx.Response(200, json={"access_token": "at"})
        assert request.headers["Authorization"] == "Bearer at"
        if request.url.path.endswith("/accounts/acc-1"):
            return httpx.Response(200, json={"balance": 321.5, "currency": "EUR"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": "tx1",
                    "state": "completed",
                    "created_at": "2026-10-05T08:00:00Z",
                    "completed_at": "2026-10-05T08:01:00Z",
                    "legs": [
                        {"leg_id": "l1", "account_id": "acc-1", "amount": -50, "currency": "EUR", "description": "To Rexven TR"},
                        {"leg_id": "l2", "account_id": "other", "amount": 50, "currency": "EUR"},
                    ],
                },
                {"id": "tx2", "state": "pending", "created_at": "2026-10-06T08:00:00Z", "legs": []},
            ],
        )

    account = _account("revolut", company_id="gmbh", currency="EUR", credential_key="GMBH")
    result = revolut(account, SINCE, _client(handler))
    assert result.balance == D("321.5")
    assert [(t.external_id, t.amount, t.description) for t in result.transactions] == [("tx1:l1", D("-50"), "To Rexven TR")]


def test_revolut_client_assertion_is_valid_rs256() -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    token = revolut_mod.client_assertion("cid", "finans.rexven.com", pem, now=1_000)
    head, body, sig = token.split(".")

    def unb64(part: str) -> bytes:
        import base64

        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    assert json.loads(unb64(head)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(unb64(body)) == {"iss": "finans.rexven.com", "sub": "cid", "aud": "https://revolut.com", "exp": 1_300}
    key.public_key().verify(unb64(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    with pytest.raises(ProviderError, match="PKCS8"):
        revolut_mod.client_assertion("cid", "iss", "not a pem")
