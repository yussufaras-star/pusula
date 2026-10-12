"""Temsilci portföyü: lead statüsüne göre dağılım ve dokunulmamış yeni adaylar.

"Yeni Müşteri Adayı" statüsündeki (pusula.lead_reach DOKUNULMAMIS) lead'e
son 24 saatte dokunulmadıysa uyarı. Dokunmak: lead'e giden arama veya
cevaplanan gelen arama, giden WhatsApp, ya da CRM'de lead kaydını
değiştirmek (crm_actions, işlemi yapan temsilci). Süre en son dokunuştan,
hiç yoksa atanmadan başlar; yeni atanan lead 24 saat dolmadan uyarı vermez.
Saat takvim saatidir (pazartesi sabahı cumartesiden kalanlar görünür).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.lead_reach import is_dokunulmamis_sql
from pusula.panel_data import connect
from pusula.temas import is_answered_inbound_sql, is_not_future_sql, is_not_planned_sql

UNTOUCHED_HOURS = 24
NEW_STATUS_LABEL = "Yeni Müşteri Adayı"


def _touch_sql(has_crm: bool) -> str:
    call = (
        f"e.channel = 'call' AND {is_not_planned_sql('e')} AND {is_not_future_sql('e')} "
        f"AND (e.direction = 'outbound' OR ({is_answered_inbound_sql('e')}))"
    )
    wp = "e.channel = 'whatsapp' AND e.direction = 'outbound' AND e.occurred_at <= now()"
    crm = (
        """(SELECT max(a.acted_at) FROM crm_actions a
            WHERE a.org_id = l.org_id AND a.module = 'Leads'
              AND a.record_id = l.lead_id AND a.acted_at <= now())"""
        if has_crm
        else "NULL::timestamptz"
    )
    return f"""
        SELECT l.lead_id, l.owner_rep_id, l.full_name, l.assigned_at,
               (SELECT max(e.occurred_at) FROM events e
                 WHERE e.org_id = l.org_id AND e.thread_id = l.thread_id
                   AND l.thread_id IS NOT NULL
                   AND (({call}) OR ({wp}))) AS last_event,
               {crm} AS last_crm
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s AND r.category = 'sales' AND r.active
          AND {is_dokunulmamis_sql('l.status')}
    """


def untouched_rows(rows: list[tuple[Any, ...]], now: datetime) -> list[dict[str, Any]]:
    """Saf: (lead_id, rep, ad, atanma, son olay, son crm) → 24 saati geçenler."""
    limit = now - timedelta(hours=UNTOUCHED_HOURS)
    out = []
    for lead_id, rep_id, name, assigned_at, last_event, last_crm in rows:
        touches = [t for t in (last_event, last_crm) if t is not None]
        last_touch = max(touches) if touches else None
        since = max([t for t in (last_touch, assigned_at) if t is not None], default=None)
        if since is not None and since > limit:
            continue
        out.append(
            {
                "lead_id": str(lead_id),
                "rep_id": str(rep_id),
                "name": name,
                "assigned_at": assigned_at,
                "last_touch": last_touch,
                "hours": round((now - since).total_seconds() / 3600, 1) if since else None,
            }
        )
    # En uzun bekleyen üstte; süresi bilinmeyen en sonda.
    return sorted(out, key=lambda x: (x["hours"] is None, -(x["hours"] or 0)))


def load_portfolio(now: datetime) -> dict[str, Any]:
    org_id = get_org_id()
    with connect() as conn:
        status_rows = conn.execute(
            """
            SELECT l.owner_rep_id, coalesce(nullif(btrim(l.status), ''), 'Statüsüz'), count(*)
            FROM leads l
            JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
            WHERE l.org_id = %s AND r.category = 'sales' AND r.active
            GROUP BY 1, 2
            """,
            (org_id,),
        ).fetchall()
        exists = conn.execute("SELECT to_regclass('crm_actions')").fetchone()
        has_crm = bool(exists and exists[0])
        lead_rows = conn.execute(_touch_sql(has_crm), (org_id,)).fetchall()
    return {
        "new_status": NEW_STATUS_LABEL,
        "untouched_hours": UNTOUCHED_HOURS,
        "by_status": [
            {"rep_id": str(rep_id), "status": status, "count": int(count)}
            for rep_id, status, count in status_rows
        ],
        "untouched": untouched_rows([tuple(r) for r in lead_rows], now),
    }
