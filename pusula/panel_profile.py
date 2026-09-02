"""Yönetici performans profili — kanıt özeti, öneri yok.

Yalnız satış ekibi. Satış sonrası (Nihat, Burcu) girmez.
Eşik: ekip ortalamasının %20'si (göreli).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pusula.config import get_org_id
from pusula.panel_ciro import (
    SALES_TEAM_IDS,
    ciro_monthly_by_rep,
    ciro_weekly_by_rep,
    ciro_ytd_by_rep,
    fmt_tl,
)
from pusula.panel_data import (
    DateWindow,
    _bounds,
    connect,
    take_rate,
    weekly_series,
)
from pusula.temas import is_cevirme_sql, is_donus_sql, is_temas_sql

_TZ = ZoneInfo("Europe/Istanbul")
REL_THRESHOLD = 0.20
TREND_THRESHOLD = 0.10

# Abdullah — operasyon notu, deals'te yok.
_PORTFOLIO_NOTES: dict[str, str] = {
    "6847331000049858071": (
        "Abdullah, Mehmet Para'nın portföyünü devraldı."
    ),
}

_CEVIRME = is_cevirme_sql("e")
_TEMAS = is_temas_sql("e")
_DONUS = is_donus_sql("e")

MetricDir = Literal["yukseliyor", "sabit", "dusuyor", "veri yetersiz"]


def _ratio(num: int | float | None, den: int | float | None) -> float | None:
    if num is None or den is None:
        return None
    if float(den) == 0:
        return None
    return round(100.0 * float(num) / float(den), 1)


def _rel(value: float | None, team: float | None) -> float | None:
    if value is None or team is None:
        return None
    if abs(float(team)) < 1e-9:
        return None
    return (float(value) - float(team)) / abs(float(team))


def _fmt_metric(key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if key == "satisa_donme_orani":
        return f"%{float(value):.1f}"
    if key in {
        "ulasma_orani",
        "katilim_orani",
        "sonuc_girme_orani",
    }:
        return f"%{round(float(value)):.0f}"
    if key == "ciro":
        return fmt_tl(value)
    if key in {"arama", "randevu"}:
        return str(int(round(float(value))))
    if float(value) == int(value):
        return str(int(value))
    return f"{value:.1f}"


def _label(key: str) -> str:
    return {
        "arama": "Giden arama",
        "ulasma_orani": "Ulaşma oranı",
        "randevu": "Randevu",
        "katilim_orani": "Toplantı katılım oranı",
        "sonuc_girme_orani": "Randevu sonuç girme oranı",
        "ciro": "Ciro",
        "satisa_donme_orani": "Satışa dönme oranı",
    }[key]


# 90 gün operasyon + YTD ciro / mevcut satışa dönme (filtre değişmez).
_COMPARE_KEYS = (
    "arama",
    "ulasma_orani",
    "randevu",
    "katilim_orani",
    "sonuc_girme_orani",
    "ciro",
    "satisa_donme_orani",
)
_TREND_KEYS = (
    "arama",
    "ulasma_orani",
    "randevu",
    "katilim_orani",
    "ciro",
    "satisa_donme_orani",
)


def _ops_90d(window: DateWindow | None = None) -> dict[str, dict[str, Any]]:
    org_id = get_org_id()
    start_ts, end_ts = _bounds(window)
    sql = f"""
        SELECT r.rep_id, r.full_name,
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_CEVIRME}
          )::int AS arama,
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_TEMAS}
          )::int AS ulasilan_giden,
          count(*) FILTER (WHERE {_DONUS})::int AS donus,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
          )::int AS randevu,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'katildi'
          )::int AS katildi,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'sonuc_girilmedi'
          )::int AS sonuc_bos
        FROM unnest(%s::text[]) AS t(rep_id)
        JOIN reps r ON r.org_id = %s AND r.rep_id = t.rep_id
        LEFT JOIN events e
          ON e.org_id = r.org_id AND e.rep_id = r.rep_id
         AND e.occurred_at >= %s
         AND e.occurred_at <= %s
         AND e.occurred_at <= now()
        GROUP BY r.rep_id, r.full_name
    """
    with connect() as conn:
        rows = conn.execute(
            sql, (list(SALES_TEAM_IDS), org_id, start_ts, end_ts)
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for rep_id, name, arama, ulasilan_giden, donus, randevu, katildi, bos in rows:
        arama_n = int(arama or 0)
        ulasilan_n = int(ulasilan_giden or 0) + int(donus or 0)
        randevu_n = int(randevu or 0)
        katildi_n = int(katildi or 0)
        bos_n = int(bos or 0)
        girme_den = randevu_n + bos_n
        out[str(rep_id)] = {
            "rep_id": str(rep_id),
            "temsilci": str(name),
            "arama": float(arama_n),
            "ulasilan": float(ulasilan_n),
            "ulasma_orani": _ratio(ulasilan_n, arama_n),
            "randevu": float(randevu_n),
            "katildi": float(katildi_n),
            "sonuc_bos": float(bos_n),
            "katilim_orani": _ratio(katildi_n, randevu_n),
            "sonuc_girme_orani": _ratio(randevu_n, girme_den),
        }
    return out


def _attach_ciro_and_take(
    by_id: dict[str, dict[str, Any]],
    window: DateWindow | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, float | None]]:
    ytd = {row["rep_id"]: row for row in ciro_ytd_by_rep("sales")}
    take_rows, take_team = take_rate(window)
    take_by_name = {str(r["temsilci"]): r.get("genel") for r in take_rows}
    for _rep_id, row in by_id.items():
        y = ytd.get(_rep_id)
        row["ciro"] = float(y["ciro"]) if y else 0.0
        row["satisa_donme_orani"] = take_by_name.get(str(row["temsilci"]))
    return by_id, take_team


def _team_pooled(
    rows: list[dict[str, Any]], take_team: dict[str, float | None]
) -> dict[str, float | None]:
    """Oranlar havuzlanır (pay/payda), sayılar kişi başı ortalama."""
    n = max(len(rows), 1)
    arama = sum(float(r.get("arama") or 0) for r in rows)
    ulasilan = sum(float(r.get("ulasilan") or 0) for r in rows)
    randevu = sum(float(r.get("randevu") or 0) for r in rows)
    katildi = sum(float(r.get("katildi") or 0) for r in rows)
    bos = sum(float(r.get("sonuc_bos") or 0) for r in rows)
    ciro = sum(float(r.get("ciro") or 0) for r in rows)
    return {
        "arama": arama / n,
        "ulasma_orani": _ratio(ulasilan, arama),
        "randevu": randevu / n,
        "katilim_orani": _ratio(katildi, randevu),
        "sonuc_girme_orani": _ratio(randevu, randevu + bos),
        "ciro": ciro / n,
        "satisa_donme_orani": take_team.get("genel"),
    }


def _strengths_weaknesses(
    row: dict[str, Any], team: dict[str, float | None]
) -> tuple[list[str], list[str], list[str]]:
    strong: list[str] = []
    weak: list[str] = []
    weak_keys: list[str] = []
    for key in _COMPARE_KEYS:
        val = row.get(key)
        avg = team.get(key)
        rel = _rel(
            float(val) if isinstance(val, (int, float)) else None,
            avg,
        )
        if rel is None:
            continue
        val_f = float(val) if isinstance(val, (int, float)) else None
        shown_val = _fmt_metric(key, val_f)
        shown_avg = _fmt_metric(key, avg)
        if shown_val == shown_avg:
            continue
        shown = f"{_label(key)} {shown_val} — ekip ortalaması {shown_avg}"
        if rel >= REL_THRESHOLD:
            strong.append(shown)
        elif rel <= -REL_THRESHOLD:
            weak.append(shown)
            weak_keys.append(key)
    return strong, weak, weak_keys


def _trend_label(last: float | None, prev: float | None) -> MetricDir:
    if last is None or prev is None:
        return "veri yetersiz"
    if abs(float(prev)) < 1e-9:
        if abs(float(last)) < 1e-9:
            return "sabit"
        return "yukseliyor" if last > 0 else "dusuyor"
    change = (float(last) - float(prev)) / abs(float(prev))
    if abs(change) < TREND_THRESHOLD:
        return "sabit"
    return "yukseliyor" if change > 0 else "dusuyor"


def _mean_weeks(rows: list[dict[str, Any]], key: str) -> float | None:
    vals: list[float] = []
    for row in rows:
        v = row.get(key)
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if not vals:
        return None
    return sum(vals) / len(vals)


def _trend_block(rep_id: str, window: DateWindow | None = None) -> list[str]:
    weeks = weekly_series(rep_id, window)
    ciro_w = ciro_weekly_by_rep(rep_id)
    ciro_by_week = {
        w["hafta"]: w["ciro"] for w in ciro_w if isinstance(w.get("hafta"), datetime)
    }
    for row in weeks:
        row["ciro"] = ciro_by_week.get(row["hafta"])
        row["satisa_donme_orani"] = row.get("take_genel")
    # Son hafta (içinde bulunulan) yarım kalabilir; onu dışarıda bırak.
    complete = weeks[:-1] if len(weeks) > 1 else weeks
    last4 = complete[-4:]
    prev4 = complete[-8:-4]
    lines: list[str] = []
    direction_tr = {
        "yukseliyor": "yükseliyor",
        "sabit": "sabit",
        "dusuyor": "düşüyor",
        "veri yetersiz": "veri yetersiz",
    }
    for key in _TREND_KEYS:
        src = "arama_ham" if key == "arama" else key
        last = _mean_weeks(last4, src)
        prev = _mean_weeks(prev4, src)
        if key == "arama":
            last = _mean_weeks(last4, "arama_ham")
            prev = _mean_weeks(prev4, "arama_ham")
        direction = _trend_label(last, prev)
        lines.append(f"{_label(key)}: {direction_tr[direction]}")
    return lines


def _monthly_ops() -> dict[str, dict[datetime, dict[str, Any]]]:
    """Ay → temsilci metrikleri (süreklilik)."""
    org_id = get_org_id()
    sql = f"""
        WITH months AS (
            SELECT generate_series(
                date_trunc(
                    'month',
                    (now() AT TIME ZONE 'Europe/Istanbul')
                ) - interval '5 months',
                date_trunc('month', (now() AT TIME ZONE 'Europe/Istanbul')),
                interval '1 month'
            ) AS month_start
        )
        SELECT r.rep_id,
          months.month_start,
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_CEVIRME}
          )::int AS arama,
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_TEMAS}
          )::int AS ulasilan_giden,
          count(*) FILTER (WHERE {_DONUS})::int AS donus,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
          )::int AS randevu,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'katildi'
          )::int AS katildi,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'sonuc_girilmedi'
          )::int AS sonuc_bos
        FROM months
        CROSS JOIN unnest(%s::text[]) AS t(rep_id)
        JOIN reps r ON r.org_id = %s AND r.rep_id = t.rep_id
        LEFT JOIN events e
          ON e.org_id = r.org_id AND e.rep_id = r.rep_id
         AND date_trunc(
               'month', e.occurred_at AT TIME ZONE 'Europe/Istanbul'
             ) = months.month_start
         AND e.occurred_at <= now()
        GROUP BY r.rep_id, months.month_start
        ORDER BY months.month_start
    """
    with connect() as conn:
        rows = conn.execute(sql, (list(SALES_TEAM_IDS), org_id)).fetchall()
    by_rep: dict[str, dict[datetime, dict[str, Any]]] = {}
    for rep_id, month, arama, ulasilan_giden, donus, randevu, katildi, bos in rows:
        month_dt = month if isinstance(month, datetime) else None
        if month_dt is None:
            continue
        arama_n = int(arama or 0)
        randevu_n = int(randevu or 0)
        bos_n = int(bos or 0)
        by_rep.setdefault(str(rep_id), {})[month_dt] = {
            "arama": float(arama_n),
            "ulasma_orani": _ratio(int(ulasilan_giden or 0) + int(donus or 0), arama_n),
            "randevu": float(randevu_n),
            "katilim_orani": _ratio(int(katildi or 0), randevu_n),
            "sonuc_girme_orani": _ratio(randevu_n, randevu_n + bos_n),
        }
    ciro_rows = ciro_monthly_by_rep("sales")
    for row in ciro_rows:
        rid = str(row["rep_id"])
        month = row.get("ay")
        if not isinstance(month, datetime):
            continue
        by_rep.setdefault(rid, {}).setdefault(month, {})
        by_rep[rid][month]["ciro"] = float(row["ciro"])
    return by_rep


def _continuity(
    rep_id: str,
    weak_keys: list[str],
    monthly: dict[str, dict[datetime, dict[str, Any]]],
) -> list[str]:
    if not weak_keys:
        return ["zayıf yön yok"]
    months = sorted({m for by_m in monthly.values() for m in by_m})
    if len(months) >= 2:
        months = months[:-1]
    if not months:
        return ["veri yetersiz"]
    lines: list[str] = []
    for key in weak_keys:
        streak = 0
        for month in reversed(months):
            values = []
            for rid, by_m in monthly.items():
                cell = by_m.get(month, {})
                v = cell.get(key)
                if isinstance(v, (int, float)):
                    values.append((rid, float(v)))
            if not values:
                break
            team = sum(v for _, v in values) / len(values)
            mine = next((v for rid, v in values if rid == rep_id), None)
            rel = _rel(mine, team)
            if rel is not None and rel <= -REL_THRESHOLD:
                streak += 1
            else:
                break
        label = _label(key)
        if streak <= 0:
            lines.append(f"{label}: veri yetersiz")
        elif streak == 1:
            lines.append(f"{label}: yeni başladı")
        else:
            lines.append(f"{label}: {streak} aydır")
    return lines


def _first_call_at(rep_id: str) -> datetime | None:
    org_id = get_org_id()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT min(occurred_at)
            FROM events
            WHERE org_id = %s AND rep_id = %s AND channel = 'call'
            """,
            (org_id, rep_id),
        ).fetchone()
    if not row or row[0] is None:
        return None
    value = row[0]
    return value if isinstance(value, datetime) else None


