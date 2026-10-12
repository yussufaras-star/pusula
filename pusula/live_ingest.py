"""Canlı ingest: mesai içinde birkaç dakikada bir, kaldığı yerden.

Her çalıştırmada: aramalar ve WhatsApp (imleçten), CRM işlemleri.
Çeyrek saatte bir: randevular, lead'ler, kişiler (pencereli kaynaklar).
Eşzamanlı iki tetik (GitHub Actions + harici pinger) Postgres advisory
lock ile tekilleşir; ikincisi hiçbir şey yapmadan döner.

Başarılı arama senkronunda sync_state 'live_ingest' damgalanır; panelin
"veri x dk önce" göstergesi ve hareketsizlik ölçüm ucu bunu okur.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.models import SyncState
from pusula.ingest import get
from pusula.ingest.crm_actions import sync_crm_actions
from pusula.ingest.crm_contacts import sync_contacts
from pusula.ingest.lead_identity import sync_leads
from pusula.metrics import TZ
from pusula.panel_data import connect

logger = logging.getLogger(__name__)

LIVE_SOURCE = "live_ingest"
_LOCK_KEY = 0x50555355  # 'PUSU'
# Mesai 09-18 (cumartesi 09-15); öncesi/sonrası yarım saat payı.
_ACTIVE_FROM = (8, 30)
_ACTIVE_UNTIL = {0: (18, 45), 1: (18, 45), 2: (18, 45), 3: (18, 45), 4: (18, 45), 5: (15, 45)}
SLOW_EVERY_MIN = 15
WINDOW_LOOKBACK = timedelta(hours=2)
# Vercel 60 sn sınırı: yeni adıma bu süreden sonra başlanmaz.
DEFAULT_BUDGET_SEC = 40.0
# İmleç yoksa veya çok eskiyse (ör. uzun kesinti) sınırsız geri sarma olmasın.
MAX_CURSOR_AGE = timedelta(days=2)


def is_active(now: datetime) -> bool:
    local = now.astimezone(TZ)
    until = _ACTIVE_UNTIL.get(local.weekday())
    if until is None:
        return False
    clock = (local.hour, local.minute)
    return _ACTIVE_FROM <= clock <= until


def slow_due(now: datetime, last_slow: datetime | None) -> bool:
    """Pencereli kaynaklar çeyrek saatte bir."""
    if last_slow is None:
        return True
    return now - last_slow >= timedelta(minutes=SLOW_EVERY_MIN) - timedelta(seconds=30)


def _cursor_since(source: str, now: datetime) -> datetime | None:
    """İmleç tazeyse None (ingester imleçten okur); yoksa sınırlı pencere."""
    state = client.get_sync_state(source)
    if state and state.last_synced_at and now - state.last_synced_at <= MAX_CURSOR_AGE:
        return None
    return now - WINDOW_LOOKBACK


def _run_cursor_source(source: str, now: datetime) -> dict[str, Any]:
    result = get(source)().run(since=_cursor_since(source, now))
    return {
        "fetched": result.fetched,
        "inserted": result.inserted,
        "duplicated": result.duplicated,
        "failed": result.failed,
    }


def _run_bookings(now: datetime) -> dict[str, Any]:
    ingester = get("zoho_bookings")()
    setattr(ingester, "lookback", WINDOW_LOOKBACK)
    prior = client.get_sync_state("zoho_bookings")
    result = ingester.run(since=now - WINDOW_LOOKBACK)
    if prior is not None:
        # Günlük ingest'in imleci pencereli çalıştırmayla geri sarılmasın.
        client.set_sync_state(prior)
    return {"fetched": result.fetched, "inserted": result.inserted, "failed": result.failed}


def _try_lock(conn: Any) -> bool:
    """İşlem düzeyi kilit: transaction pooler'da da güvenli, işlem bitince düşer."""
    row = conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (_LOCK_KEY,)).fetchone()
    return bool(row and row[0])


def whatsapp_enabled() -> bool:
    """KVKK (ROADMAP): ekibe yazılı aydınlatma yapılana kadar kapalı."""
    return os.environ.get("PUSULA_WHATSAPP_INGEST", "").strip() == "1"


def _steps(now: datetime, run_slow: bool) -> list[tuple[str, Callable[[], Any]]]:
    steps: list[tuple[str, Callable[[], Any]]] = [
        ("aramalar", lambda: _run_cursor_source("zoho_crm_calls", now)),
    ]
    if whatsapp_enabled():
        steps.append(("whatsapp", lambda: _run_cursor_source("zoho_crm_whatsapp", now)))
    steps.append(("crm_islemleri", lambda: sync_crm_actions(now)))
    if run_slow:
        steps += [
            ("randevular", lambda: _run_bookings(now)),
            ("leadler", lambda: sync_leads(since=now - WINDOW_LOOKBACK)),
            ("kisiler", lambda: sync_contacts(since=now - WINDOW_LOOKBACK)),
        ]
    return steps


def _run_steps(
    now: datetime, *, force: bool, budget_sec: float, started: float
) -> dict[str, Any]:
    slow_state = client.get_sync_state(f"{LIVE_SOURCE}:slow")
    run_slow = force or slow_due(now, slow_state.last_synced_at if slow_state else None)
    report: dict[str, Any] = {}
    for name, step in _steps(now, run_slow):
        if time.monotonic() - started > budget_sec:
            report[name] = {"skipped": "sure_doldu"}
            continue
        try:
            report[name] = step()
        except Exception as exc:
            logger.exception("canli ingest adimi basarisiz: %s", name)
            report[name] = {"error": str(exc)[:300]}
    calls = report.get("aramalar", {})
    if isinstance(calls, dict) and calls.get("failed") == 0:
        client.set_sync_state(
            SyncState(source_name=LIVE_SOURCE, last_synced_at=now, last_cursor="ok")
        )
    slow_ok = all(
        isinstance(report.get(s), dict) and "error" not in report[s] and "skipped" not in report[s]
        for s in ("randevular", "leadler", "kisiler")
    )
    if run_slow and slow_ok:
        client.set_sync_state(SyncState(source_name=f"{LIVE_SOURCE}:slow", last_synced_at=now))
    return report


def run_live_ingest(
    now: datetime | None = None,
    *,
    budget_sec: float = DEFAULT_BUDGET_SEC,
    force: bool = False,
) -> dict[str, Any]:
    now = now or datetime.now(TZ)
    if not force and not is_active(now):
        return {"skipped": "mesai_disi", "at": now.isoformat()}
    started = time.monotonic()
    # Kilit havuz dışı ayrı bağlantıda: ingester'lar havuzu sıfırlayabilir.
    with connect() as lock_conn:
        if not _try_lock(lock_conn):
            return {"skipped": "baska_calisma_suruyor", "at": now.isoformat()}
        steps = _run_steps(now, force=force, budget_sec=budget_sec, started=started)
    return {
        "at": now.isoformat(),
        "org_id": get_org_id(),
        "seconds": round(time.monotonic() - started, 1),
        "steps": steps,
    }
