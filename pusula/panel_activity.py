"""Tarih aralığında satış ekibi süre sırası.

Canlı panel_data bu isimleri yüklemediği için sorgu burada durur.
Eski panel_data sembollerine (connect, süre biçimi) fonksiyon içinde bağlanır.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from pusula.temas import is_answered_inbound_sql, is_cevirme_sql, is_temas_sql

logger = logging.getLogger(__name__)

_MONTHS = (
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
ACTIVITY_REL = 0.20
TALK_AVG_MIN_N = 3
MEET_SHARE_NOTE = 0.60


def fmt_span(start: date, end: date) -> str:
    """'28 Eylül – 2 Ekim 2026'. Aynı günse tek tarih."""
    if start == end:
        return f"{start.day} {_MONTHS[start.month - 1]} {start.year}"
    left = f"{start.day} {_MONTHS[start.month - 1]}"
    if start.year != end.year:
        left = f"{left} {start.year}"
    right = f"{end.day} {_MONTHS[end.month - 1]} {end.year}"
    return f"{left} – {right}"


def activity_total_sec(phone_sec: float, meet_min: float) -> float:
    """Telefon saniyesi + katılınan toplantı dakikası."""
    phone = max(float(phone_sec), 0.0)
    meet = max(float(meet_min), 0.0)
    return phone + meet * 60.0


def rank_activity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Toplam süre azalan. Eşitlikte ad, sonra rep_id. Sıra 1'den."""
    prepared: list[dict[str, Any]] = []
    for row in rows:
        phone = float(row.get("phone_sec") or 0)
        meet = float(row.get("meet_min") or 0)
        talk_n = int(row.get("talk_n") or 0)
        meet_n = int(row.get("meet_n") or 0)
        item = dict(row)
        item["phone_sec"] = phone
        item["meet_min"] = meet
        item["talk_n"] = talk_n
        item["meet_n"] = meet_n
        dial_n = int(row.get("dial_n") or 0)
        out_talk_n = int(row.get("out_talk_n") or 0)
        book_n = int(row.get("book_n") or 0)
        noshow_n = int(row.get("noshow_n") or 0)
        item["dial_n"] = dial_n
        item["out_talk_n"] = out_talk_n
        item["book_n"] = book_n
        item["noshow_n"] = noshow_n
        item["dial_rate"] = (100.0 * out_talk_n / dial_n) if dial_n else None
        join_den = meet_n + noshow_n
        item["join_rate"] = (100.0 * meet_n / join_den) if join_den else None
        item["total_sec"] = activity_total_sec(phone, meet)
        item["avg_sec"] = (phone / talk_n) if talk_n > 0 else None
        prepared.append(item)
    prepared.sort(
        key=lambda r: (
            -float(r["total_sec"]),
            str(r.get("temsilci") or "").casefold(),
            str(r.get("rep_id") or ""),
        )
    )
    out: list[dict[str, Any]] = []
    for index, row in enumerate(prepared, start=1):
        ranked = dict(row)
        ranked["sira"] = index
        out.append(ranked)
    return out


def _activity_band(value: float | None, baseline: float | None) -> str | None:
    """Ekip ortalamasına göre alti / yakin / ustu."""
    if value is None or baseline is None:
        return None
    base = float(baseline)
    if base <= 0:
        return None
    rel = (float(value) - base) / base
    if rel <= -ACTIVITY_REL:
        return "alti"
    if rel >= ACTIVITY_REL:
        return "ustu"
    return "yakin"