def _coverage(rep_id: str, _name: str) -> list[str]:
    notes: list[str] = []
    first_call = _first_call_at(rep_id)
    if first_call is not None:
        local = first_call.astimezone(_TZ)
        notes.append(f"İlk arama kaydı {local.strftime('%d.%m.%Y')}.")
    org_id = get_org_id()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT min(occurred_at)
            FROM events
            WHERE org_id = %s AND rep_id = %s AND channel = 'meeting'
            """,
            (org_id, rep_id),
        ).fetchone()
    if row and row[0] is not None and isinstance(row[0], datetime):
        local = row[0].astimezone(_TZ)
        notes.append(
            f"Bookings verisi {local.day} {_month_tr(local.month)} "
            f"{local.year} tarihinden itibaren."
        )
    extra = _PORTFOLIO_NOTES.get(rep_id)
    if extra:
        notes.append(extra)
    notes.append(
        "Olay ingest'i Temmuz 2026'da seyrek (164 kayıt), "
        "Ağustos 2026'da toplu yazım var. Bu kesinti önceki ayları eksik bırakır."
    )
    return notes


def _month_tr(month: int) -> str:
    names = (
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
    return names[month - 1]


def performance_profiles(
    window: DateWindow | None = None,
) -> list[dict[str, Any]]:
    """Satış ekibi kartları. Sıra ekip tanımı sırası, puan yok."""
    by_id, take_team = _attach_ciro_and_take(_ops_90d(window), window)
    rows = [by_id[rid] for rid in SALES_TEAM_IDS if rid in by_id]
    team = _team_pooled(rows, take_team)
    monthly = _monthly_ops()
    cards: list[dict[str, Any]] = []
    for row in rows:
        strong, weak, weak_keys = _strengths_weaknesses(row, team)
        cards.append(
            {
                "rep_id": row["rep_id"],
                "temsilci": row["temsilci"],
                "guclu": strong,
                "zayif": weak,
                "egri": _trend_block(row["rep_id"], window),
                "sureklilik": _continuity(row["rep_id"], weak_keys, monthly),
                "kapsam": _coverage(row["rep_id"], str(row["temsilci"])),
            }
        )
    return cards


PROFILE_WARNING = (
    "Bu bölüm karar önerisi değil, kanıt özetidir. Veri "
    "penceresi kısadır ve ingest kesintileri yaşanmıştır. "
    "Dürtü sistemi henüz gölge modda olduğu için hiçbir "
    "temsilciye koçluk müdahalesi uygulanmamıştır; davranış "
    "değişimi ölçümü için müdahale sonrası veri gereklidir."
)
