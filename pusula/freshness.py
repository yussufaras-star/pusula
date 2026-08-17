"""Veri tazeliği kontrolü ve rapor satırları.

events / leads / deals / contacts için max(created_at) ve yaş (gün).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Geçmiş analiz / günlük sayım: ileri tarihli occurred_at hariç.
# planlanmis_arama bu filtreyi KULLANMAZ.
NOT_FUTURE_OCCURRED = "occurred_at <= now()"


def freshness_rows(
    conn: Any, org_id: str
) -> list[tuple[str, datetime | None, float | None]]:
    """(tablo, max_created_at, age_days)."""
    specs = (
        ("events", "SELECT max(created_at) FROM events WHERE org_id = %s"),
        ("events_call", "SELECT max(created_at) FROM events WHERE org_id = %s AND channel = 'call'"),
        ("leads", "SELECT max(created_at) FROM leads WHERE org_id = %s"),
        ("deals", "SELECT max(created_at) FROM deals WHERE org_id = %s"),
        ("contacts", "SELECT max(created_at) FROM contacts WHERE org_id = %s"),
    )
    now = datetime.now(timezone.utc)
    out: list[tuple[str, datetime | None, float | None]] = []
    for name, sql in specs:
        row = conn.execute(sql, (org_id,)).fetchone()
        mx = row[0] if row else None
        age: float | None = None
        if mx is not None:
            if mx.tzinfo is None:
                mx = mx.replace(tzinfo=timezone.utc)
            age = (now - mx.astimezone(timezone.utc)).total_seconds() / 86400.0
        out.append((name, mx, age))
    return out


def print_freshness(conn: Any, org_id: str) -> None:
    print("--- tazelik ---")
    for name, mx, age in freshness_rows(conn, org_id):
        if mx is None:
            print(f"  {name}: max(created_at)=- (bos)")
        else:
            print(
                f"  {name}: max(created_at)={mx.isoformat(timespec='seconds')} "
                f"yas={age:.1f}g"
            )


def call_events_stale_days(
    conn: Any, org_id: str, *, max_age_hours: float = 48.0
) -> float | None:
    """events(channel=call) max(created_at) yaşı (gün).

    None = tablo boş. Eşik aşılmamışsa negatif olmayan küçük değer değil,
    uyarı için: age_hours > max_age_hours ise yaş (gün) döner; aksi None
    yerine... Kullanıcı: 48 saatten eskiyse uyarı. Dönüş: stale ise
    age_days, değilse None.
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
    mx = row[0]
    if mx.tzinfo is None:
        mx = mx.replace(tzinfo=timezone.utc)
    age_hours = (
        datetime.now(timezone.utc) - mx.astimezone(timezone.utc)
    ).total_seconds() / 3600.0
    if age_hours > max_age_hours:
        return age_hours / 24.0
    return None


def print_call_stale_warning(conn: Any, org_id: str) -> None:
    age = call_events_stale_days(conn, org_id)
    if age is not None:
        days = max(1, int(age + 0.5))
        print(
            f"UYARI: cagri verisi {days} gundur guncellenmedi "
            f"(events.created_at channel=call)"
        )
