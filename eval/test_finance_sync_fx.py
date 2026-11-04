"""Kur senkronu: geçmiş doldurmada kurların başı da uzatılır (ağ / DB yok)."""

from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pusula.finance import sync

NOW = datetime(2026, 11, 4, 12, tzinfo=ZoneInfo("Europe/Istanbul"))


def _run(monkeypatch, first, last, days=None):
    calls = []

    @contextmanager
    def connect():
        yield object()

    monkeypatch.setattr(sync, "connect", connect)
    monkeypatch.setattr(sync.store, "fx_date_range", lambda conn: (first, last))
    monkeypatch.setattr(sync.fx, "fetch_ecb_rates", lambda start, end: calls.append((start, end)) or [1, 2])
    monkeypatch.setattr(sync.fx, "save_rates", lambda conn, rates: len(rates))
    return sync.sync_fx(NOW, days), calls


def test_daily_only_after_last(monkeypatch) -> None:
    report, calls = _run(monkeypatch, date(2025, 9, 11), date(2026, 11, 3))
    assert calls == [(date(2026, 11, 4), date(2026, 11, 4))] and report == {"ok": True, "rows": 2}


def test_backfill_extends_start(monkeypatch) -> None:
    _, calls = _run(monkeypatch, date(2025, 9, 11), date(2026, 11, 4), days=672)
    assert calls == [(date(2025, 1, 1), date(2025, 9, 10))]


def test_backfill_capped_and_noop_when_covered(monkeypatch) -> None:
    _, calls = _run(monkeypatch, date(2025, 9, 11), date(2026, 11, 4), days=5000)
    assert calls == [(date(2024, 11, 4), date(2025, 9, 10))]
    _, calls = _run(monkeypatch, date(2025, 9, 11), date(2026, 11, 4), days=30)
    assert calls == []
