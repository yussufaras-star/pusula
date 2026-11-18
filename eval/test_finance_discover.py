"""Hesap keşfi: Wise'ta yeni bakiye / kumbara, Mercury'de yeni hesap otomatik eklenir."""

import httpx

from pusula.db.models import FinAccount
from pusula.finance import discover


def _acc(id, provider, key, company="llc", active=True, cred="LLC"):
    return FinAccount(id=id, company_id=company, name=f"{provider} {id}", institution=provider, currency="USD",
                      provider=provider, provider_account_id=key, credential_key=cred, active=active)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_wise_yeni_bakiye_ve_kumbara(monkeypatch) -> None:
    monkeypatch.setenv("WISE_API_TOKEN", "t")
    accounts = [_acc(9, "wise", "111:1"), _acc(10, "wise", "111:2", active=False)]  # pasif: yeniden eklenmez

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/profiles/111/balances"
        assert request.url.params["types"] == "STANDARD,SAVINGS"
        return httpx.Response(200, json=[
            {"id": 1, "type": "STANDARD", "amount": {"currency": "USD", "value": 1}},
            {"id": 2, "type": "STANDARD", "amount": {"currency": "EUR", "value": 1}},
            {"id": 3, "type": "STANDARD", "amount": {"currency": "GBP", "value": 1}},
            {"id": 4, "type": "SAVINGS", "name": "Vergi", "amount": {"currency": "USD", "value": 1}},
        ])

    rows = discover.wise_new(accounts, _client(handler))
    assert [(r["name"], r["currency"], r["provider_account_id"], r["company_id"], r["credential_key"]) for r in rows] == [
        ("Wise GBP", "GBP", "111:3", "llc", "LLC"),
        ("Wise USD kumbara · Vergi", "USD", "111:4", "llc", "LLC"),
    ]


def test_mercury_yeni_hesap(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_API_TOKEN", "t")
    accounts = [_acc(2, "mercury", "a1"), _acc(15, "mercury", "credit")]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/accounts"
        return httpx.Response(200, json={"accounts": [
            {"id": "a1", "status": "active", "accountNumber": "1234"},
            {"id": "a2", "status": "active", "accountNumber": "99995678"},
            {"id": "a3", "status": "archived", "accountNumber": "1111"},
        ]})

    rows = discover.mercury_new(accounts, _client(handler))
    assert [(r["name"], r["provider_account_id"]) for r in rows] == [("Mercury ••5678", "a2")]


def test_anahtar_yoksa_sessizce_atlanir(monkeypatch) -> None:
    monkeypatch.delenv("WISE_API_TOKEN", raising=False)
    monkeypatch.delenv("WISE_TOKEN_LLC", raising=False)
    monkeypatch.delenv("MERCURY_API_TOKEN", raising=False)
    monkeypatch.delenv("MERCURY_TOKEN_LLC", raising=False)
    monkeypatch.setattr(discover.store, "load_accounts", lambda conn: [_acc(9, "wise", "111:1"), _acc(2, "mercury", "a1")])
    inserted = []
    monkeypatch.setattr(discover.store, "insert_row", lambda conn, table, row: inserted.append(row) or 1)
    assert discover.discover_accounts(object(), _client(lambda r: httpx.Response(500))) == {"added": []}
    assert inserted == []
