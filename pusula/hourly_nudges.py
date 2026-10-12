"""Business-hours KPI nudges for individual sales reps and the sales team."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from pusula.block_share import (
    TEAM_REP_ID,
    SalesRep,
    apply_shadow,
    load_sales_reps,
    post_cliq,
    shadow_email,
)
from pusula.config import get_org_id
from pusula.kpi_standards import (
    evaluate_hourly_kpis,
    hour_kinds_for,
    latest_ready_hour,
)
from pusula.panel_data import connect, hour_history, today_hours

logger = logging.getLogger(__name__)
_TZ = ZoneInfo("Europe/Istanbul")


def _control_hour(now: datetime) -> int | None:
    """Choose a finished hour; Saturday's final hour is checked at 14:50."""
    local = now.astimezone(_TZ) if now.tzinfo else now.replace(tzinfo=_TZ)
    if local.minute < 15:
        return None
    if local.weekday() == 6 or local.hour >= 19:
        return None
    if local.weekday() == 5 and local.hour >= 15:
        return None
    latest = latest_ready_hour(local)
    if latest is None or local.weekday() == 6:
        return None
    return latest


def _format_gap(gap: dict[str, Any]) -> str:
    actual = gap["actual"]
    target = gap["target"]
    if gap["unit"] == "%":
        value = f"%{actual:g} (hedef en az %{target:g})"
    elif gap["key"] == "meeting_outcome":
        value = f"{actual} randevunun sonucu girilmemiş"
    else:
        value = f"{actual} (hedef en az {target:g})"
    sample = gap.get("sample")
    suffix = f", örneklem {sample}" if sample is not None else ""
    return f"- {gap['label']}: {value}{suffix}"


def format_kpi_message(
    *,
    day: date,
    hour: int,
    alerts: list[dict[str, Any]],
    recipient_name: str | None,
    is_team: bool,
) -> str:
    audience = "Ekip" if is_team else (recipient_name or "Merhaba")
    lines = [
        f"🧭 Pusula saatlik KPI kontrolü — {day:%d.%m.%Y} {hour:02d}:00–{hour + 1:02d}:00",
        f"{audience},",
        "Bu saatlik kontrol aşağıdaki başlangıç standartlarının altında kalan başlıkları gösteriyor:",
    ]
    lines.extend(_format_gap(gap) for gap in alerts)
    lines.append(
        "Sonraki uygun blokta bu başlıklara odaklanıp CRM kayıtlarını tamamlayabilirsin."
    )
    lines.append(
        "Not: arama/ulaşma hedefleri aynı saat ve gün türünün son 90 günlük "
        "ortalamasına göre hesaplanır; oranlar düşük örneklemde değerlendirilmez."
    )
    return "\n".join(lines)


