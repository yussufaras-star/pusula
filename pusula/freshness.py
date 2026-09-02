"""Veri tazeliği kontrolü ve rapor satırları.

events / leads / deals / contacts için max(created_at) ve yaş (gün).
events_call için ayrıca max(occurred_at) — created_at tek başına
Call_Start boşluğunu gizleyebilir.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Istanbul")

# Panel tazelik eşikleri — tek sözlük.
FRESHNESS_THRESHOLDS: dict[str, timedelta] = {
    "aramalar": timedelta(hours=3),
    "randevular": timedelta(hours=36),
    "leadler": timedelta(days=3),
    "kisiler": timedelta(days=5),
}

_MESAI_START = time(9, 0)
_MESAI_END = time(19, 0)


def is_mesai(now: datetime | None = None) -> bool:
    """Pazartesi–Cuma 09:00–18:59 Istanbul. Hafta sonu ve gece False."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    if local.weekday() >= 5:
        return False
    clock = local.time()
    return _MESAI_START <= clock < _MESAI_END


# Geçmiş analiz / günlük sayım: ileri tarihli occurred_at hariç.
# planlanmis_arama bu filtreyi KULLANMAZ.
NOT_FUTURE_OCCURRED = "occurred_at <= now()"


def _age_days(mx: datetime | None) -> float | None:
    if mx is None:
        return None
    if mx.tzinfo is None:
        mx = mx.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - mx.astimezone(timezone.utc)).total_seconds() / 86400.0


def freshness_rows(
    conn: Any, org_id: str
) -> list[tuple[str, str, datetime | None, float | None]]:
    """(etiket, alan, max_ts, age_days)."""
    specs = (
        ("events", "created_at", "SELECT max(created_at) FROM events WHERE org_id = %s"),
        (
            "events_call",
            "created_at",
            "SELECT max(created_at) FROM events WHERE org_id = %s AND channel = 'call'",
        ),
        (
            "events_call",
            "occurred_at",
            "SELECT max(occurred_at) FROM events WHERE org_id = %s AND channel = 'call'",
        ),
        ("leads", "created_at", "SELECT max(created_at) FROM leads WHERE org_id = %s"),
        ("deals", "created_at", "SELECT max(created_at) FROM deals WHERE org_id = %s"),
        (
            "contacts",
            "created_at",
            "SELECT max(created_at) FROM contacts WHERE org_id = %s",
        ),
    )
    out: list[tuple[str, str, datetime | None, float | None]] = []
    for name, field, sql in specs:
        row = conn.execute(sql, (org_id,)).fetchone()
        mx = row[0] if row else None
        out.append((name, field, mx, _age_days(mx)))
    return out


def print_freshness(conn: Any, org_id: str) -> None:
    print("--- tazelik ---")
    for name, field, mx, age in freshness_rows(conn, org_id):
        if mx is None:
            print(f"  {name}: max({field})=- (bos)")
        else:
            print(
                f"  {name}: max({field})={mx.isoformat(timespec='seconds')} "
                f"yas={age:.1f}g"
            )


def call_events_stale_days(
    conn: Any, org_id: str, *, max_age_hours: float = 48.0
) -> float | None:
    """events(channel=call) max(created_at) yaşı (gün).

    Stale ise age_days, değilse None. Hiç veri yoksa 999.0.
    """
    row = conn.execute(
        """
        SELECT max(created_at) FROM events
        WHERE org_id = %s AND channel = 'call'
        """,
        (org_id,),
    ).fetchone()
    if not row or row[0] is None:
        return 999.0  # hiç veri yok → uyarı
    age = _age_days(row[0])
    assert age is not None
    if age * 24.0 > max_age_hours:
        return age
    return None


def print_call_stale_warning(conn: Any, org_id: str) -> None:
    age = call_events_stale_days(conn, org_id)
    if age is not None:
        days = max(1, int(age + 0.5))
        print(
            f"UYARI: cagri verisi {days} gundur guncellenmedi "
            f"(events.created_at channel=call)"
        )
