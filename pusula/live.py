"""Canlı operasyon kokpiti: bugün, aynı saatin geçmiş temposu, temsilci durumu.

Hesap pusula.metrics'tedir; burası birleştirir. Saf fonksiyonlar
(build_*, rep_status, baseline_until) veritabanı olmadan test edilir.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.metrics import (
    COUNT_KEYS,
    TZ,
    Span,
    combine,
    crm_tracking_enabled,
    finish,
    idle_minutes,
    in_shift,
    last_activity,
    query_kpis,
    shift_bounds,
)
from pusula.leaves import load_leaves
from pusula.occupancy import (
    DayTimeline,
    aggregate,
    attach,
    gaps_report,
    occupancy_rows,
    timelines_for,
)
from pusula.panel_data import connect, load_reps

HISTORY_DAYS = 90
IDLE_WARN_MIN = 15.0
IDLE_ALERT_MIN = 30.0
CALL_SOURCES = ("zoho_crm_calls", "live_ingest")


def history_span(day: date) -> Span:
    return Span.days(day - timedelta(days=HISTORY_DAYS), day - timedelta(days=1))


def same_type_isodow(day: date) -> tuple[int, ...]:
    return (6,) if day.weekday() == 5 else (1, 2, 3, 4, 5)


def same_type_day_count(day: date) -> int:
    span = history_span(day)
    wanted = set(same_type_isodow(day))
    count = 0
    cursor = span.start.date()
    while cursor < span.end.date():
        if cursor.isoweekday() in wanted:
            count += 1
        cursor += timedelta(days=1)
    return max(count, 1)


def baseline_until(
    hour_rows: dict[int, dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """Geçmiş saat ortalamalarından 'bu saate kadar' beklenen değer.

    Tamamlanan saatler tam, içinde bulunulan saat geçen dakika oranında.
    """
    local = now.astimezone(TZ)
    parts: list[dict[str, Any]] = []
    for hour, row in hour_rows.items():
        if hour < local.hour:
            parts.append(row)
        elif hour == local.hour:
            frac = local.minute / 60.0
            parts.append({k: (row.get(k) or 0) * frac for k in COUNT_KEYS})
    return combine(parts)


def rep_status(
    *,
    now: datetime,
    first_at: datetime | None,
    idle_min: float | None,
    in_meeting: bool = False,
    in_call: bool = False,
) -> str:
    """Süren toplantı veya arama hareketsizlik sayılmaz."""
    if not in_shift(now):
        return "mesai_disi"
    if in_meeting:
        return "toplantida"
    if in_call:
        return "aramada"
    if first_at is None:
        return "baslamadi"
    if idle_min is None:
        return "aktif"
    if idle_min >= IDLE_ALERT_MIN:
        return "hareketsiz"
    if idle_min >= IDLE_WARN_MIN:
        return "bosta"
    return "aktif"


def scale(row: dict[str, Any], factor: float) -> dict[str, Any]:
    out = dict(row)
    for key in COUNT_KEYS:
        value = out.get(key)
        if isinstance(value, (int, float)):
            out[key] = round(float(value) * factor, 2)
    out["ulasilan"] = round(float(out.get("ulasilan") or 0) * factor, 2)
    return out


def data_as_of() -> datetime | None:
    """Arama verisinin son başarılı senkronu (sync_state.updated_at)."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT max(updated_at) FROM sync_state
            WHERE org_id = %s
              AND (source_name = ANY(%s) OR source_name LIKE 'hourly_slot:%%')
            """,
            (get_org_id(), list(CALL_SOURCES)),
        ).fetchone()
    return row[0] if row else None


def history_by_rep_hour(day: date, now: datetime) -> dict[str, dict[int, dict[str, Any]]]:
    """Temsilci → saat → geçmiş günlük ortalama (aynı gün tipi)."""
    span = history_span(day)
    isodow = same_type_isodow(day)
    rows = query_kpis(span, group="rep_hour", isodow=isodow, now=now)
    rep_ids = [rep.rep_id for rep in load_reps()]
    occ = occupancy_rows(span, group="rep_hour", rep_ids=rep_ids, now=now, isodow=isodow)
    rows = attach(rows, occ)
    factor = 1.0 / same_type_day_count(day)
    out: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        out[str(row["rep_id"])][int(row["bucket"])] = scale(row, factor)
    return out


def _hour_row(
    day_span: Span,
    hour: int,
    cutoff: datetime,
    today_parts: list[dict[str, Any]],
    hist_parts: list[dict[str, Any]],
    *,
    n_reps: int,
) -> dict[str, Any]:
    start = day_span.start.replace(hour=hour)
    return {
        "hour": hour,
        "done": start + timedelta(hours=1) <= cutoff,
        "today": finish(combine(today_parts), n_reps=n_reps),
        "expected": finish(combine(hist_parts), n_reps=n_reps),
    }


def build_live(
    *,
    day: date,
    now: datetime,
    reps: list[tuple[str, str]],
    today_rows: list[dict[str, Any]],
    history: dict[str, dict[int, dict[str, Any]]],
    activity: dict[str, dict[str, Any]],
    as_of: datetime | None,
    crm_tracking: bool = False,
    timelines: dict[str, DayTimeline] | None = None,
    on_leave: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    """Kokpit yükü. today_rows: query_kpis(group='rep_hour') + doluluk dakikaları."""
    span = Span.day(day)
    timelines = timelines or {}
    is_today = now.astimezone(TZ).date() == day
    by_rep_hour: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in today_rows:
        by_rep_hour[str(row["rep_id"])][int(row["bucket"])] = row

    bounds = shift_bounds(day)
    hours = list(range(bounds[0].hour, bounds[1].hour)) if bounds else []
    cutoff = now if is_today else span.end

    rep_cards: list[dict[str, Any]] = []
    for rep_id, name in reps:
        today_hours = by_rep_hour.get(rep_id, {})
        hist_hours = history.get(rep_id, {})
        today = finish(combine(today_hours.values()))
        leave = rep_id in on_leave
        expected = finish(combine([]) if leave else baseline_until(hist_hours, cutoff))
        act = activity.get(rep_id, {})
        busy_now = bool(act.get("in_meeting") or act.get("in_call"))
        idle = (
            (0.0 if busy_now else idle_minutes(act.get("last_active_at"), now=now, data_as_of=as_of))
            if is_today and not leave
            else None
        )
        timeline = timelines.get(rep_id)
        rep_cards.append(
            {
                "rep_id": rep_id,
                "name": name,
                "today": today,
                "expected": expected,
                "first_at": act.get("first_at"),
                "last_active_at": act.get("last_active_at"),
                "last_call_at": act.get("last_call_at"),
                "last_meeting_at": act.get("last_meeting_at"),
                "last_whatsapp_at": act.get("last_whatsapp_at"),
                "last_crm_at": act.get("last_crm_at"),
                "idle_min": idle,
                "on_leave": leave,
                "status": "izinli"
                if leave
                else rep_status(
                    now=now,
                    first_at=act.get("first_at"),
                    idle_min=idle,
                    in_meeting=bool(act.get("in_meeting")),
                    in_call=bool(act.get("in_call")),
                )
                if is_today
                else "gecmis",
                "timeline": [
                    {"start": seg.start, "end": seg.end, "kind": seg.kind}
                    for seg in (timeline.segments if timeline else [])
                ],
                "gaps": gaps_report(timeline)[:5] if timeline else [],
                "hours": [
                    _hour_row(
                        span,
                        hour,
                        cutoff,
                        [today_hours[hour]] if hour in today_hours else [],
                        [hist_hours[hour]] if hour in hist_hours and not leave else [],
                        n_reps=1,
                    )
                    for hour in hours
                ],
            }
        )

    # İzinli temsilci ekip beklentisine ve kişi sayısına girmez.
    working = [(rep_id, name) for rep_id, name in reps if rep_id not in on_leave]
    n = len(working)
    team_today = finish(
        combine(row for rep in by_rep_hour.values() for row in rep.values()),
        n_reps=n,
    )
    team_expected = finish(
        combine(
            baseline_until(history.get(rep_id, {}), cutoff) for rep_id, _ in working
        ),
        n_reps=n,
    )
    rep_ids = {rep_id for rep_id, _ in reps}
    working_ids = {rep_id for rep_id, _ in working}
    team_hours = [
        _hour_row(
            span,
            hour,
            cutoff,
            [rows[hour] for rid, rows in by_rep_hour.items() if rid in rep_ids and hour in rows],
            [rows[hour] for rid, rows in history.items() if rid in working_ids and hour in rows],
            n_reps=n,
        )
        for hour in hours
    ]
    return {
        "day": day,
        "now": now,
        "is_today": is_today,
        "in_shift": in_shift(now) and is_today,
        "data_as_of": as_of,
        "crm_tracking": crm_tracking,
        "history_days": same_type_day_count(day),
        "team": {"today": team_today, "expected": team_expected},
        "hours": team_hours,
        "reps": rep_cards,
        "idle_thresholds": {"warn": IDLE_WARN_MIN, "alert": IDLE_ALERT_MIN},
    }


def load_live(
    day: date | None = None,
    now: datetime | None = None,
    *,
    history: dict[str, dict[int, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """history verilirse yeniden sorgulanmaz (gün içinde değişmez, önbellek)."""
    now = now or datetime.now(TZ)
    day = day or now.astimezone(TZ).date()
    reps = [(rep.rep_id, rep.full_name) for rep in load_reps()]
    span = Span.day(day)
    is_today = now.astimezone(TZ).date() == day
    as_of = data_as_of()
    # Şerit veri tazeliğinde biter: henüz çekilmemiş dakikalar kayıp sayılmaz.
    cutoff = min(now, as_of) if is_today and as_of else (now if is_today else span.end)
    rep_ids = [rep_id for rep_id, _ in reps]
    leaves = load_leaves(span)
    timelines = timelines_for(span, cutoff, rep_ids=rep_ids, leaves=leaves)
    occ = aggregate(timelines, "rep_hour", running_day=day if is_today else None)
    return build_live(
        day=day,
        now=now,
        reps=reps,
        today_rows=attach(query_kpis(span, group="rep_hour", now=now), occ),
        timelines={rep_id: t for (rep_id, _), t in timelines.items()},
        on_leave={rep_id for rep_id, leave_day in leaves if leave_day == day},
        history=history if history is not None else history_by_rep_hour(day, now),
        activity=last_activity(day, now=now),
        as_of=as_of,
        crm_tracking=crm_tracking_enabled(),
    )