def activity_team_baseline(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """Ekip: görüşme ortalaması havuz, toplam süre kişi ortalaması."""
    if not rows:
        return {"avg_sec": None, "total_sec": None}
    phone = sum(float(row.get("phone_sec") or 0) for row in rows)
    talks = sum(int(row.get("talk_n") or 0) for row in rows)
    totals = [float(row.get("total_sec") or 0) for row in rows]
    dials = sum(int(row.get("dial_n") or 0) for row in rows)
    out_talks = sum(int(row.get("out_talk_n") or 0) for row in rows)
    meets = sum(int(row.get("meet_n") or 0) for row in rows)
    noshows = sum(int(row.get("noshow_n") or 0) for row in rows)
    join_den = meets + noshows
    return {
        "avg_sec": (phone / talks) if talks else None,
        "total_sec": sum(totals) / len(totals),
        "dial_rate": (100.0 * out_talks / dials) if dials else None,
        "join_rate": (100.0 * meets / join_den) if join_den else None,
    }


def efficiency_comment(
    row: dict[str, Any],
    team: dict[str, float | None],
) -> str:
    """Kişi yorumu. Gözlem ve ölçülen süre. Talimat yok."""
    from pusula.panel_data import fmt_clock_span

    phone = float(row.get("phone_sec") or 0)
    meet_min = float(row.get("meet_min") or 0)
    total = float(row.get("total_sec") or 0)
    talk_n = int(row.get("talk_n") or 0)
    meet_n = int(row.get("meet_n") or 0)
    avg = row.get("avg_sec")
    if avg is None and talk_n > 0:
        avg = phone / talk_n
    if talk_n == 0 and meet_n == 0:
        return "Telefon ve toplantı kaydı yok."
    if talk_n == 0 and meet_min <= 0:
        return "Telefon görüşmesi yok. Toplantı süresi çevrilemedi."
    if talk_n == 0:
        return "Telefon görüşmesi yok. Süre toplantıdan geliyor."

    parts: list[str] = []
    volume = _activity_band(total, team.get("total_sec"))
    volume_text = {
        "alti": "Müşteriyle geçen süre ekibin altında",
        "ustu": "Müşteriyle geçen süre ekibin üstünde",
        "yakin": "Müşteriyle geçen süre ekibe yakın",
    }.get(volume or "")
    own_total = fmt_clock_span(total, day_total=True)
    if volume_text and team.get("total_sec") is not None:
        team_total = fmt_clock_span(team.get("total_sec"), day_total=True)
        parts.append(f"{volume_text} ({own_total}, ekip {team_total}).")
    else:
        parts.append(f"Müşteriyle geçen süre {own_total}.")

    if talk_n < TALK_AVG_MIN_N:
        parts.append(f"Ortalama görüşme için veri yetersiz ({talk_n} görüşme).")
    else:
        avg_band = _activity_band(
            float(avg) if avg is not None else None,
            team.get("avg_sec"),
        )
        avg_text = {
            "alti": "Görüşme ortalaması ekibin altında",
            "ustu": "Görüşme ortalaması ekibin üstünde",
            "yakin": "Görüşme ortalaması ekibe yakın",
        }.get(avg_band or "")
        if avg_text and avg is not None and team.get("avg_sec") is not None:
            own_avg = fmt_clock_span(float(avg))
            team_avg = fmt_clock_span(team.get("avg_sec"))
            parts.append(
                f"{avg_text} ({own_avg}, ekip {team_avg}, {talk_n} görüşme)."
            )
        elif avg is not None:
            parts.append(
                f"Görüşme ortalaması {fmt_clock_span(float(avg))} "
                f"({talk_n} görüşme)."
            )

    meet_sec = meet_min * 60.0
    if total > 0 and meet_sec / total >= MEET_SHARE_NOTE and talk_n > 0:
        parts.append("Sürenin çoğu toplantıda.")
    elif meet_n == 0 and volume in {"ustu", "yakin"} and talk_n >= TALK_AVG_MIN_N:
        parts.append("Toplantı yok.")
    funnel = _funnel_note(row, team)
    if funnel:
        parts.append(funnel)
    return " ".join(parts)


def _fmt_rate(value: float) -> str:
    return f"%{value:.1f}"


def _funnel_note(row: dict[str, Any], team: dict[str, float | None]) -> str | None:
    """En açık oran sapması. Talimat yok. Payda 5'in altındaysa sus."""
    notes: list[tuple[float, str]] = []
    dial_n = int(row.get("dial_n") or 0)
    if (
        dial_n >= 5
        and row.get("dial_rate") is not None
        and team.get("dial_rate") is not None
    ):
        own = float(row["dial_rate"])
        base = float(team["dial_rate"])
        if _activity_band(own, base) == "alti":
            gap = abs(own - base)
            notes.append(
                (
                    gap,
                    "Aramanın görüşmeye dönmesi ekibin altında "
                    f"({_fmt_rate(own)}, ekip {_fmt_rate(base)}).",
                )
            )
    join_den = int(row.get("meet_n") or 0) + int(row.get("noshow_n") or 0)
    if (
        join_den >= 5
        and row.get("join_rate") is not None
        and team.get("join_rate") is not None
    ):
        own = float(row["join_rate"])
        base = float(team["join_rate"])
        if _activity_band(own, base) == "alti":
            gap = abs(own - base)
            notes.append(
                (
                    gap,
                    "Katılım ekibin altında "
                    f"({_fmt_rate(own)}, ekip {_fmt_rate(base)}).",
                )
            )
    if not notes:
        return None
    notes.sort(key=lambda item: item[0], reverse=True)
    return notes[0][1]


def apply_efficiency_notes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sıra satırlarına bireysel verimlilik yorumu yazar."""
    team = activity_team_baseline(rows)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["yorum"] = efficiency_comment(item, team)
        out.append(item)
    return out


def _outbound_dial_sql(alias: str = "e") -> str:
    """Giden arama. Saat tablosundaki giden arama ile aynı: çevirme."""
    cevirme = is_cevirme_sql(alias)
    return f"""
        {alias}.channel = 'call'
        AND {alias}.direction = 'outbound'
        AND ({cevirme})
    """


def _outbound_talk_sql(alias: str = "e") -> str:
    """Giden ve açılan görüşme. Gelen arama bu orana girmez."""
    temas = is_temas_sql(alias)
    return f"""
        {alias}.channel = 'call'
        AND {alias}.direction = 'outbound'
        AND ({temas})
    """


def _phone_talk_sql(alias: str = "e") -> str:
    """Açılan telefon. Giden temas veya süreli gelen. Cevapsız yok."""
    temas = is_temas_sql(alias)
    inbound = is_answered_inbound_sql(alias)
    return f"""
        {alias}.channel = 'call'
        AND (
            ({alias}.direction = 'outbound' AND ({temas}))
            OR ({inbound})
        )
    """


def _attended_meeting_sql(alias: str = "e") -> str:
    """Katılınan randevu. Katılmayan ve iptal bu süreye girmez."""
    return (
        f"{alias}.channel = 'meeting' "
        f"AND {alias}.meta->>'randevu_durumu' = 'katildi'"
    )


def activity_rank_between(start: date, end: date) -> list[dict[str, Any]]:
    """Satış ekibi, kapalı tarih aralığı. Toplam süreye göre sıra.

    Telefon: giden temas ve süreli gelen. Toplantı: katılınan
    randevunun Zoho Meeting giriş-çıkış süresi. Planlanan aralık
    gerçekleşen süreye girmez.
    """
    from pusula.config import get_org_id
    from pusula.panel_ciro import SALES_TEAM_IDS
    from pusula.panel_data import (
        DateWindow,
        _DUR_E,
        _attended_meet_minutes_sql,
        _bounds,
        _corrupt_actual_meet_sql,
        connect,
    )

    if end < start:
        start, end = end, start
    org_id = get_org_id()
    start_ts, end_ts = _bounds(DateWindow(start, end))
    phone = _phone_talk_sql("e")
    attended = _attended_meeting_sql("e")
    dial = _outbound_dial_sql("e")
    out_talk = _outbound_talk_sql("e")
    meet_minutes = _attended_meet_minutes_sql("e")
    meet_bad = _corrupt_actual_meet_sql("e")
    sql = f"""
        SELECT r.rep_id,
               r.full_name,
               coalesce(sum({_DUR_E}) FILTER (WHERE {phone}), 0)::float
                 AS phone_sec,
               count(*) FILTER (WHERE {phone})::int AS talks,
               count(*) FILTER (WHERE {dial})::int AS dials,
               count(*) FILTER (WHERE {out_talk})::int AS out_talks,
               coalesce(sum({meet_minutes}) FILTER (WHERE {attended}), 0)::float
                 AS meet_min,
               count(*) FILTER (WHERE {attended})::int AS meets,
               count(*) FILTER (
                 WHERE e.channel = 'meeting'
                   AND e.meta->>'randevu_durumu' = 'katilmadi'
               )::int AS noshows,
               count(*) FILTER (WHERE e.channel = 'meeting')::int AS books,
               count(*) FILTER (
                 WHERE {attended} AND ({meet_bad})
               )::int AS meet_err
        FROM unnest(%s::text[]) AS t(rep_id)
        JOIN reps r ON r.org_id = %s AND r.rep_id = t.rep_id
        LEFT JOIN events e
          ON e.org_id = r.org_id
         AND e.rep_id = r.rep_id
         AND e.occurred_at >= %s
         AND e.occurred_at <= %s
         AND e.occurred_at <= now()
        GROUP BY r.rep_id, r.full_name
    """
    with connect() as conn:
        fetched = conn.execute(
            sql,
            (list(SALES_TEAM_IDS), org_id, start_ts, end_ts),
        ).fetchall()
    raw: list[dict[str, Any]] = []
    meet_err = 0
    for (
        rep_id,
        name,
        phone_sec,
        talks,
        dials,
        out_talks,
        meet_min,
        meets,
        noshows,
        books,
        err,
    ) in fetched:
        raw.append(
            {
                "rep_id": str(rep_id),
                "temsilci": str(name),
                "phone_sec": float(phone_sec or 0),
                "meet_min": float(meet_min or 0),
                "talk_n": int(talks or 0),
                "meet_n": int(meets or 0),
                "dial_n": int(dials or 0),
                "out_talk_n": int(out_talks or 0),
                "book_n": int(books or 0),
                "noshow_n": int(noshows or 0),
            }
        )
        meet_err += int(err or 0)
    if meet_err:
        logger.warning(
            "toplantı süresi bozuk: %s kayıt (aralık, katildi)",
            meet_err,
        )
    rows = apply_efficiency_notes(rank_activity_rows(raw))
    for row in rows:
        row["meet_err"] = meet_err
    return rows
