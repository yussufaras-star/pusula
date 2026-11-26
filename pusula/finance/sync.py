"""Finans senkronu: ECB kurları + API hesaplarının bakiye ve hareketleri.

Tetikler: paneldeki buton (POST /api/finance/sync, dakikada en fazla bir),
cron ucu (GET /api/cron/finance-sync) ve GitHub Actions (günde 2 kez,
scripts/finance_sync.py). Eşzamanlı çalıştırmalar advisory lock ile tekildir.
Rexven hesapları ve platform cüzdanları yalnız NetBird'e katılan GitHub
senkronunda (REXVEN_NETBIRD=1) güncellenir.

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
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from pusula.db.models import FinAccount, FinCompany, FinReceivable
from pusula.finance import alerts, discover, fx, store
from pusula.finance.providers import ADAPTERS, rexven
from pusula.finance.providers.base import HTTP_TIMEOUT, AdapterResult, ProviderError
from pusula.metrics import TZ
from pusula.panel_data import connect

logger = logging.getLogger(__name__)

LOCK_KEY = 0x46494E53  # 'FINS'
DEFAULT_BUDGET_SEC = 40.0
# İlk senkronda geriye bakış; sonrakilerde son senkrondan bu kadar önce
# (geç kesinleşen hareketler için örtüşme; çift kaydı ON CONFLICT önler).
FIRST_DAYS = 90
MAX_DAYS = 730  # geçmiş doldurma: 2 yıl
OVERLAP = timedelta(days=7)
FX_BACKFILL_DAYS = 400
# GitHub Actions'ta toJSON(secrets) içinden yalnız bu adlar ortama alınır.
PROVIDER_SECRET_RE = re.compile(
    r"^(MERCURY_API_TOKEN|WISE_API_TOKEN|STRIPE_RESTRICTED_KEY|KUVEYT_TURK_[A-Z_]+|GARANTI_[A-Z_]+"
    r"|(MERCURY_TOKEN|WISE_TOKEN|STRIPE_KEY|REVOLUT_(CLIENT_ID|PRIVATE_KEY|REFRESH_TOKEN|ISS))_[A-Z0-9_]+)$"
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


def sync_fx(now: datetime, days: int | None = None) -> dict[str, Any]:
    """Son kayıtlı günden bugüne ECB kurları; tablo boşsa 400 gün geriye.

    days verilirse (geçmiş doldurma) kurların başı da o güne kadar uzatılır.
    """
    today = now.date()
    with connect() as conn:
        first, last = store.fx_date_range(conn)
    ranges = []
    if days is not None and first is not None:
        need = today - timedelta(days=min(days, MAX_DAYS))
        if need < first:
            ranges.append((need, first - timedelta(days=1)))
    if last is None or last < today:
        ranges.append(((last + timedelta(days=1)) if last else today - timedelta(days=FX_BACKFILL_DAYS), today))
    count = 0
    for start, end in ranges:
        try:
            rates = fx.fetch_ecb_rates(start, end)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"Kur alınamadı ({type(exc).__name__})"}
        with connect() as conn:
            count += fx.save_rates(conn, rates)
    return {"ok": True, "rows": count}


def sync_wallets(
    http: httpx.Client, now: datetime, suppliers: list[rexven.SupplierWallet] | None = None
) -> dict[str, Any]:
    """Rexven platform cüzdan toplamları → fin_wallets (günde bir satır)."""
    try:
        totals, skipped = rexven.wallet_totals(http, suppliers)
        with connect() as conn:
            rows = store.save_platform_wallets(conn, totals, now.date())
    except Exception as exc:
        logger.warning("cuzdan senkronu basarisiz (%s)", type(exc).__name__)
        return {"ok": False, "error": short_error(exc)}
    return {"ok": True, "rows": rows, "skipped": skipped}


def sync_payouts(http: httpx.Client, now: datetime, suppliers: Sequence[rexven.SupplierWallet]) -> dict[str, Any]:
    """Üretici cüzdanlarından yapılan ödemeler → fin_producer_payouts.

    Her cüzdan son taramasından (yoksa PRODUCER_LOOKBACK gün öncesinden) bir
    gün geriye örtüşerek taranır. Bakiyesi sıfırlanan ama önceden taranan
    cüzdanlar da taranır: yoksa en eski tarama tarihi takılı kalır.
    """
    with connect() as conn:
        alerts.ensure_schema(conn)
        starts = alerts.scan_starts(conn)
    wallets = {w.wallet_id: w for w in suppliers}
    for wid in starts.keys() - wallets.keys():
        wallets[wid] = rexven.SupplierWallet(wid, 0, "", "", Decimal("0"))
    first = now.date() - timedelta(days=alerts.PRODUCER_LOOKBACK + alerts.PAYOUT_BEFORE)
    added = errors = 0
    for wallet in wallets.values():
        start = starts[wallet.wallet_id].date() - timedelta(days=1) if wallet.wallet_id in starts else first
        try:
            payouts = rexven.supplier_payouts(http, wallet.wallet_id, start, now.date() + timedelta(days=1))
            with connect() as conn:
                if not wallet.currency:  # bakiyesi sıfırlanmış: kayıtlı ad / para birimi kalır
                    row = conn.execute("SELECT producer, currency FROM fin_payout_scans WHERE wallet_id = %s", (wallet.wallet_id,)).fetchone()
                    wallet = rexven.SupplierWallet(wallet.wallet_id, 0, row[0], row[1], Decimal("0"))
                added += alerts.save_payouts(conn, wallet, payouts, now)
        except Exception as exc:
            errors += 1
            logger.warning("uretici odeme taramasi basarisiz (%s)", type(exc).__name__)
    return {"ok": errors == 0, "wallets": len(wallets), "added": added, **({"errors": errors} if errors else {})}


def refresh_alerts(now: datetime, send: Callable[[str, str], Any] | None = None) -> dict[str, Any]:
    """Uyarıları yeniler ve bildirilmemişleri Cliq'e kişisel mesajla gönderir."""
    since: date = now.date() - timedelta(days=alerts.HISTORY_DAYS + alerts.LOOKBACK_DAYS)
    with connect() as conn:
        txs = store.load_transactions(conn, since)
        accounts = store.load_accounts(conn)
        kinds = {c.id: c.kind for c in store.load_categories(conn)}
        rates = fx.load_fx(conn, since)
        report: dict[str, Any] = alerts.refresh(conn, txs, accounts, kinds, rates, now.date())
    if send is None:
        from pusula.block_share import post_cliq

        send = post_cliq
    try:
        with connect() as conn:
            report["notified"] = alerts.notify(conn, now, send)
    except Exception as exc:
        logger.warning("uyari bildirimi basarisiz (%s)", type(exc).__name__)
        report["notify_error"] = short_error(exc)
    return report


