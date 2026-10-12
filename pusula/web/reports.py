"""Dönemsel raporlar: huni, dönüşüm, satış döngüsü, ciro, haftalık seriler.

Eski panelin analizleri; hesap panel_data / panel_ciro'dadır.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException

from pusula.panel_auth import AuthUser
from pusula.panel_ciro import (
    ciro_monthly_by_rep,
    ciro_team_monthly,
    ciro_team_year_compare,
    ciro_ytd_by_rep,
    latest_deal_created_at,
)
from pusula.panel_data import (
    DateWindow,
    conv_window,
    funnel,
    funnel_dropped_by_rep,
    lead_reach_breakdown,
    load_reps,
    path_take_rate,
    sales_cycle,
    source_take_rate,
    take_rate,
    talk_duration_by_rep,
    team_reach_and_join,
    weekly_series,
    weekly_team_series,
)


def selected_rep(user: AuthUser, requested: str | None) -> str | None:
    """Temsilci her zaman kendisine kilitlenir; yönetici seçebilir."""
    if user.role == "rep":
        return user.rep_id
    if requested in (None, "", "team"):
        return None
    if requested not in {rep.rep_id for rep in load_reps()}:
        raise HTTPException(status_code=404, detail="Temsilci bulunamadı")
    return requested


def _named(rows: list[dict[str, Any]], rep_id: str | None) -> list[dict[str, Any]]:
    if rep_id is None:
        return rows
    rep = next((item for item in load_reps() if item.rep_id == rep_id), None)
    if rep is None:
        return []
    return [row for row in rows if row.get("temsilci") == rep.full_name]


def build_reports(
    user: AuthUser, rep_id: str | None, days: int, today: date
) -> dict[str, Any]:
    window = DateWindow(start=today - timedelta(days=days), end=today)
    conversion = conv_window(window)
    is_admin = user.role == "admin"
    if is_admin and rep_id is None:
        reach_rows = lead_reach_breakdown(window, by_rep=True)
    else:
        reach_rows = lead_reach_breakdown(window, rep_id=rep_id)
    cycle_rows, cycle_team = sales_cycle(window)
    take_rows, take_team = take_rate(window)
    payload: dict[str, Any] = {
        "period": {
            "days": days,
            "start": window.start,
            "end": window.end,
            "conversion_start": conversion.start,
            "conversion_end": conversion.end,
        },
        "reach": reach_rows,
        "talk_time": _named(talk_duration_by_rep(window), rep_id),
        "sales_cycle": {
            "by_rep": _named(cycle_rows, rep_id),
            "team": cycle_team if rep_id is None else None,
        },
        "conversion": {
            "by_rep": _named(take_rows, rep_id),
            "summary": take_team if rep_id is None else None,
        },
        "funnel": _named(funnel(rep_id, named=True, window=window), rep_id),
        "funnel_dropped": _named(funnel_dropped_by_rep(window), rep_id),
        "weekly": weekly_series(rep_id, window),
        "latest_deal_at": latest_deal_created_at(),
    }
    sales_ytd = [
        row
        for row in ciro_ytd_by_rep("sales")
        if rep_id is None or row["rep_id"] == rep_id
    ]
    if is_admin:
        payload["source_conversion"] = source_take_rate(window)
        payload["conversion_path"] = path_take_rate(window)
        payload["weekly_team"] = weekly_team_series(window)
        payload["team"] = {"reach_and_attendance": team_reach_and_join(window)}
        payload["sales"] = {
            "year_to_date": sales_ytd,
            "monthly": ciro_monthly_by_rep("sales", rep_id=rep_id),
            "team_monthly": ciro_team_monthly(),
            "year_compare": ciro_team_year_compare(),
            "after_sales_year_to_date": ciro_ytd_by_rep("after_sales"),
            "after_sales_monthly": ciro_monthly_by_rep("after_sales", rep_id=None),
        }
    elif rep_id:
        payload["sales"] = {
            "year_to_date": sales_ytd,
            "monthly": ciro_monthly_by_rep("sales", rep_id=rep_id),
        }
    return payload
