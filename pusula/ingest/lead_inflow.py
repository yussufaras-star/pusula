"""Gelen lead akışı: Zoho Leads modülüne günde kaç lead düştü.

Sayım Created_Time'a göredir ve contact'a dönüştürülmüş lead'leri de içerir
(Zoho dönüştürülenleri varsayılan sorguda gizler; 2026 Q1'de lead'ler
contact'a çevriliyordu). CRM taşımasında 1-2 Nisan 2026'da toplu içe
aktarılan ~16,6 bin eski kayıt akış değildir; o günler is_import ile
işaretlenir ve toplamlara girmez.

Ekip Panosu'ndaki "gelen lead" anahtar sonucu buradan okunur. Tablo ilk
yazmada oluşur; yoksa okuma None döner.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.ingest.crm_actions import parse_zoho_time
from pusula.panel_data import connect

IMPORT_DAYS = frozenset({date(2026, 4, 1), date(2026, 4, 2)})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lead_inflow_daily (
    org_id      text NOT NULL DEFAULT 'rexven',
    day         date NOT NULL,
    n           int NOT NULL,
    converted   int NOT NULL DEFAULT 0,
    is_import   boolean NOT NULL DEFAULT false,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, day)
);
ALTER TABLE lead_inflow_daily ENABLE ROW LEVEL SECURITY;
"""


def bucket(created: Iterable[Any]) -> Counter[date]:
    """Zoho Created_Time değerlerini İstanbul gününe sayar."""
    out: Counter[date] = Counter()
    for value in created:
        moment = parse_zoho_time(value)
        if moment is not None:
            out[moment.date()] += 1
    return out


def day_rows(first: date, last: date, total: Counter[date], converted: Counter[date]) -> list[tuple[date, int, int, bool]]:
    """Aralığın her günü (sıfırlar dahil): (gün, toplam, dönüştürülen, içe aktarım mı)."""
    rows = []
    day = first
    while day <= last:
        rows.append((day, total[day], converted[day], day in IMPORT_DAYS))
        day += timedelta(days=1)
    return rows


def _month_chunks(first: date, last: date) -> list[tuple[date, date]]:
    out = []
    start = first
    while start <= last:
        nxt = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
        out.append((start, min(last, nxt - timedelta(days=1))))
        start = nxt
    return out


def fetch(first: date, last: date) -> tuple[Counter[date], Counter[date]]:
    """(tüm lead'ler, dönüştürülenler) günlük sayım. Yalnız okuma."""
    from pusula.zoho import coql

    total: Counter[date] = Counter()
    converted: Counter[date] = Counter()
    for start, end in _month_chunks(first, last):
        a = f"{start.isoformat()}T00:00:00+03:00"
        b = f"{(end + timedelta(days=1)).isoformat()}T00:00:00+03:00"
        where = f"Created_Time between '{a}' and '{b}'"
        plain = bucket(r.get("Created_Time") for r in coql(f"select Created_Time from Leads where {where}"))
        conv = bucket(
            r.get("Created_Time")
            for r in coql(f"select Created_Time from Leads where Converted__s = true and {where}")
        )
        # between iki ucu da kapsar; ertesi günün 00:00 kaydı o güne aittir.
        plain.pop(end + timedelta(days=1), None)
        conv.pop(end + timedelta(days=1), None)
        total.update(plain)
        total.update(conv)
        converted.update(conv)
    return total, converted


def write(rows: list[tuple[date, int, int, bool]]) -> None:
    org = get_org_id()
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        for day, n, conv, is_import in rows:
            conn.execute(
                """
                INSERT INTO lead_inflow_daily (org_id, day, n, converted, is_import, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (org_id, day) DO UPDATE
                SET n = EXCLUDED.n, converted = EXCLUDED.converted,
                    is_import = EXCLUDED.is_import, updated_at = now()
                """,
                (org, day, n, conv, is_import),
            )


def first_stored_day() -> date | None:
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('lead_inflow_daily')").fetchone()
        if not exists or exists[0] is None:
            return None
        row = conn.execute(
            "SELECT min(day) FROM lead_inflow_daily WHERE org_id = %s", (get_org_id(),)
        ).fetchone()
    return row[0] if row else None


def sync(today: date, *, since: date | None = None, lookback_days: int = 3) -> dict[str, Any]:
    """Tablo boşsa yıl başından, değilse son birkaç günü yeniden sayar."""
    if since is None:
        stored = first_stored_day()
        since = date(today.year, 1, 1) if stored is None else today - timedelta(days=lookback_days)
    total, converted = fetch(since, today)
    rows = day_rows(since, today, total, converted)
    write(rows)
    return {
        "since": since.isoformat(),
        "days": len(rows),
        "leads": sum(n for _, n, _, imp in rows if not imp),
        "import_excluded": sum(n for _, n, _, imp in rows if imp),
    }


def load_inflow(first: date, last: date) -> int | None:
    """Aralıkta gelen lead (içe aktarım günleri hariç). Tablo aralığın başını
    kapsamıyorsa None."""
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('lead_inflow_daily')").fetchone()
        if not exists or exists[0] is None:
            return None
        row = conn.execute(
            """
            SELECT min(day), coalesce(sum(n) FILTER (WHERE NOT is_import), 0)
            FROM lead_inflow_daily WHERE org_id = %s AND day BETWEEN %s AND %s
            """,
            (get_org_id(), first, last),
        ).fetchone()
    if not row or row[0] is None or row[0] > first:
        return None
    return int(row[1])


def now_istanbul() -> datetime:
    from pusula.metrics import TZ

    return datetime.now(TZ)