def sync_receivables(http: httpx.Client, now: datetime) -> dict[str, Any]:
    """iyzico / Lidio'da bekleyen net tahsilat (AŞ, TL) → fin_receivables (günde bir satır)."""
    today = now.date()
    out: dict[str, Any] = {"ok": True}
    rows = []
    for source, pending in (("lidio", rexven.lidio_pending), ("iyzico", rexven.iyzico_pending)):
        try:
            amount = pending(http, today)
        except Exception as exc:
            logger.warning("%s bekleyen tahsilat basarisiz (%s)", source, type(exc).__name__)
            out = {**out, "ok": False, source: short_error(exc)}
            continue
        rows.append(FinReceivable(company_id="tr", source=source, as_of=today, amount=amount.quantize(Decimal("0.01")), notes="otomatik"))
        out[source] = str(amount.quantize(Decimal("0.01")))
    with connect() as conn:
        store.save_receivables(conn, rows)
    return out


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
    provider: str | None = None,
) -> dict[str, Any]:
    """min_gap_sec: son çalıştırmadan bu kadar geçmediyse hiçbir şey yapmaz (buton).

    provider: yalnız bu sağlayıcının hesapları (ör. 'rexven': NetBird'e bağlı
    yerel bilgisayardan; diğer sağlayıcıların anahtarları orada yok).
    """
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
            fx_report = sync_fx(now, days)
        except Exception as exc:
            logger.warning("kur senkronu basarisiz (%s)", type(exc).__name__)
            fx_report = {"ok": False, "error": short_error(exc)}

        discovered = None
        if account_id is None and provider is None:
            # Wise'ta açılan yeni bakiye / kumbara, Mercury'de yeni hesap
            try:
                with connect() as conn, httpx.Client(timeout=HTTP_TIMEOUT) as http:
                    discovered = discover.discover_accounts(conn, http)
            except Exception as exc:
                logger.warning("hesap kesfi basarisiz (%s)", type(exc).__name__)
                discovered = {"added": [], "errors": {"kesif": short_error(exc)}}

        with connect() as conn:
            accounts = store.load_sync_accounts(conn, account_id)
            companies = store.load_companies(conn)
        # Rexven yalnız NetBird içinden erişilir (GitHub senkronu); başka yerde atlanır
        if not rexven.reachable():
            accounts = [a for a in accounts if a.provider != "rexven"]
        if provider is not None:
            accounts = [a for a in accounts if a.provider == provider]
        with httpx.Client(timeout=HTTP_TIMEOUT) as http:
            results = run_accounts(
                accounts,
                lambda account: _sync_account(account, companies, http, now, days),
                budget_sec,
                started,
            )
            platform = rexven.reachable() and account_id is None and provider in (None, "rexven")
            suppliers: list[rexven.SupplierWallet] = []
            wallets_report = sync_wallets(http, now, suppliers) if platform else None
            receivables_report = sync_receivables(http, now) if platform else None
            payouts_report = sync_payouts(http, now, suppliers) if platform and wallets_report and wallets_report["ok"] else None
        own = categorized = 0
        try:
            # Yeni gelenler eski hareketlerle de eşleşebilir: ilk senkron penceresi + pay
            since = now.date() - timedelta(days=(days or FIRST_DAYS)) - OVERLAP
            with connect() as conn:
                store.fill_counterparties(conn)
                own = store.apply_own_transfers(conn, since)
                # Önce hesap arası aktarımlar, sonra satıcı cüzdan yüklemeleri
                # ayrılır; kalanlar kurallarla kategorilenir
                categorized = store.apply_wallet_deposits(conn, since)
                categorized += store.apply_category_rules(conn, since)
        except Exception as exc:
            logger.warning("hesap arasi / kategori kurali basarisiz (%s)", type(exc).__name__)
        alerts_report = None
        if account_id is None:
            try:
                alerts_report = refresh_alerts(now)
            except Exception as exc:
                logger.warning("uyari taramasi basarisiz (%s)", type(exc).__name__)
                alerts_report = {"error": short_error(exc)}
        with connect() as conn:
            store.stamp_sync_run(conn, now)
    return {
        "at": now.isoformat(),
        "seconds": round(time.monotonic() - started, 1),
        "fx": fx_report,
        "accounts": results,
        "own_transfers": own,
        "categorized": categorized,
        **({"wallets": wallets_report} if wallets_report is not None else {}),
        **({"receivables": receivables_report} if receivables_report is not None else {}),
        **({"payouts": payouts_report} if payouts_report is not None else {}),
        **({"alerts": alerts_report} if alerts_report is not None else {}),
        **({"discovered": discovered} if discovered else {}),
    }
