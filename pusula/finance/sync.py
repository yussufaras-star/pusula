"""Finans senkronu: ECB kurları + API hesaplarının bakiye ve hareketleri.

Tetikler: paneldeki buton (POST /api/finance/sync, dakikada en fazla bir),
cron ucu (GET /api/cron/finance-sync) ve GitHub Actions (günde 2 kez,
scripts/finance_sync.py). Eşzamanlı çalıştırmalar advisory lock ile tekildir.

Hesaplar sırayla, her biri kendi işleminde senkronlanır: bir hesabın hatası
sync_error'a kısa mesaj olarak yazılır, diğerlerine devam edilir. Vercel 60 sn
sınırı için süre bütçesi dolunca yeni hesaba başlanmaz; atlananlar bir sonraki
çalıştırmada başa geçer (load_sync_accounts sıralaması).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, MutableMapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from pusula.db.models import FinAccount, FinCompany
from pusula.finance import fx, store
from pusula.finance.providers import ADAPTERS
from pusula.finance.providers.base import HTTP_TIMEOUT, AdapterResult, ProviderError
from pusula.metrics import TZ
from pusula.panel_data import connect

logger = logging.getLogger(__name__)

LOCK_KEY = 0x46494E53  # 'FINS'
DEFAULT_BUDGET_SEC = 40.0
# İlk senkronda geriye bakış; sonrakilerde son senkrondan bu kadar önce
# (geç kesinleşen hareketler için örtüşme; çift kaydı ON CONFLICT önler).
FIRST_DAYS = 90
MAX_DAYS = 400
OVERLAP = timedelta(days=7)
FX_BACKFILL_DAYS = 400
# GitHub Actions'ta toJSON(secrets) içinden yalnız bu adlar ortama alınır.
PROVIDER_SECRET_RE = re.compile(
    r"^(MERCURY_TOKEN|WISE_TOKEN|STRIPE_KEY|REVOLUT_(CLIENT_ID|PRIVATE_KEY|REFRESH_TOKEN|ISS))_[A-Z0-9_]+$"
)


def load_provider_secrets(
    raw: str | None, environ: MutableMapping[str, str] = os.environ
) -> list[str]:
    """JSON secret paketinden sağlayıcı anahtarlarını ortama yazar; adları döner.

    Ortamda zaten tanımlı olan değer ezilmez. Değerler loglanmaz.
    """
    if not raw:
        return []
    try:
        bundle = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(bundle, dict):
        return []
    loaded: list[str] = []
    for name, value in bundle.items():
        if not isinstance(name, str) or not PROVIDER_SECRET_RE.match(name):
            continue
        if not isinstance(value, str) or not value or environ.get(name):
            continue
        environ[name] = value
        loaded.append(name)
    return sorted(loaded)


def account_since(account: FinAccount, now: datetime, days: int | None = None) -> datetime:
    if days is not None:
        return now - timedelta(days=max(1, min(days, MAX_DAYS)))
    if account.last_synced_at is None:
        return now - timedelta(days=FIRST_DAYS)
    return max(account.last_synced_at - OVERLAP, now - timedelta(days=MAX_DAYS))


def prepare_rows(
    account: FinAccount, result: AdapterResult, companies: Sequence[FinCompany]
) -> list[store.NewTx]:
    """Hesap para birimi dışındakini ve sıfır tutarlıyı atar, kimliği tekiller."""
    seen: set[str] = set()
    rows: list[store.NewTx] = []
    for tx in result.transactions:
        if tx.currency and tx.currency.upper() != account.currency:
            continue
        if not tx.external_id or tx.external_id in seen:
            continue
        amount = tx.amount.quantize(Decimal("0.01"))
        if amount == 0:
            continue
        seen.add(tx.external_id)
        rows.append(
            store.classify(
                account,
                store.NewTx(
                    booked_at=tx.booked_at,
                    amount=amount,
                    description=tx.description.strip(),
                    external_id=tx.external_id,
                    counterparty=tx.counterparty,
                ),
                companies,
            )
        )
    return rows


def short_error(exc: BaseException) -> str:
    """Kullanıcıya gösterilecek mesaj. Beklenmeyen hatada yalnız tür adı
    (iç ayrıntı, bağlantı dizesi vb. sızmasın)."""
    if isinstance(exc, ProviderError):
        return " ".join(str(exc).split())[: store.ERROR_MAX]
    return f"Beklenmeyen hata ({type(exc).__name__})"


def run_accounts(
    accounts: Sequence[FinAccount],
    sync_one: Callable[[FinAccount], dict[str, Any]],
    budget_sec: float,
    started: float,
    clock: Callable[[], float] = time.monotonic,
) -> list[dict[str, Any]]:
    """Hesap hesap çalıştırır; hata diğerlerini durdurmaz, bütçe dolunca atlar."""
    results: list[dict[str, Any]] = []
    for account in accounts:
        base = {"account_id": account.id, "name": account.name}
        if clock() - started > budget_sec:
            results.append({**base, "ok": False, "skipped": "sure_doldu"})
            continue
        try:
            results.append({**base, "ok": True, **sync_one(account)})
        except Exception as exc:
            logger.warning("finans senkronu basarisiz: hesap %s (%s)", account.id, type(exc).__name__)
            results.append({**base, "ok": False, "error": short_error(exc)})
    return results


def sync_fx(now: datetime) -> dict[str, Any]:
    """Son kayıtlı günden bugüne ECB kurları; tablo boşsa 400 gün geriye."""
    today = now.date()
    with connect() as conn:
        last = store.last_fx_date(conn)
    if last is not None and last >= today:
        return {"ok": True, "rows": 0}
    start = (last + timedelta(days=1)) if last else today - timedelta(days=FX_BACKFILL_DAYS)
    try:
        rates = fx.fetch_ecb_rates(start, today)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Kur alınamadı ({type(exc).__name__})"}
    with connect() as conn:
        count = fx.save_rates(conn, rates)
    return {"ok": True, "rows": count}


def _sync_account(
    account: FinAccount,
    companies: Sequence[FinCompany],
    http: httpx.Client,
    now: datetime,
    days: int | None,
) -> dict[str, Any]:
    if account.id is None:
        raise ProviderError("Kayıtlı olmayan hesap")
    adapter = ADAPTERS.get(account.provider)
    if adapter is None:
        raise ProviderError(f"Desteklenmeyen sağlayıcı: {account.provider}")
    try:
        result = adapter(account, account_since(account, now, days), http)
        rows = prepare_rows(account, result, companies)
        with connect() as conn:
            inserted = store.insert_transactions(conn, account, rows, "api")
            store.mark_synced(conn, account.id, result.balance.quantize(Decimal("0.01")), result.warning, now)
    except Exception as exc:
        with connect() as conn:
            store.mark_error(conn, account.id, short_error(exc))
        raise
    out: dict[str, Any] = {"inserted": inserted, "fetched": len(rows), "balance": result.balance}
    if result.warning:
        out["warning"] = result.warning
    return out


def _try_lock(conn: Any) -> bool:
    """İşlem düzeyi kilit: transaction pooler'da da güvenli, işlem bitince düşer."""
    row = conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()
    return bool(row and row[0])


