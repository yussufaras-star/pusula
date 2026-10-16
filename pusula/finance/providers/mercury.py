"""Mercury: bakiye + hareketler (read-only API token, MERCURY_TOKEN_<KEY>).

provider_account_id = Mercury hesap id'si (GET /api/v1/accounts).
Yalnız kesinleşmiş (postedAt dolu) hareketler alınır; bekleyen hareket
iptal olabileceği için yazılmaz. Bakiye currentBalance (kesinleşmiş).
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

import httpx

from pusula.db.models import FinAccount
from pusula.finance.providers.base import (
    AdapterResult,
    NormTx,
    cred_key,
    local_day,
    num,
    provider_id,
    request_json,
    require_env,
)

BASE = "https://api.mercury.com/api/v1"
PAGE = 500
MAX_PAGES = 40
SKIP_STATUSES = {"failed", "cancelled", "pending"}


def fetch(account: FinAccount, since: datetime, client: httpx.Client) -> AdapterResult:
    token = require_env(f"MERCURY_TOKEN_{cred_key(account)}")
    account_id = quote(provider_id(account), safe="")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    info = request_json(client, "Mercury", "GET", f"{BASE}/account/{account_id}", headers=headers)
    balance = num(info.get("currentBalance", info.get("availableBalance")))

    txs: list[NormTx] = []
    for page in range(MAX_PAGES):
        data = request_json(
            client,
            "Mercury",
            "GET",
            f"{BASE}/account/{account_id}/transactions",
            headers=headers,
            params={"start": since.date().isoformat(), "limit": PAGE, "offset": page * PAGE},
        )
        rows = data.get("transactions") or []
        for row in rows:
            if str(row.get("status") or "").lower() in SKIP_STATUSES or not row.get("postedAt"):
                continue
            counterparty = row.get("counterpartyName")
            txs.append(
                NormTx(
                    external_id=str(row["id"]),
                    booked_at=local_day(row["postedAt"]),
                    amount=num(row.get("amount")),  # zaten işaretli
                    description=row.get("bankDescription") or row.get("note") or counterparty or "",
                    counterparty=counterparty,
                )
            )
        if len(rows) < PAGE:
            break
    return AdapterResult(balance=balance, transactions=txs)