def _reserve_delivery(
    *,
    rep_id: str,
    day: date,
    hour: int,
    scope: str,
) -> bool:
    """Atomically reserve a delivery; a failed previous attempt can be retried."""
    org_id = get_org_id()
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO kpi_nudge_sends (
                org_id, rep_id, nudge_date, nudge_hour, scope, status
            )
            VALUES (%s, %s, %s, %s, %s, 'sending')
            ON CONFLICT (org_id, rep_id, nudge_date, nudge_hour, scope)
            DO UPDATE SET status = 'sending', failure = NULL,
                          updated_at = now()
            WHERE kpi_nudge_sends.status = 'failed'
               OR (
                   kpi_nudge_sends.status = 'sending'
                   AND kpi_nudge_sends.updated_at < now() - interval '10 minutes'
               )
            RETURNING 1
            """,
            (org_id, rep_id, day, hour, scope),
        ).fetchone()
        conn.commit()
    return row is not None


def _finish_delivery(
    *,
    rep_id: str,
    day: date,
    hour: int,
    scope: str,
    failure: str | None = None,
) -> None:
    org_id = get_org_id()
    with connect() as conn:
        conn.execute(
            """
            UPDATE kpi_nudge_sends
            SET status = %s, sent_at = %s, failure = %s, updated_at = now()
            WHERE org_id = %s AND rep_id = %s
              AND nudge_date = %s AND nudge_hour = %s AND scope = %s
            """,
            (
                "failed" if failure else "sent",
                None if failure else datetime.now(_TZ),
                failure,
                failure[:500] if failure else None,
                org_id,
                rep_id,
                day,
                hour,
                scope,
            ),
        )
        conn.commit()


def _send_message(
    *,
    rep_id: str,
    day: date,
    hour: int,
    scope: str,
    intended: str,
    recipients: str,
    body: str,
) -> bool:
    if not recipients:
        raise RuntimeError(f"KPI dürtüsü için Cliq alıcısı yok: {intended}")
    if not _reserve_delivery(
        rep_id=rep_id, day=day, hour=hour, scope=scope
    ):
        return False
    shadow = shadow_email()
    text, destination, is_shadow = apply_shadow(body, intended)
    try:
        post_cliq(text, destination if is_shadow else recipients)
    except Exception as exc:
        failure = type(exc).__name__
        status_code = getattr(exc, "code", None)
        if isinstance(status_code, int):
            failure = f"{failure} HTTP {status_code}"
        _finish_delivery(
            rep_id=rep_id,
            day=day,
            hour=hour,
            scope=scope,
            failure=failure,
        )
        raise RuntimeError(f"Cliq gönderimi başarısız: {failure}") from None
    _finish_delivery(rep_id=rep_id, day=day, hour=hour, scope=scope)
    logger.info(
        "hourly KPI nudge sent: scope=%s rep=%s day=%s hour=%s shadow=%s",
        scope,
        rep_id,
        day,
        hour,
        bool(shadow),
    )
    return True


def run_hourly_nudges(now: datetime | None = None) -> dict[str, Any]:
    """Evaluate finished work hours and send at most one message per audience."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    from pusula.freshness import is_mesai

    if not is_mesai(local):
        return {"sent": 0, "skipped": "outside_business_hours"}
    hour = _control_hour(local)
    if hour is None:
        return {"sent": 0, "skipped": "hour_not_ready"}

    day = local.date()
    call_hour, meeting_hour = hour_kinds_for(day, hour)
    reps = load_sales_reps()
    sent = 0
    people_below: list[tuple[SalesRep, list[dict[str, Any]]]] = []

    for rep in reps:
        actual_by_hour = {
            int(row["saat"]): row for row in today_hours(rep.rep_id, day)
        }
        actual = actual_by_hour.get(hour)
        history = hour_history(rep.rep_id, day).get(hour)
        if actual is None or history is None:
            continue
        alerts = evaluate_hourly_kpis(
            actual,
            history,
            hour=hour,
            is_meeting_hour=meeting_hour,
            is_call_hour=call_hour,
        )
        if alerts:
            people_below.append((rep, alerts))
            body = format_kpi_message(
                day=day,
                hour=hour,
                alerts=alerts,
                recipient_name=rep.full_name,
                is_team=False,
            )
            sent += _send_message(
                rep_id=rep.rep_id,
                day=day,
                hour=hour,
                scope="person",
                intended=rep.full_name,
                recipients=rep.email,
                body=body,
            )

    actual_by_hour = {
        int(row["saat"]): row for row in today_hours(None, day)
    }
    actual_team = actual_by_hour.get(hour)
    history_team = hour_history(None, day).get(hour)
    team_alerts = (
        evaluate_hourly_kpis(
            actual_team,
            history_team,
            hour=hour,
            is_meeting_hour=meeting_hour,
            is_call_hour=call_hour,
        )
        if actual_team is not None and history_team is not None
        else []
    )
    if team_alerts:
        recipient_emails = sorted({rep.email for rep in reps if rep.email})
        body = format_kpi_message(
            day=day,
            hour=hour,
            alerts=team_alerts,
            recipient_name=None,
            is_team=True,
        )
        sent += _send_message(
            rep_id=TEAM_REP_ID,
            day=day,
            hour=hour,
            scope="team",
            intended="satış ekibi",
            recipients=",".join(recipient_emails),
            body=body,
        )

    return {
        "sent": sent,
        "day": day.isoformat(),
        "hour": hour,
        "people_below_standard": len(people_below),
        "team_alerts": len(team_alerts),
    }
