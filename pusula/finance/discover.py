"""Yeni bakiye / hesap keşfi: Wise'ta açılan para birimi bakiyeleri ve
kumbaralar, Mercury'de açılan hesaplar fin_accounts'a otomatik eklenir.

Var olan bir hesabın anahtarı (credential_key) ve şirketi kullanılır; bu
yüzden her şirket için en az bir hesap elle tanımlanmış olmalı. Pasife
alınmış hesap yeniden eklenmez (tüm hesaplarla karşılaştırılır). Depo herkese
açık olduğundan günlükte tutar değil yalnız eklenen hesap adı yazılır.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx
import psycopg

from pusula.db.models import FinAccount
from pusula.finance import store
from pusula.finance.providers import mercury, wise
from pusula.finance.providers.base import ProviderError, provider_id, provider_secret, request_json

logger = logging.getLogger(__name__)

WISE_TYPES = "STANDARD,SAVINGS"


def _groups(accounts: Sequence[FinAccount], provider: str) -> dict[tuple[str | None, str], FinAccount]:
    """(anahtar, şirket) başına örnek hesap; keşif o anahtarla yapılır."""
    out: dict[tuple[str | None, str], FinAccount] = {}
    for a in accounts:
        if a.provider == provider:
            out.setdefault((a.credential_key, a.company_id), a)
    return out


def wise_new(accounts: Sequence[FinAccount], client: httpx.Client) -> list[dict[str, object]]:
    known = {provider_id(a) for a in accounts if a.provider == "wise" and a.provider_account_id}
    new: list[dict[str, object]] = []
    for sample in _groups(accounts, "wise").values():
        token = provider_secret(sample, "WISE_TOKEN", "WISE_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        profile_id = provider_id(sample).partition(":")[0]
        balances = request_json(
            client, "Wise", "GET", f"{wise.BASE}/v4/profiles/{profile_id}/balances", headers=headers, params={"types": WISE_TYPES}
        )
        for b in balances or []:
            key = f"{profile_id}:{b.get('id')}"
            currency = str((b.get("amount") or {}).get("currency") or b.get("currency") or "")
            if key in known or len(currency) != 3:
                continue
            jar = b.get("type") == "SAVINGS"
            name = f"Wise {currency}" + (f" kumbara · {b.get('name')}" if jar and b.get("name") else " kumbara" if jar else "")
            new.append(_row(sample, name, "Wise", "fintech", currency, "wise", key))
            known.add(key)
    return new


def mercury_new(accounts: Sequence[FinAccount], client: httpx.Client) -> list[dict[str, object]]:
    known = {provider_id(a) for a in accounts if a.provider == "mercury" and a.provider_account_id}
    new: list[dict[str, object]] = []
    for sample in _groups(accounts, "mercury").values():
        token = provider_secret(sample, "MERCURY_TOKEN", "MERCURY_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        data = request_json(client, "Mercury", "GET", f"{mercury.BASE}/accounts", headers=headers)
        for a in data.get("accounts") or []:
            key = str(a.get("id") or "")
            if not key or key in known or str(a.get("status", "active")).lower() != "active":
                continue
            digits = str(a.get("accountNumber") or "")[-4:]
            name = f"Mercury ••{digits}" if digits else f"Mercury {a.get('name') or key[:8]}"
            new.append(_row(sample, name, "Mercury", "fintech", "USD", "mercury", key))
            known.add(key)
    return new


def _row(sample: FinAccount, name: str, institution: str, kind: str, currency: str, provider: str, key: str) -> dict[str, object]:
    return {
        "company_id": sample.company_id,
        "name": name,
        "institution": institution,
        "type": kind,
        "currency": currency,
        "provider": provider,
        "provider_account_id": key,
        "credential_key": sample.credential_key,
        "sort": sample.sort,
    }


def discover_accounts(conn: psycopg.Connection, client: httpx.Client) -> dict[str, object]:
    """Eksik Wise bakiyelerini ve Mercury hesaplarını ekler. Anahtar yoksa (yerel
    çalışma) o sağlayıcı sessizce atlanır; diğer hatalar rapora yazılır."""
    accounts = store.load_accounts(conn)
    added: list[str] = []
    errors: dict[str, str] = {}
    for name, find in (("wise", wise_new), ("mercury", mercury_new)):
        try:
            rows = find(accounts, client)
        except ProviderError as exc:
            if "Eksik ortam değişkeni" not in str(exc):
                errors[name] = str(exc)[:120]
            continue
        for row in rows:
            store.insert_row(conn, "fin_accounts", row)
            added.append(str(row["name"]))
    if added:
        logger.info("yeni hesap eklendi: %s", ", ".join(added))
    return {"added": added, **({"errors": errors} if errors else {})}