def run_finance_sync(
    now: datetime | None = None,
    *,
    account_id: int | None = None,
    days: int | None = None,
    budget_sec: float = DEFAULT_BUDGET_SEC,
    min_gap_sec: float | None = None,
) -> dict[str, Any]:
    """min_gap_sec: son çalıştırmadan bu kadar geçmediyse hiçbir şey yapmaz (buton)."""
    now = now or datetime.now(TZ)
    started = time.monotonic()
    with connect() as lock_conn:
        if not _try_lock(lock_conn):
            return {"skipped": "baska_calisma_suruyor", "at": now.isoformat()}
        if min_gap_sec is not None:
            last = store.last_sync_run(lock_conn)
            if last is not None and (now - last).total_seconds() < min_gap_sec:
                return {"skipped": "yeni_guncellendi", "last_run": last.isoformat()}

        try:
            fx_report = sync_fx(now)
        except Exception as exc:
            logger.warning("kur senkronu basarisiz (%s)", type(exc).__name__)
            fx_report = {"ok": False, "error": short_error(exc)}

        with connect() as conn:
            accounts = store.load_sync_accounts(conn, account_id)
            companies = store.load_companies(conn)
        with httpx.Client(timeout=HTTP_TIMEOUT) as http:
            results = run_accounts(
                accounts,
                lambda account: _sync_account(account, companies, http, now, days),
                budget_sec,
                started,
            )
        with connect() as conn:
            store.stamp_sync_run(conn, now)
    return {
        "at": now.isoformat(),
        "seconds": round(time.monotonic() - started, 1),
        "fx": fx_report,
        "accounts": results,
    }
