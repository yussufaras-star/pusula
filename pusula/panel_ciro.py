"""Ciro: kazanılan anlaşmalar, ilk/tekrar ayrımı yok.

Kaynak deals. owner_rep_id. Satış ekibi ile satış sonrası ekip ayrılır.
Satışa dönme oranı hesaplarına dokunulmaz.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pusula.config import get_org_id
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

_TZ = ZoneInfo("Europe/Istanbul")
CIRO_START = datetime(2026, 1, 1, tzinfo=_TZ)
MONTH_COUNT = 12

# Zoho user id — isim değişse bile ekip tanımı sabit kalsın.
SALES_TEAM_IDS: tuple[str, ...] = (
    "6847331000013182011",  # Miray Aksel
    "6847331000013134001",  # Serkan Şahin
    "6847331000013140001",  # Ayşe Kar
    "6847331000049858071",  # Abdullah Benli
)
AFTER_SALES_IDS: tuple[str, ...] = (
    "6847331000000615001",  # Nihat Kemer
    "6847331000000653001",  # Burcu Özkan
)

TeamKind = Literal["sales", "after_sales"]

_MONTH_TR = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)


def _deal_at_sql(alias: str = "d") -> str:
    return f"coalesce({alias}.closed_at, {alias}.created_at)"


def _team_ids(kind: TeamKind) -> tuple[str, ...]:
    if kind == "after_sales":
        return AFTER_SALES_IDS
    return SALES_TEAM_IDS


def fmt_tl(amount: float | int | None) -> str:
    """1.462.224 TL — kuruş yok, toplam yuvarlanır."""
    if amount is None:
        return "—"
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        n = 0
    grouped = f"{n:,}".replace(",", ".")
    return f"{grouped} TL"


def month_label(value: datetime) -> str:
    local = value if value.tzinfo is None else value.astimezone(_TZ)
    return f"{_MONTH_TR[local.month - 1]} {local.year}"


def _avg_deal(ciro: float, n: int) -> float | None:
    if n <= 0:
        return None
    return float(ciro) / float(n)


def latest_deal_created_at() -> datetime | None:
    """deals.created_at en yenisi — ingest gecikmesi için."""
    org_id = get_org_id()
    with connect() as conn:
        row = conn.execute(
            "SELECT max(created_at) FROM deals WHERE org_id = %s",
            (org_id,),
        ).fetchone()
    if not row or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, datetime):
        return value
    return None


def _won_base_sql(alias: str = "d") -> str:
    at = _deal_at_sql(alias)
    return f"""
        {alias}.org_id = %s
        AND {alias}.stage = %s
        AND {at} >= %s
        AND {at} <= now()
        AND {alias}.owner_rep_id = ANY(%s)
    """


def ciro_ytd_by_rep(kind: TeamKind) -> list[dict[str, Any]]:
    """2026 başından bugüne: temsilci, satış, ciro, ortalama."""
    ids = _team_ids(kind)
    org_id = get_org_id()
    sql = f"""
        SELECT r.rep_id, r.full_name,
          count(d.deal_id)::int AS satis,
          coalesce(sum(d.amount), 0)::float AS ciro
        FROM unnest(%s::text[]) WITH ORDINALITY AS t(rep_id, ord)
        JOIN reps r ON r.org_id = %s AND r.rep_id = t.rep_id
        LEFT JOIN deals d
          ON d.org_id = r.org_id
         AND d.owner_rep_id = r.rep_id
         AND d.stage = %s
         AND {_deal_at_sql("d")} >= %s
         AND {_deal_at_sql("d")} <= now()
        GROUP BY r.rep_id, r.full_name, t.ord
        ORDER BY t.ord
    """
    with connect() as conn:
        rows = conn.execute(
            sql,
            (list(ids), org_id, WON_STAGE, CIRO_START),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for rep_id, name, satis, ciro in rows:
        n = int(satis)
        total = float(ciro or 0)
        out.append(
            {
                "rep_id": str(rep_id),
                "temsilci": str(name),
                "satis": n,
                "ciro": total,
                "ortalama": _avg_deal(total, n),
            }
        )
    return out


def ciro_monthly_by_rep(
    kind: TeamKind, *, rep_id: str | None = None
) -> list[dict[str, Any]]:
    """Ay × temsilci. rep_id doluysa tek kişi (satış ekibindeyse)."""
    ids = _team_ids(kind)
    if rep_id is not None:
        if kind == "sales" and rep_id not in SALES_TEAM_IDS:
            return []
        ids = (rep_id,)
    org_id = get_org_id()
    sql = f"""
        WITH months AS (
            SELECT generate_series(
                date_trunc(
                    'month',
                    (now() AT TIME ZONE 'Europe/Istanbul')
                ) - interval '{MONTH_COUNT - 1} months',
                date_trunc('month', (now() AT TIME ZONE 'Europe/Istanbul')),
                interval '1 month'
            ) AS month_start
        ),
        roster AS (
            SELECT r.rep_id, r.full_name, t.ord
            FROM unnest(%s::text[]) WITH ORDINALITY AS t(rep_id, ord)
            JOIN reps r ON r.org_id = %s AND r.rep_id = t.rep_id
        ),
        won AS (
            SELECT
              d.owner_rep_id AS rep_id,
              date_trunc(
                'month',
                {_deal_at_sql("d")} AT TIME ZONE 'Europe/Istanbul'
              ) AS month_start,
              count(*)::int AS satis,
              coalesce(sum(d.amount), 0)::float AS ciro
            FROM deals d
            WHERE {_won_base_sql("d")}
            GROUP BY 1, 2
        )
        SELECT roster.rep_id, roster.full_name, months.month_start,
          coalesce(won.satis, 0)::int,
          coalesce(won.ciro, 0)::float
        FROM months
        CROSS JOIN roster
        LEFT JOIN won
          ON won.rep_id = roster.rep_id AND won.month_start = months.month_start
        ORDER BY months.month_start, roster.ord
    """
    args: tuple[Any, ...] = (
        list(ids),
        org_id,
        org_id,
        WON_STAGE,
        CIRO_START,
        list(ids),
    )
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for rid, name, month, satis, ciro in rows:
        n = int(satis)
        total = float(ciro or 0)
        month_dt = month if isinstance(month, datetime) else None
        out.append(
            {
                "rep_id": str(rid),
                "temsilci": str(name),
                "ay": month_dt,
                "ay_etiket": month_label(month_dt) if month_dt else "—",
                "satis": n,
                "ciro": total,
                "ortalama": _avg_deal(total, n),
            }
        )
    return out


def ciro_team_monthly() -> list[dict[str, Any]]:
    """Satış ekibi aylık toplam ciro, son 12 ay."""
    rows = ciro_monthly_by_rep("sales")
    by_month: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        month = row["ay"]
        if not isinstance(month, datetime):
            continue
        bucket = by_month.setdefault(
            month,
            {"ay": month, "ay_etiket": row["ay_etiket"], "satis": 0, "ciro": 0.0},
        )
        bucket["satis"] = int(bucket["satis"]) + int(row["satis"])
        bucket["ciro"] = float(bucket["ciro"]) + float(row["ciro"])
    out: list[dict[str, Any]] = []
    for month in sorted(by_month):
        bucket = by_month[month]
        n = int(bucket["satis"])
        total = float(bucket["ciro"])
        out.append(
            {
                "ay": month,
                "ay_etiket": bucket["ay_etiket"],
                "satis": n,
                "ciro": total,
                "ortalama": _avg_deal(total, n),
                "kisi_basi": total / max(len(SALES_TEAM_IDS), 1),
            }
        )
    return out


def ciro_rep_monthly(rep_id: str) -> list[dict[str, Any]]:
    """Tek temsilcinin aylık cirosu (satış ekibi üyesi)."""
    return ciro_monthly_by_rep("sales", rep_id=rep_id)


def ciro_weekly_by_rep(rep_id: str | None) -> list[dict[str, float | None | datetime]]:
    """Son 8+ hafta ciro — eğri yönü için. Satış ekibi."""
    org_id = get_org_id()
    extra = ""
    params: list[Any] = [org_id, WON_STAGE, CIRO_START, list(SALES_TEAM_IDS)]
    if rep_id:
        extra = " AND d.owner_rep_id = %s "
        params.append(rep_id)
    sql = f"""
        WITH weeks AS (
            SELECT generate_series(
                date_trunc(
                    'week',
                    (now() AT TIME ZONE 'Europe/Istanbul')
                ) - interval '11 weeks',
                date_trunc('week', (now() AT TIME ZONE 'Europe/Istanbul')),
                interval '1 week'
            ) AS week_start
        ),
        won AS (
            SELECT
              date_trunc(
                'week',
                {_deal_at_sql("d")} AT TIME ZONE 'Europe/Istanbul'
              ) AS week_start,
              coalesce(sum(d.amount), 0)::float AS ciro,
              count(*)::int AS satis
            FROM deals d
            WHERE {_won_base_sql("d")}
              {extra}
            GROUP BY 1
        )
        SELECT weeks.week_start,
          coalesce(won.ciro, 0)::float,
          coalesce(won.satis, 0)::int
        FROM weeks
        LEFT JOIN won ON won.week_start = weeks.week_start
        ORDER BY weeks.week_start
    """
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    n_div = 1 if rep_id else max(len(SALES_TEAM_IDS), 1)
    out: list[dict[str, float | None | datetime]] = []
    for week, ciro, satis in rows:
        total = float(ciro or 0)
        out.append(
            {
                "hafta": week,
                "ciro": total / n_div,
                "satis": int(satis or 0) / n_div,
            }
        )
    return out
