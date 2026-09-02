"""Streamlit paneli için salt-okunur metrik sorguları.

Temas / çevirme / faaliyet: pusula.temas. Dönüşüm evreni 1 Mayıs 2026
sonrası, Mevcut Müşteri hariç. Operasyon kıyası son 90 gün.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from pusula.blocks import BLOK_DISI, PLANNED_BLOCKS, hour_in_planned_sql
from pusula.config import get_org_id
from pusula.sifir_satis import WON_STAGE, won_stage_sql
from pusula.temas import (
    distinct_attempted_leads_sql,
    distinct_reached_leads_sql,
    duration_sec,
    is_attempt_sql,
    is_cevirme_sql,
    is_donus_sql,
    is_gelen_sql,
    is_temas_sql,
    lead_reach_split_agg_sql,
    lead_reach_thread_flags_sql,
)

_TZ = ZoneInfo("Europe/Istanbul")
WINDOW_DAYS = 90
WEEK_COUNT = 12
CONV_START = datetime(2026, 5, 1, tzinfo=_TZ)
MEVCUT_MUSTERI = "Mevcut Müşteri"
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
_WEEKDAYS = (
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
)

# İş yükü süre varsayımları — tek yer.
TOPLANTI_DK = 30.0
CRM_DK_PER_GORUSME = 1.5
OLU_ZAMAN_SN = 20.0
GUN_SAAT = 8.0
DEFAULT_ARAMA_PER_LEAD = 3.0
DEFAULT_TOPLANTI_GUN = 6.0
# Eski ad: panel import kırılmasın.
CRM_DK_PER_ARAMA = CRM_DK_PER_GORUSME
TOPLANTI_DK_VARSAYILAN = TOPLANTI_DK

_FUNNEL_STATUSES = (
    "1.Arama-Ulaşılamadı",
    "2.Arama-Ulaşılamadı",
    "Aging",
)
FUNNEL_DROPPED_STATUS = "3.Arama-Ulaşılamadı"

_SOURCE_FORM = "Contact Form"
_SOURCE_REGISTER = "Register"

_TEMAS_E = is_temas_sql("e")
_CEVIRME_E = is_cevirme_sql("e")
_ATTEMPT_E = is_attempt_sql("e")
_DONUS_E = is_donus_sql("e")
_GELEN_E = is_gelen_sql("e")
_DUR_E = duration_sec("e")
_WON_D = won_stage_sql("d")
_LEAD_PAYDA_E = distinct_attempted_leads_sql("e")
_LEAD_PAY_E = distinct_reached_leads_sql("e")
_DAY_IST = "(e.occurred_at AT TIME ZONE 'Europe/Istanbul')::date"


def _reach_pay(ulasilan_giden: int, donus: int) -> int:
    """Ulaşma payı: giden temas + dönüş. Payda giden arama kalır."""
    return int(ulasilan_giden) + int(donus)


@dataclass(frozen=True)
class Rep:
    rep_id: str
    full_name: str


@dataclass(frozen=True)
class DateWindow:
    """Istanbul takvim günü, her iki uç dahil."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days


def default_window() -> DateWindow:
    end = datetime.now(_TZ).date()
    return DateWindow(start=end - timedelta(days=WINDOW_DAYS), end=end)


def fmt_day(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}, {_WEEKDAYS[day.weekday()]}"


def fmt_window(window: DateWindow) -> str:
    """'1 Haziran - 1 Eylül 2026 (90 gün)' — pencereden üretilir."""
    start, end = window.start, window.end
    left = f"{start.day} {_MONTHS[start.month - 1]}"
    if start.year != end.year:
        left = f"{left} {start.year}"
    right = f"{end.day} {_MONTHS[end.month - 1]} {end.year}"
    return f"{left} - {right} ({window.days} gün)"


def _bounds(window: DateWindow | None = None) -> tuple[datetime, datetime]:
    """[start 00:00, end+1 00:00) Istanbul; bitiş now()'u aşmaz."""
    w = window or default_window()
    start_ts = datetime.combine(w.start, time.min, tzinfo=_TZ)
    end_ts = datetime.combine(w.end + timedelta(days=1), time.min, tzinfo=_TZ)
    now = datetime.now(_TZ)
    if end_ts > now:
        end_ts = now
    return start_ts, end_ts


def _conv_bounds(window: DateWindow | None = None) -> tuple[datetime, datetime]:
    start_ts, end_ts = _bounds(window)
    if start_ts < CONV_START:
        start_ts = CONV_START
    return start_ts, end_ts


def conv_window(window: DateWindow | None = None) -> DateWindow:
    start_ts, end_ts = _conv_bounds(window)
    end_day = (end_ts - timedelta(seconds=1)).date()
    return DateWindow(start=start_ts.date(), end=end_day)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_POOLED / DATABASE_URL yok")
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(_database_url(), prepare_threshold=None)


def _sales_rep_sql(alias: str = "r") -> str:
    return f"{alias}.category = 'sales' AND {alias}.active = true"


def _rep_filter(
    alias: str, rep_id: str | None, column: str = "rep_id"
) -> tuple[str, list[Any]]:
    if not rep_id:
        return "", []
    return f" AND {alias}.{column} = %s ", [rep_id]


def load_reps() -> list[Rep]:
    org_id = get_org_id()
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT rep_id, full_name
            FROM reps r
            WHERE r.org_id = %s AND {_sales_rep_sql()}
            ORDER BY r.full_name
            """,
            (org_id,),
        ).fetchall()
    return [Rep(str(r[0]), str(r[1])) for r in rows]


def load_rep_by_email(email: str) -> Rep | None:
    """Aktif satis temsilcisini e-posta ile bulur. Yoksa None."""
    normalized = email.strip().lower()
    if not normalized:
        return None
    org_id = get_org_id()
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT rep_id, full_name
            FROM reps r
            WHERE r.org_id = %s
              AND lower(trim(r.email)) = %s
              AND {_sales_rep_sql()}
            """,
            (org_id, normalized),
        ).fetchone()
    if row is None:
        return None
    return Rep(str(row[0]), str(row[1]))


def _hour_expr(alias: str = "e") -> str:
    return f"extract(hour FROM {alias}.occurred_at AT TIME ZONE 'Europe/Istanbul')::int"


def hourly_table(
    rep_id: str | None, window: DateWindow | None = None
) -> list[dict[str, Any]]:
    """Saat, giden arama, ulaşılan, dönüş, gelen, randevu, katıldı."""
    org_id = get_org_id()
    extra, params = _rep_filter("e", rep_id)
    hour = _hour_expr("e")
    start_ts, end_ts = _bounds(window)
    sql = f"""
        WITH hours AS (
            SELECT h FROM generate_series(9, 18) AS h
        ),
        calls AS (
            SELECT
              {hour} AS saat,
              count(*) FILTER (
                WHERE e.direction = 'outbound' AND {_CEVIRME_E}
              )::int AS arama,
              count(*) FILTER (
                WHERE e.direction = 'outbound' AND {_TEMAS_E}
              )::int AS ulasilan_giden,
              count(*) FILTER (WHERE {_DONUS_E})::int AS donus,
              count(*) FILTER (WHERE {_GELEN_E})::int AS gelen,
              {_LEAD_PAYDA_E}::int AS lead_payda,
              {_LEAD_PAY_E}::int AS lead_pay
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND {_sales_rep_sql()}
              AND e.channel = 'call'
              AND e.occurred_at >= %s
              AND e.occurred_at <= %s
              AND e.occurred_at <= now()
              {extra}
            GROUP BY 1
        ),
        meetings AS (
            SELECT
              {hour} AS saat,
              count(*) FILTER (
                WHERE e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
              )::int AS randevu,
              count(*) FILTER (
                WHERE e.meta->>'randevu_durumu' = 'katildi'
              )::int AS katildi
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND {_sales_rep_sql()}
              AND e.channel = 'meeting'
              AND e.occurred_at >= %s
              AND e.occurred_at <= %s
              AND e.occurred_at <= now()
              {extra}
            GROUP BY 1
        )
        SELECT
          hours.h AS saat,
          coalesce(c.arama, 0) AS arama,
          coalesce(c.ulasilan_giden, 0) AS ulasilan_giden,
          coalesce(c.donus, 0) AS donus,
          coalesce(c.gelen, 0) AS gelen,
          coalesce(c.lead_payda, 0) AS lead_payda,
          coalesce(c.lead_pay, 0) AS lead_pay,
          coalesce(m.randevu, 0) AS randevu,
          coalesce(m.katildi, 0) AS katildi
        FROM hours
        LEFT JOIN calls c ON c.saat = hours.h
        LEFT JOIN meetings m ON m.saat = hours.h
        ORDER BY hours.h
    """
    with connect() as conn:
        rows = conn.execute(
            sql,
            (org_id, start_ts, end_ts, *params, org_id, start_ts, end_ts, *params),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for saat, arama, ulasilan_giden, donus, gelen, lead_payda, lead_pay, randevu, katildi in rows:
        arama_n = int(arama)
        donus_n = int(donus)
        ulasilan_n = _reach_pay(int(ulasilan_giden), donus_n)
        out.append(
            {
                "saat": f"{int(saat):02d}:00",
                "arama": arama_n,
                "donus": donus_n,
                "gelen": int(gelen),
                "ulasilan": ulasilan_n,
                "ulasma_orani": _ratio(int(lead_pay), int(lead_payda)),
                "randevu": int(randevu),
                "katildi": int(katildi),
                "katilim_orani": _ratio(katildi, randevu),
            }
        )
    return out


def team_reach_and_join(
    window: DateWindow | None = None,
) -> dict[str, float | None]:
    """Ekip geneli ulaşma (lead, tüm gün) ve katılım (saatlik 09–18)."""
    rows = hourly_table(None, window)
    arama = sum(r["arama"] for r in rows)
    ulasilan = sum(r["ulasilan"] for r in rows)
    donus = sum(r["donus"] for r in rows)
    gelen = sum(r["gelen"] for r in rows)
    randevu = sum(r["randevu"] for r in rows)
    katildi = sum(r["katildi"] for r in rows)
    payda, pay = _lead_reach_counts(None, window)
    return {
        "ulasma_orani": _ratio(pay, payda),
        "katilim_orani": _ratio(katildi, randevu),
        "arama": arama,
        "ulasilan": ulasilan,
        "donus": donus,
        "gelen": gelen,
        "randevu": randevu,
        "katildi": katildi,
        "lead_payda": payda,
        "lead_pay": pay,
    }


def _lead_reach_counts(
    rep_id: str | None, window: DateWindow | None = None
) -> tuple[int, int]:
    """Dönem içi benzersiz giden-lead ve ulaşılan-lead."""
    org_id = get_org_id()
    extra, params = _rep_filter("e", rep_id)
    start_ts, end_ts = _bounds(window)
    sql = f"""
        SELECT
          {_LEAD_PAYDA_E}::int AS payda,
          {_LEAD_PAY_E}::int AS pay
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND {_sales_rep_sql()}
          AND e.channel = 'call'
          AND e.occurred_at >= %s
          AND e.occurred_at <= %s
          AND e.occurred_at <= now()
          {extra}
    """
    with connect() as conn:
        row = conn.execute(sql, (org_id, start_ts, end_ts, *params)).fetchone()
    return (int(row[0] or 0) if row else 0, int(row[1] or 0) if row else 0)


def _reach_break_row(
    temsilci: str | None,
    aranan: int,
    giden_temas: int,
    donusle_gelen: int,
    ulasilan: int,
) -> dict[str, Any]:
    return {
        "temsilci": temsilci,
        "aranan": int(aranan),
        "giden_temas": int(giden_temas),
        "donusle_gelen": int(donusle_gelen),
        "ulasilan": int(ulasilan),
        "oran": _ratio(ulasilan, aranan),
    }


def _reach_break_from_events(
    window: DateWindow | None,
    *,
    rep_id: str | None,
    by_rep: bool,
) -> list[tuple[Any, ...]]:
    """Thread bayraklarını temsilci veya ekip düzeyinde toplar."""
    org_id = get_org_id()
    extra, params = _rep_filter("e", None if by_rep else rep_id)
    start_ts, end_ts = _bounds(window)
    flags = lead_reach_thread_flags_sql("e")
    agg = lead_reach_split_agg_sql("t")
    if by_rep:
        sql = f"""
            WITH flags AS (
                SELECT e.rep_id, r.full_name, e.thread_id,
                  {flags}
                FROM events e
                JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
                WHERE e.org_id = %s
                  AND {_sales_rep_sql()}
                  AND e.channel = 'call'
                  AND e.occurred_at >= %s
                  AND e.occurred_at <= %s
                  AND e.occurred_at <= now()
                  AND e.thread_id IS NOT NULL
                  {extra}
                GROUP BY e.rep_id, r.full_name, e.thread_id
            )
            SELECT t.full_name, {agg}
            FROM flags t
            GROUP BY t.full_name
            ORDER BY t.full_name
        """
    else:
        sql = f"""
            WITH flags AS (
                SELECT e.thread_id,
                  {flags}
                FROM events e
                JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
                WHERE e.org_id = %s
                  AND {_sales_rep_sql()}
                  AND e.channel = 'call'
                  AND e.occurred_at >= %s
                  AND e.occurred_at <= %s
                  AND e.occurred_at <= now()
                  AND e.thread_id IS NOT NULL
                  {extra}
                GROUP BY e.thread_id
            )
            SELECT {agg}
            FROM flags t
        """
    with connect() as conn:
        return list(conn.execute(sql, (org_id, start_ts, end_ts, *params)).fetchall())


def lead_reach_breakdown(
    window: DateWindow | None = None,
    *,
    rep_id: str | None = None,
    by_rep: bool = False,
) -> list[dict[str, Any]]:
    """Ulaşma kırılımı: aranan, giden temas, dönüşle gelen, ulaşılan.

    by_rep: temsilci satırları + ekip (ekip benzersiz thread, toplam değil).
    rep_id ve by_rep yok: isimsiz ekip satırı.
    """
    if by_rep:
        raw = _reach_break_from_events(window, rep_id=None, by_rep=True)
        by_name = {
            str(name): _reach_break_row(
                str(name), int(a or 0), int(g or 0), int(d or 0), int(u or 0)
            )
            for name, a, g, d, u in raw
        }
        out: list[dict[str, Any]] = []
        for rep in load_reps():
            out.append(
                by_name.get(
                    rep.full_name,
                    _reach_break_row(rep.full_name, 0, 0, 0, 0),
                )
            )
        team = _reach_break_from_events(window, rep_id=None, by_rep=False)
        if team:
            a, g, d, u = team[0]
            out.append(
                _reach_break_row("ekip", int(a or 0), int(g or 0), int(d or 0), int(u or 0))
            )
        else:
            out.append(_reach_break_row("ekip", 0, 0, 0, 0))
        return out

    raw = _reach_break_from_events(window, rep_id=rep_id, by_rep=False)
    if not raw:
        name = None
        if rep_id:
            names = {r.rep_id: r.full_name for r in load_reps()}
            name = names.get(rep_id)
        return [_reach_break_row(name, 0, 0, 0, 0)]
    a, g, d, u = raw[0]
    name = None
    if rep_id:
        names = {r.rep_id: r.full_name for r in load_reps()}
        name = names.get(rep_id)
    return [_reach_break_row(name, int(a or 0), int(g or 0), int(d or 0), int(u or 0))]


def _hist_weekdays(before: date | None = None) -> int:
    """Seçilen günden önceki 90 gün, pazartesi–cuma."""
    gun = before or datetime.now(_TZ).date()
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT count(*)::int
            FROM generate_series(
                %s::date - interval '{WINDOW_DAYS} days',
                %s::date - interval '1 day',
                interval '1 day'
            ) AS d
            WHERE extract(isodow FROM d) < 6
            """,
            (gun, gun),
        ).fetchone()
    return int(row[0]) if row else WINDOW_DAYS


def today_arama_count(rep_id: str | None = None) -> int:
    """Bugün Istanbul, satış outbound bağlı arama."""
    org_id = get_org_id()
    extra, params = _rep_filter("e", rep_id)
    sql = f"""
        SELECT count(*)::int
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND {_sales_rep_sql()}
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND {_CEVIRME_E}
          AND (e.occurred_at AT TIME ZONE 'Europe/Istanbul')::date
                = (now() AT TIME ZONE 'Europe/Istanbul')::date
          {extra}
    """
    with connect() as conn:
        row = conn.execute(sql, (org_id, *params)).fetchone()
    return int(row[0]) if row else 0


def today_blocks(
    rep_id: str | None, day: date | None = None
) -> dict[str, Any]:
    """Seçilen günün dört bloğu + 90 günlük aynı blok ortalaması."""
    org_id = get_org_id()
    extra, params = _rep_filter("e", rep_id)
    hour = _hour_expr("e")
    chosen = day or datetime.now(_TZ).date()
    day_col = "(e.occurred_at AT TIME ZONE 'Europe/Istanbul')::date"
    today = f"{day_col} = p.gun"
    hist = f"""
        {day_col} >= p.gun - interval '{WINDOW_DAYS} days'
        AND {day_col} < p.gun
        AND extract(isodow FROM {day_col}) < 6
    """
    is_call = "e.channel = 'call' AND e.direction = 'outbound'"
    is_meet = "e.channel = 'meeting'"
    planned = hour_in_planned_sql(hour)

    def _append_sure(prefix: str, window_sql: str, slot_sql: str) -> None:
        # Sure yalniz ulasilan gorusme; temas tanimi temas.py.
        cond = f"{window_sql} AND {slot_sql} AND {is_call} AND {_TEMAS_E}"
        select_parts.append(
            f"sum({_DUR_E}) FILTER (WHERE {cond}) AS {prefix}_sure_toplam"
        )
        select_parts.append(
            f"avg({_DUR_E}) FILTER (WHERE {cond}) AS {prefix}_sure_ort"
        )
        select_parts.append(
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DUR_E}) "
            f"FILTER (WHERE {cond}) AS {prefix}_sure_tipik"
        )

    select_parts: list[str] = []
    for block in PLANNED_BLOCKS:
        rng = f"{hour} >= {block.start_hour} AND {hour} < {block.end_hour}"
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng} AND {is_call}"
            f" AND {_CEVIRME_E})::int AS t_{block.key}_arama"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng} AND {is_call}"
            f" AND {_TEMAS_E})::int AS t_{block.key}_ulasilan"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng}"
            f" AND {_DONUS_E})::int AS t_{block.key}_donus"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng}"
            f" AND {_GELEN_E})::int AS t_{block.key}_gelen"
        )
        select_parts.append(
            f"{distinct_attempted_leads_sql('e', extra=f'{today} AND {rng}')}"
            f"::int AS t_{block.key}_lead_payda"
        )
        select_parts.append(
            f"{distinct_reached_leads_sql('e', extra=f'{today} AND {rng}')}"
            f"::int AS t_{block.key}_lead_pay"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng} AND {is_meet})"
            f"::int AS t_{block.key}_randevu"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'katildi')"
            f"::int AS t_{block.key}_katildi"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'katilmadi')"
            f"::int AS t_{block.key}_katilmadi"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {today} AND {rng} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'sonuc_girilmedi')"
            f"::int AS t_{block.key}_sonuc"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng} AND {is_call}"
            f" AND {_CEVIRME_E})::int AS h_{block.key}_arama"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng} AND {is_call}"
            f" AND {_TEMAS_E})::int AS h_{block.key}_ulasilan"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng}"
            f" AND {_DONUS_E})::int AS h_{block.key}_donus"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng}"
            f" AND {_GELEN_E})::int AS h_{block.key}_gelen"
        )
        select_parts.append(
            f"{distinct_attempted_leads_sql('e', day_expr=_DAY_IST, extra=f'{hist} AND {rng}')}"
            f"::int AS h_{block.key}_lead_payda"
        )
        select_parts.append(
            f"{distinct_reached_leads_sql('e', day_expr=_DAY_IST, extra=f'{hist} AND {rng}')}"
            f"::int AS h_{block.key}_lead_pay"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng} AND {is_meet})"
            f"::int AS h_{block.key}_randevu"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'katildi')"
            f"::int AS h_{block.key}_katildi"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'katilmadi')"
            f"::int AS h_{block.key}_katilmadi"
        )
        select_parts.append(
            f"count(*) FILTER (WHERE {hist} AND {rng} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'sonuc_girilmedi')"
            f"::int AS h_{block.key}_sonuc"
        )
        _append_sure(f"t_{block.key}", today, rng)
        _append_sure(f"h_{block.key}", hist, rng)

    disi = f"NOT {planned}"
    select_parts.extend(
        [
            f"count(*) FILTER (WHERE {today} AND {disi} AND {is_call}"
            f" AND {_CEVIRME_E})::int AS t_disi_arama",
            f"count(*) FILTER (WHERE {today} AND {disi} AND {is_call}"
            f" AND {_TEMAS_E})::int AS t_disi_ulasilan",
            f"count(*) FILTER (WHERE {today} AND {disi}"
            f" AND {_DONUS_E})::int AS t_disi_donus",
            f"count(*) FILTER (WHERE {today} AND {disi}"
            f" AND {_GELEN_E})::int AS t_disi_gelen",
            f"{distinct_attempted_leads_sql('e', extra=f'{today} AND {disi}')}"
            f"::int AS t_disi_lead_payda",
            f"{distinct_reached_leads_sql('e', extra=f'{today} AND {disi}')}"
            f"::int AS t_disi_lead_pay",
            f"count(*) FILTER (WHERE {today} AND {disi} AND {is_meet})"
            f"::int AS t_disi_randevu",
            f"count(*) FILTER (WHERE {today} AND {disi} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'katildi')"
            f"::int AS t_disi_katildi",
            f"count(*) FILTER (WHERE {hist} AND {disi} AND {is_call}"
            f" AND {_CEVIRME_E})::int AS h_disi_arama",
            f"count(*) FILTER (WHERE {hist} AND {disi} AND {is_call}"
            f" AND {_TEMAS_E})::int AS h_disi_ulasilan",
            f"count(*) FILTER (WHERE {hist} AND {disi}"
            f" AND {_DONUS_E})::int AS h_disi_donus",
            f"count(*) FILTER (WHERE {hist} AND {disi}"
            f" AND {_GELEN_E})::int AS h_disi_gelen",
            f"{distinct_attempted_leads_sql('e', day_expr=_DAY_IST, extra=f'{hist} AND {disi}')}"
            f"::int AS h_disi_lead_payda",
            f"{distinct_reached_leads_sql('e', day_expr=_DAY_IST, extra=f'{hist} AND {disi}')}"
            f"::int AS h_disi_lead_pay",
            f"count(*) FILTER (WHERE {hist} AND {disi} AND {is_meet})"
            f"::int AS h_disi_randevu",
            f"count(*) FILTER (WHERE {hist} AND {disi} AND {is_meet}"
            f" AND e.meta->>'randevu_durumu' = 'katildi')"
            f"::int AS h_disi_katildi",
        ]
    )
    _append_sure("t_disi", today, disi)
    _append_sure("h_disi", hist, disi)
    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        CROSS JOIN (SELECT %s::date AS gun) p
        WHERE e.org_id = %s
          AND {_sales_rep_sql()}
          AND (e.occurred_at AT TIME ZONE 'Europe/Istanbul')::date
                >= p.gun - interval '{WINDOW_DAYS} days'
          AND e.occurred_at <= now()
          {extra}
    """
    days = max(_hist_weekdays(chosen), 1)
    with connect() as conn:
        cur = conn.execute(sql, (chosen, org_id, *params))
        row = cur.fetchone()
        colnames = [str(col.name) for col in (cur.description or [])]
    packed: dict[str, Any] = {}
    for i, name in enumerate(colnames):
        raw = row[i] if row else None
        if name.endswith(("_sure_toplam", "_sure_ort", "_sure_tipik")):
            packed[name] = float(raw) if raw is not None else None
        else:
            packed[name] = int(raw or 0)

    def _avg(total: int | float) -> float:
        return round(float(total) / float(days), 1)

    def _sure_pair(
        prefix: str, ulasilan: int, *, daily_total: bool
    ) -> dict[str, float | None]:
        if ulasilan == 0:
            return {
                "sure_toplam": None,
                "sure_ort": None,
                "sure_tipik": None,
            }
        toplam = packed.get(f"{prefix}_sure_toplam")
        if daily_total and toplam is not None:
            toplam = _avg(toplam)
        ort = packed.get(f"{prefix}_sure_ort")
        tipik = packed.get(f"{prefix}_sure_tipik")
        return {
            "sure_toplam": float(toplam) if toplam is not None else None,
            "sure_ort": float(ort) if ort is not None else None,
            "sure_tipik": float(tipik) if tipik is not None else None,
        }

    blocks: list[dict[str, Any]] = []
    for block in PLANNED_BLOCKS:
        t_a = packed[f"t_{block.key}_arama"]
        t_u = packed[f"t_{block.key}_ulasilan"]
        t_d = packed[f"t_{block.key}_donus"]
        t_g = packed[f"t_{block.key}_gelen"]
        t_lp = packed[f"t_{block.key}_lead_pay"]
        t_ld = packed[f"t_{block.key}_lead_payda"]
        t_r = packed[f"t_{block.key}_randevu"]
        t_k = packed[f"t_{block.key}_katildi"]
        t_m = packed[f"t_{block.key}_katilmadi"]
        t_s = packed[f"t_{block.key}_sonuc"]
        h_a = packed[f"h_{block.key}_arama"]
        h_u = packed[f"h_{block.key}_ulasilan"]
        h_d = packed[f"h_{block.key}_donus"]
        h_g = packed[f"h_{block.key}_gelen"]
        h_lp = packed[f"h_{block.key}_lead_pay"]
        h_ld = packed[f"h_{block.key}_lead_payda"]
        h_r = packed[f"h_{block.key}_randevu"]
        h_k = packed[f"h_{block.key}_katildi"]
        h_m = packed[f"h_{block.key}_katilmadi"]
        h_s = packed[f"h_{block.key}_sonuc"]
        t_pay = _reach_pay(t_u, t_d)
        h_pay = _reach_pay(h_u, h_d)
        blocks.append(
            {
                "key": block.key,
                "label": block.label,
                "kind": block.kind,
                "today": {
                    "arama": t_a,
                    "donus": t_d,
                    "gelen": t_g,
                    "ulasilan": t_pay,
                    "ulasma_orani": _ratio(t_lp, t_ld),
                    "randevu": t_r,
                    "katildi": t_k,
                    "katilmadi": t_m,
                    "sonuc_girilmedi": t_s,
                    **_sure_pair(f"t_{block.key}", t_u, daily_total=False),
                },
                "avg90": {
                    "arama": _avg(h_a),
                    "donus": _avg(h_d),
                    "gelen": _avg(h_g),
                    "ulasilan": _avg(h_pay),
                    "ulasma_orani": _ratio(h_lp, h_ld),
                    "randevu": _avg(h_r),
                    "katildi": _avg(h_k),
                    "katilmadi": _avg(h_m),
                    "sonuc_girilmedi": _avg(h_s),
                    **_sure_pair(f"h_{block.key}", h_u, daily_total=True),
                },
            }
        )

    t_da = packed["t_disi_arama"]
    t_du = packed["t_disi_ulasilan"]
    t_dd = packed["t_disi_donus"]
    t_dg = packed["t_disi_gelen"]
    t_dlp = packed["t_disi_lead_pay"]
    t_dld = packed["t_disi_lead_payda"]
    t_dr = packed["t_disi_randevu"]
    t_dk = packed["t_disi_katildi"]
    h_da = packed["h_disi_arama"]
    h_du = packed["h_disi_ulasilan"]
    h_dd = packed["h_disi_donus"]
    h_dg = packed["h_disi_gelen"]
    h_dlp = packed["h_disi_lead_pay"]
    h_dld = packed["h_disi_lead_payda"]
    h_dr = packed["h_disi_randevu"]
    h_dk = packed["h_disi_katildi"]
    t_disi_pay = _reach_pay(t_du, t_dd)
    h_disi_pay = _reach_pay(h_du, h_dd)
    blocks.append(
        {
            "key": BLOK_DISI.key,
            "label": BLOK_DISI.label,
            "kind": BLOK_DISI.kind,
            "today": {
                "arama": t_da,
                "donus": t_dd,
                "gelen": t_dg,
                "ulasilan": t_disi_pay,
                "ulasma_orani": _ratio(t_dlp, t_dld),
                "randevu": t_dr,
                "katildi": t_dk,
                **_sure_pair("t_disi", t_du, daily_total=False),
            },
            "avg90": {
                "arama": _avg(h_da),
                "donus": _avg(h_dd),
                "gelen": _avg(h_dg),
                "ulasilan": _avg(h_disi_pay),
                "ulasma_orani": _ratio(h_dlp, h_dld),
                "randevu": _avg(h_dr),
                "katildi": _avg(h_dk),
                **_sure_pair("h_disi", h_du, daily_total=True),
            },
        }
    )
    return {"blocks": blocks, "workdays": days}


def _workdays() -> int:
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT count(*)::int
            FROM generate_series(
                (now() AT TIME ZONE 'Europe/Istanbul')::date
                  - interval '{WINDOW_DAYS} days',
                (now() AT TIME ZONE 'Europe/Istanbul')::date,
                interval '1 day'
            ) AS d
            WHERE extract(isodow FROM d) < 6
            """
        ).fetchone()
    return int(row[0]) if row else WINDOW_DAYS


def daily_workload() -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    """Kişi başı günlük iş yükü + ulaşılamayan ort. / ulaşılan medyan süre."""
    org_id = get_org_id()
    days = max(_workdays(), 1)
    meet_dk = f"""
        COALESCE(
            NULLIF(regexp_replace(e.meta->>'duration', '[^0-9]', '', 'g'), '')
                ::numeric,
            {TOPLANTI_DK_VARSAYILAN}
        )
    """
    sql = f"""
        SELECT r.full_name,
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_CEVIRME_E}
          )::int AS arama,
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_TEMAS_E}
          )::int AS ulasilan,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
          )::int AS randevu,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'katildi'
          )::int AS katildi,
          coalesce(sum({_DUR_E}) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_CEVIRME_E}
          ), 0)::float AS arama_sn,
          coalesce(sum({_DUR_E}) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_TEMAS_E}
          ), 0)::float AS temas_sn,
          coalesce(sum({meet_dk}) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
          ), 0)::float AS randevu_dk,
          coalesce(sum({meet_dk}) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'katildi'
          ), 0)::float AS katildi_dk
        FROM reps r
        LEFT JOIN events e
          ON e.org_id = r.org_id AND e.rep_id = r.rep_id
         AND e.occurred_at >= now() - interval '{WINDOW_DAYS} days'
         AND e.occurred_at <= now()
        WHERE r.org_id = %s AND {_sales_rep_sql()}
        GROUP BY r.full_name
        ORDER BY r.full_name
    """
    with connect() as conn:
        rows = conn.execute(sql, (org_id,)).fetchall()
        dur = conn.execute(
            f"""
            SELECT
              avg({_DUR_E}) FILTER (
                WHERE {_CEVIRME_E} AND NOT ({_TEMAS_E})
              )::float AS avg_miss,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DUR_E})
                FILTER (WHERE {_TEMAS_E})::float AS med_hit
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND {_sales_rep_sql()}
              AND e.channel = 'call' AND e.direction = 'outbound'
              AND e.occurred_at >= now() - interval '{WINDOW_DAYS} days'
              AND e.occurred_at <= now()
            """,
            (org_id,),
        ).fetchone()
    out: list[dict[str, Any]] = []
    for name, arama, ulasilan, randevu, katildi, arama_sn, temas_sn, randevu_dk, katildi_dk in rows:
        arama_n = int(arama)
        out.append(
            {
                "temsilci": str(name),
                "arama": round(arama_n / days, 1),
                "arama_dk": round(float(arama_sn) / 60.0 / days, 1),
                "ulasilan": round(int(ulasilan) / days, 1),
                "ulasilan_dk": round(float(temas_sn) / 60.0 / days, 1),
                "randevu": round(int(randevu) / days, 1),
                "randevu_dk": round(float(randevu_dk) / days, 1),
                "toplanti": round(int(katildi) / days, 1),
                "toplanti_dk": round(float(katildi_dk) / days, 1),
                "crm": round(arama_n / days, 1),
                "crm_dk": round(arama_n * CRM_DK_PER_ARAMA / days, 1),
            }
        )
    extras = {
        "ulasilamayan_ort_sn": float(dur[0]) if dur and dur[0] is not None else None,
        "ulasilan_tipik_sn": float(dur[1]) if dur and dur[1] is not None else None,
        "workdays": days,
        "crm_dk_per_gorusme": CRM_DK_PER_GORUSME,
    }
    return out, extras


def _minutes_for(
    *,
    arama: float,
    ulasilan: float,
    katildi: float,
    miss_sn: float,
    hit_sn: float,
) -> dict[str, float]:
    unreached = max(arama - ulasilan, 0.0)
    return {
        "lead": 0.0,
        "arama": (unreached * miss_sn + arama * OLU_ZAMAN_SN) / 60.0,
        "ulasilan": ulasilan * hit_sn / 60.0,
        "randevu": 0.0,
        "toplanti": katildi * TOPLANTI_DK,
        "crm": ulasilan * CRM_DK_PER_GORUSME,
    }


def workload_board(
    rep_id: str | None,
    arama_per_lead: float,
    toplanti_gun: float,
) -> dict[str, Any]:
    """Kişi başı günlük PLANLANAN / GERÇEKLEŞEN + doluluk."""
    org_id = get_org_id()
    days = max(_workdays(), 1)
    extra, params = _rep_filter("e", rep_id)
    extra_l, params_l = _rep_filter("l", rep_id, "owner_rep_id")
    n_reps = 1
    if not rep_id:
        n_reps = max(len(load_reps()), 1)

    sql = f"""
        SELECT
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_CEVIRME_E}
          )::int AS arama,
          count(*) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_TEMAS_E}
          )::int AS ulasilan,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
          )::int AS randevu,
          count(*) FILTER (
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'katildi'
          )::int AS katildi,
          avg({_DUR_E}) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_CEVIRME_E} AND NOT ({_TEMAS_E})
          )::float AS miss_sn,
          avg({_DUR_E}) FILTER (
            WHERE e.channel = 'call' AND e.direction = 'outbound'
              AND {_TEMAS_E}
          )::float AS hit_sn
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND {_sales_rep_sql()}
          AND e.occurred_at >= now() - interval '{WINDOW_DAYS} days'
          AND e.occurred_at <= now()
          {extra}
    """
    lead_sql = f"""
        SELECT count(*)::int
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND {_sales_rep_sql()}
          AND coalesce(l.assigned_at, l.created_at)
                >= now() - interval '{WINDOW_DAYS} days'
          {extra_l}
    """
    with connect() as conn:
        row = conn.execute(sql, (org_id, *params)).fetchone()
        lead_row = conn.execute(lead_sql, (org_id, *params_l)).fetchone()

    arama_t = int(row[0] or 0) if row else 0
    ulasilan_t = int(row[1] or 0) if row else 0
    randevu_t = int(row[2] or 0) if row else 0
    katildi_t = int(row[3] or 0) if row else 0
    miss_sn = float(row[4]) if row and row[4] is not None else 0.0
    hit_sn = float(row[5]) if row and row[5] is not None else 0.0
    leads_t = int(lead_row[0] or 0) if lead_row else 0

    scale = float(days) * float(n_reps)
    actual = {
        "lead": leads_t / scale,
        "arama": arama_t / scale,
        "ulasilan": ulasilan_t / scale,
        "randevu": randevu_t / scale,
        "toplanti": katildi_t / scale,
        "crm": ulasilan_t / scale,
    }
    ulasma = _ratio(ulasilan_t, arama_t)
    randevu_orani = _ratio(randevu_t, ulasilan_t)
    ulasma_f = (ulasma / 100.0) if ulasma is not None else 0.0
    randevu_f = (randevu_orani / 100.0) if randevu_orani is not None else 0.0

    lead_plan = actual["lead"]
    arama_plan = lead_plan * arama_per_lead
    ulasilan_plan = arama_plan * ulasma_f
    randevu_plan = ulasilan_plan * randevu_f
    plan = {
        "lead": lead_plan,
        "arama": arama_plan,
        "ulasilan": ulasilan_plan,
        "randevu": randevu_plan,
        "toplanti": float(toplanti_gun),
        "crm": ulasilan_plan,
    }
    plan_dk = _minutes_for(
        arama=plan["arama"],
        ulasilan=plan["ulasilan"],
        katildi=plan["toplanti"],
        miss_sn=miss_sn,
        hit_sn=hit_sn,
    )
    gercek_dk = _minutes_for(
        arama=actual["arama"],
        ulasilan=actual["ulasilan"],
        katildi=actual["toplanti"],
        miss_sn=miss_sn,
        hit_sn=hit_sn,
    )
    labels = [
        ("lead", "gelen lead"),
        ("arama", "arama"),
        ("ulasilan", "ulaşılan görüşme"),
        ("randevu", "randevu alınan"),
        ("toplanti", "gerçekleşen toplantı"),
        ("crm", "CRM kayıt"),
    ]
    rows: list[dict[str, Any]] = []
    for key, label in labels:
        p = plan[key]
        g = actual[key]
        rows.append(
            {
                "iş": label,
                "planlanan": round(p, 1),
                "plan dk": round(plan_dk[key], 1),
                "gerçekleşen": round(g, 1),
                "gerçek dk": round(gercek_dk[key], 1),
                "plan gerçekleşme": _ratio(g, p),
            }
        )
    plan_dk_sum = sum(plan_dk.values())
    gercek_dk_sum = sum(gercek_dk.values())
    return {
        "rows": rows,
        "plan_saat": round(plan_dk_sum / 60.0, 2),
        "gercek_saat": round(gercek_dk_sum / 60.0, 2),
        "doluluk": _ratio(gercek_dk_sum, GUN_SAAT * 60.0),
        "toplam_oran": _ratio(gercek_dk_sum, plan_dk_sum),
        "miss_sn": miss_sn,
        "hit_sn": hit_sn,
        "workdays": days,
        "n_reps": n_reps,
    }


def talk_duration_by_rep(
    window: DateWindow | None = None,
) -> list[dict[str, Any]]:
    org_id = get_org_id()
    start_ts, end_ts = _bounds(window)
    sql = f"""
        SELECT r.full_name,
          avg({_DUR_E}) FILTER (WHERE {_TEMAS_E})::float AS avg_sec,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DUR_E})
            FILTER (WHERE {_TEMAS_E})::float AS med_sec,
          count(*) FILTER (WHERE {_TEMAS_E})::int AS n
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND {_sales_rep_sql()}
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND e.occurred_at >= %s
          AND e.occurred_at <= %s
          AND e.occurred_at <= now()
        GROUP BY r.full_name
        ORDER BY r.full_name
    """
    with connect() as conn:
        rows = conn.execute(sql, (org_id, start_ts, end_ts)).fetchall()
    return [
        {
            "temsilci": str(n),
            "ortalama_sn": round(float(a), 1) if a is not None else None,
            "medyan_sn": round(float(m), 1) if m is not None else None,
            "n": int(c),
        }
        for n, a, m, c in rows
    ]


def _conversion_lead_sql() -> str:
    return f"""
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND {_sales_rep_sql()}
          AND coalesce(l.assigned_at, l.created_at) >= %s
          AND coalesce(l.assigned_at, l.created_at) < %s
          AND coalesce(l.status, '') <> %s
    """


def sales_cycle(
    window: DateWindow | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    """assigned_at → contacts.created_at, gün."""
    org_id = get_org_id()
    conv = f"""
        conv AS (
            SELECT l.lead_id, l.thread_id, l.org_id, l.assigned_at, l.created_at,
                   r.full_name
            {_conversion_lead_sql()}
        )
    """
    paired = f"""
        paired AS (
            SELECT conv.full_name,
              extract(epoch FROM (
                  c.created_at - coalesce(conv.assigned_at, conv.created_at)
              )) / 86400.0 AS gun
            FROM conv
            JOIN LATERAL (
                SELECT ct.created_at
                FROM contacts ct
                WHERE ct.org_id = conv.org_id
                  AND (
                    ct.lead_id = conv.lead_id
                    OR ct.thread_id = conv.thread_id
                  )
                  AND ct.created_at IS NOT NULL
                ORDER BY ct.created_at ASC
                LIMIT 1
            ) c ON true
            WHERE coalesce(conv.assigned_at, conv.created_at) IS NOT NULL
              AND c.created_at >= coalesce(conv.assigned_at, conv.created_at)
        )
    """
    sql = f"""
        WITH {conv},
        {paired}
        SELECT full_name,
          avg(gun)::float,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY gun)::float,
          count(*)::int
        FROM paired
        GROUP BY full_name
        ORDER BY full_name
    """
    team_sql = f"""
        WITH {conv},
        {paired}
        SELECT avg(gun)::float,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY gun)::float,
          count(*)::int
        FROM paired
    """
    args = (org_id, *_conv_bounds(window), MEVCUT_MUSTERI)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        team = conn.execute(team_sql, args).fetchone()
    by_rep = [
        {
            "temsilci": str(n),
            "ortalama_gun": round(float(a), 1) if a is not None else None,
            "medyan_gun": round(float(m), 1) if m is not None else None,
            "n": int(c),
        }
        for n, a, m, c in rows
    ]
    team_d = {
        "ortalama_gun": round(float(team[0]), 1) if team and team[0] is not None else None,
        "medyan_gun": round(float(team[1]), 1) if team and team[1] is not None else None,
        "n": int(team[2]) if team else 0,
    }
    return by_rep, team_d


def take_rate(
    window: DateWindow | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    org_id = get_org_id()
    base = _conversion_lead_sql()
    sql = f"""
        SELECT r.full_name,
          count(*)::int AS leads,
          count(*) FILTER (WHERE {_has_contact_sql()})::int AS contacts,
          count(*) FILTER (WHERE {_has_temas_sql()})::int AS reached,
          count(*) FILTER (
            WHERE {_has_temas_sql()} AND {_has_contact_sql()}
          )::int AS reached_contacts
        {base}
        GROUP BY r.full_name
        ORDER BY r.full_name
    """
    team_sql = f"""
        SELECT
          count(*)::int,
          count(*) FILTER (WHERE {_has_contact_sql()})::int,
          count(*) FILTER (WHERE {_has_temas_sql()})::int,
          count(*) FILTER (
            WHERE {_has_temas_sql()} AND {_has_contact_sql()}
          )::int
        {base}
    """
    args = (org_id, *_conv_bounds(window), MEVCUT_MUSTERI)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        team = conn.execute(team_sql, args).fetchone()
    by_rep = []
    for name, leads, contacts, reached, reached_c in rows:
        by_rep.append(
            {
                "temsilci": str(name),
                "leads": int(leads),
                "genel": _ratio(contacts, leads),
                "ulasilanda": _ratio(reached_c, reached),
            }
        )
    team_d = {
        "leads": int(team[0]) if team else 0,
        "genel": _ratio(team[1], team[0]) if team else None,
        "ulasilanda": _ratio(team[3], team[2]) if team else None,
    }
    return by_rep, team_d


def _has_contact_sql() -> str:
    return """
        EXISTS (
            SELECT 1 FROM contacts ct
            WHERE ct.org_id = l.org_id
              AND (ct.lead_id = l.lead_id OR ct.thread_id = l.thread_id)
        )
    """


def _has_won_sql() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM deals d
            WHERE d.org_id = l.org_id
              AND d.thread_id = l.thread_id
              AND {_WON_D}
        )
    """


def _has_temas_sql() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND {_TEMAS_E}
        )
    """


def _has_attempt_sql() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND {_ATTEMPT_E}
        )
    """


def source_take_rate(window: DateWindow | None = None) -> list[dict[str, Any]]:
    org_id = get_org_id()
    base = _conversion_lead_sql()
    sql = f"""
        SELECT
          CASE
            WHEN l.source = %s THEN %s
            WHEN l.source = %s THEN %s
            ELSE 'diger'
          END AS src,
          count(*)::int AS leads,
          count(*) FILTER (WHERE {_has_won_sql()})::int AS satis,
          count(*) FILTER (WHERE {_has_contact_sql()})::int AS contacts,
          count(*) FILTER (WHERE {_has_temas_sql()})::int AS reached,
          count(*) FILTER (
            WHERE {_has_temas_sql()} AND {_has_contact_sql()}
          )::int AS reached_c
        {base}
        GROUP BY 1
        ORDER BY 1
    """
    conv_start, conv_end = _conv_bounds(window)
    args = (
        _SOURCE_FORM,
        _SOURCE_FORM,
        _SOURCE_REGISTER,
        _SOURCE_REGISTER,
        org_id,
        conv_start,
        conv_end,
        MEVCUT_MUSTERI,
    )
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    order = {_SOURCE_FORM: 0, _SOURCE_REGISTER: 1, "diger": 2}
    out = [
        {
            "kaynak": str(src),
            "lead": int(leads),
            "satis": int(satis),
            "contacts": int(contacts),
            "reached": int(reached),
            "reached_c": int(reached_c),
            "genelde": _ratio(contacts, leads),
            "ulasilanda": _ratio(reached_c, reached),
        }
        for src, leads, satis, contacts, reached, reached_c in rows
    ]
    out.sort(key=lambda r: order.get(str(r["kaynak"]), 9))
    return out


def path_take_rate(window: DateWindow | None = None) -> list[dict[str, Any]]:
    """Satışa giden yol: katıldı / katılmadı / yalnız arama / hiç aranmamış."""
    org_id = get_org_id()
    base = _conversion_lead_sql()
    sql = f"""
        WITH uni AS (
            SELECT
              CASE
                WHEN EXISTS (
                    SELECT 1 FROM events e
                    WHERE e.org_id = l.org_id AND e.thread_id = l.thread_id
                      AND e.channel = 'meeting'
                      AND e.meta->>'randevu_durumu' = 'katildi'
                      AND e.occurred_at <= now()
                ) THEN 'toplantiya katildi'
                WHEN EXISTS (
                    SELECT 1 FROM events e
                    WHERE e.org_id = l.org_id AND e.thread_id = l.thread_id
                      AND e.channel = 'meeting'
                      AND e.meta->>'randevu_durumu' = 'katilmadi'
                      AND e.occurred_at <= now()
                ) THEN 'randevu var katilmadi'
                WHEN {_has_attempt_sql()} THEN 'toplanti yok sadece arama'
                ELSE 'hic aranmamis'
              END AS yol,
              {_has_won_sql()} AS won,
              {_has_contact_sql()} AS has_contact,
              {_has_temas_sql()} AS has_temas
            {base}
        )
        SELECT yol,
          count(*)::int AS leads,
          count(*) FILTER (WHERE won)::int AS satis,
          count(*) FILTER (WHERE has_contact)::int AS contacts,
          count(*) FILTER (WHERE has_temas)::int AS reached,
          count(*) FILTER (WHERE has_temas AND has_contact)::int AS reached_c
        FROM uni
        GROUP BY yol
    """
    order = {
        "toplantiya katildi": 0,
        "randevu var katilmadi": 1,
        "toplanti yok sadece arama": 2,
        "hic aranmamis": 3,
    }
    labels = {
        "toplantiya katildi": "toplantıya katıldı",
        "randevu var katilmadi": "randevu var katılmadı",
        "toplanti yok sadece arama": "toplantı yok sadece arama",
        "hic aranmamis": "hiç aranmamış",
    }
    with connect() as conn:
        rows = conn.execute(
            sql, (org_id, *_conv_bounds(window), MEVCUT_MUSTERI)
        ).fetchall()
    out = [
        {
            "yol": labels.get(str(yol), str(yol)),
            "lead": int(leads),
            "satis": int(satis),
            "genelde": _ratio(contacts, leads),
            "ulasilanda": _ratio(reached_c, reached),
            "_ord": order.get(str(yol), 9),
        }
        for yol, leads, satis, contacts, reached, reached_c in rows
    ]
    out.sort(key=lambda r: int(r["_ord"]))
    for row in out:
        del row["_ord"]
    return out


def funnel(
    rep_id: str | None,
    *,
    named: bool,
    window: DateWindow | None = None,
) -> list[dict[str, Any]]:
    org_id = get_org_id()
    extra, params = _rep_filter("l", rep_id, "owner_rep_id")
    start_ts, end_ts = _bounds(window)
    statuses = ", ".join("'" + s.replace("'", "''") + "'" for s in _FUNNEL_STATUSES)
    if named:
        sql = f"""
            SELECT r.full_name, l.status, count(*)::int
            FROM leads l
            JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
            WHERE l.org_id = %s
              AND {_sales_rep_sql()}
              AND l.status IN ({statuses})
              AND coalesce(l.assigned_at, l.created_at) >= %s
              AND coalesce(l.assigned_at, l.created_at) < %s
              {extra}
            GROUP BY r.full_name, l.status
        """
        with connect() as conn:
            rows = conn.execute(sql, (org_id, start_ts, end_ts, *params)).fetchall()
        by_rep: dict[str, dict[str, int]] = {}
        for name, status, n in rows:
            by_rep.setdefault(str(name), {s: 0 for s in _FUNNEL_STATUSES})
            by_rep[str(name)][str(status)] = int(n)
        names = [r.full_name for r in load_reps()]
        if extra:
            names = [n for n in names if n in by_rep] or names
        out = []
        for name in names:
            bucket = by_rep.get(name, {s: 0 for s in _FUNNEL_STATUSES})
            row: dict[str, Any] = {"temsilci": name}
            for s in _FUNNEL_STATUSES:
                row[s] = bucket.get(s, 0)
            out.append(row)
        return out
    sql = f"""
        SELECT l.status, count(*)::int
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND {_sales_rep_sql()}
          AND l.status IN ({statuses})
          AND coalesce(l.assigned_at, l.created_at) >= %s
          AND coalesce(l.assigned_at, l.created_at) < %s
        GROUP BY l.status
    """
    with connect() as conn:
        rows = conn.execute(sql, (org_id, start_ts, end_ts)).fetchall()
    counts = {str(s): int(n) for s, n in rows}
    return [{"durum": s, "lead": counts.get(s, 0)} for s in _FUNNEL_STATUSES]


def funnel_dropped_by_rep(
    window: DateWindow | None = None,
) -> list[dict[str, Any]]:
    """Tablodan düşen 3.Arama-Ulaşılamadı kayıtları — rapor, kolon değil."""
    org_id = get_org_id()
    start_ts, end_ts = _bounds(window)
    sql = f"""
        SELECT r.full_name, count(*)::int
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND {_sales_rep_sql()}
          AND l.status = %s
          AND coalesce(l.assigned_at, l.created_at) >= %s
          AND coalesce(l.assigned_at, l.created_at) < %s
        GROUP BY r.full_name
        ORDER BY r.full_name
    """
    with connect() as conn:
        rows = conn.execute(
            sql, (org_id, FUNNEL_DROPPED_STATUS, start_ts, end_ts)
        ).fetchall()
    return [{"temsilci": str(name), "lead": int(n)} for name, n in rows]


def first_meeting_week() -> dict[str, datetime | None]:
    """Temsilci → ilk meeting'in hafta başı (Istanbul). Veri boşluğu için."""
    org_id = get_org_id()
    sql = f"""
        SELECT r.full_name,
          date_trunc(
            'week',
            min(e.occurred_at AT TIME ZONE 'Europe/Istanbul')
          )
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND {_sales_rep_sql()}
          AND e.channel = 'meeting'
          AND e.occurred_at <= now()
        GROUP BY r.full_name
    """
    with connect() as conn:
        rows = conn.execute(sql, (org_id,)).fetchall()
    return {str(n): (w if w is not None else None) for n, w in rows}


def weekly_series(
    rep_id: str | None, window: DateWindow | None = None
) -> list[dict[str, Any]]:
    """Penceredeki haftalar: arama, ulaşma, randevu, katılım, satışa dönme.

    Meeting metrikleri ilk randevudan önceki haftalarda None (boşluk).
    """
    org_id = get_org_id()
    extra, params = _rep_filter("e", rep_id)
    extra_l, params_l = _rep_filter("l", rep_id, "owner_rep_id")
    n_reps = max(len(load_reps()), 1)
    start_ts, end_ts = _bounds(window)
    conv_start, conv_end = _conv_bounds(window)
    sql = f"""
        WITH weeks AS (
            SELECT generate_series(
                date_trunc('week', %s AT TIME ZONE 'Europe/Istanbul'),
                date_trunc('week', %s AT TIME ZONE 'Europe/Istanbul'),
                interval '1 week'
            ) AS week_start
        ),
        call_w AS (
            SELECT
              date_trunc(
                'week', e.occurred_at AT TIME ZONE 'Europe/Istanbul'
              ) AS week_start,
              count(*) FILTER (
                WHERE e.direction = 'outbound' AND {_CEVIRME_E}
              )::int AS arama,
              count(*) FILTER (
                WHERE e.direction = 'outbound' AND {_TEMAS_E}
              )::int AS ulasilan_giden,
              count(*) FILTER (WHERE {_DONUS_E})::int AS donus,
              count(*) FILTER (WHERE {_GELEN_E})::int AS gelen,
              {_LEAD_PAYDA_E}::int AS lead_payda,
              {_LEAD_PAY_E}::int AS lead_pay
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND {_sales_rep_sql()}
              AND e.channel = 'call'
              AND e.occurred_at <= now()
              AND e.occurred_at >= %s
              AND e.occurred_at <= %s
              {extra}
            GROUP BY 1
        ),
        meet_w AS (
            SELECT
              date_trunc(
                'week', e.occurred_at AT TIME ZONE 'Europe/Istanbul'
              ) AS week_start,
              count(*) FILTER (
                WHERE e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
              )::int AS randevu,
              count(*) FILTER (
                WHERE e.meta->>'randevu_durumu' = 'katildi'
              )::int AS katildi
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND {_sales_rep_sql()}
              AND e.channel = 'meeting'
              AND e.occurred_at <= now()
              AND e.occurred_at >= %s
              AND e.occurred_at <= %s
              {extra}
            GROUP BY 1
        ),
        lead_w AS (
            SELECT
              date_trunc(
                'week',
                coalesce(l.assigned_at, l.created_at)
                    AT TIME ZONE 'Europe/Istanbul'
              ) AS week_start,
              count(*)::int AS leads,
              count(*) FILTER (WHERE {_has_contact_sql()})::int AS contacts,
              count(*) FILTER (WHERE {_has_temas_sql()})::int AS reached,
              count(*) FILTER (
                WHERE {_has_temas_sql()} AND {_has_contact_sql()}
              )::int AS reached_c
            FROM leads l
            JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
            WHERE l.org_id = %s
              AND {_sales_rep_sql()}
              AND coalesce(l.assigned_at, l.created_at) >= %s
              AND coalesce(l.assigned_at, l.created_at) < %s
              AND coalesce(l.status, '') <> %s
              {extra_l}
            GROUP BY 1
        )
        SELECT
          weeks.week_start,
          c.arama,
          c.ulasilan_giden,
          c.donus,
          c.gelen,
          c.lead_payda,
          c.lead_pay,
          m.randevu,
          m.katildi,
          lw.leads,
          lw.contacts,
          lw.reached,
          lw.reached_c
        FROM weeks
        LEFT JOIN call_w c ON c.week_start = weeks.week_start
        LEFT JOIN meet_w m ON m.week_start = weeks.week_start
        LEFT JOIN lead_w lw ON lw.week_start = weeks.week_start
        ORDER BY weeks.week_start
    """
    args = (
        start_ts,
        end_ts,
        org_id,
        start_ts,
        end_ts,
        *params,
        org_id,
        start_ts,
        end_ts,
        *params,
        org_id,
        conv_start,
        conv_end,
        MEVCUT_MUSTERI,
        *params_l,
    )
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    first_meet = first_meeting_week()
    # Bookings herkeste 12 haftayı kapsamaz. İlk meeting öncesi hafta = boşluk.
    gap_from: datetime | None = None
    if rep_id:
        names = {r.rep_id: r.full_name for r in load_reps()}
        gap_from = first_meet.get(names.get(rep_id, ""))
    else:
        starts = [v for v in first_meet.values() if v is not None]
        gap_from = min(starts) if starts else None
    out: list[dict[str, Any]] = []
    for week_start, arama, ulasilan_giden, donus, gelen, lead_payda, lead_pay, randevu, katildi, leads, contacts, reached, reached_c in rows:
        week = week_start
        arama_n = int(arama) if arama is not None else 0
        donus_n = int(donus) if donus is not None else 0
        gelen_n = int(gelen) if gelen is not None else 0
        payda_n = int(lead_payda) if lead_payda is not None else 0
        pay_n = int(lead_pay) if lead_pay is not None else 0
        randevu_n = int(randevu) if randevu is not None else None
        katildi_n = int(katildi) if katildi is not None else None
        meeting_gap = bool(
            gap_from is not None and week is not None and week < gap_from
        )
        if meeting_gap:
            randevu_val: float | None = None
            katilim_val: float | None = None
        else:
            n_r = randevu_n or 0
            randevu_val = float(n_r)
            if not rep_id:
                randevu_val = randevu_val / n_reps
            katilim_val = _ratio(katildi_n or 0, n_r)
        kisi_basi = arama_n / n_reps if not rep_id else float(arama_n)
        out.append(
            {
                "hafta": week,
                "arama": kisi_basi,
                "arama_ham": arama_n,
                "donus": donus_n / n_reps if not rep_id else float(donus_n),
                "gelen": gelen_n / n_reps if not rep_id else float(gelen_n),
                "ulasma_orani": _ratio(pay_n, payda_n),
                "randevu": randevu_val,
                "katilim_orani": katilim_val,
                "take_genel": _ratio(contacts, leads),
                "take_ulasilanda": _ratio(reached_c, reached),
            }
        )
    return out


def weekly_team_series(window: DateWindow | None = None) -> list[dict[str, Any]]:
    return weekly_series(None, window)


def katilim_by_rep(window: DateWindow | None = None) -> list[dict[str, Any]]:
    org_id = get_org_id()
    start_ts, end_ts = _bounds(window)
    sql = f"""
        SELECT r.full_name,
          count(*) FILTER (
            WHERE e.meta->>'randevu_durumu' = 'katildi'
          )::int AS katildi,
          count(*) FILTER (
            WHERE e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
          )::int AS randevu
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND {_sales_rep_sql()}
          AND e.channel = 'meeting'
          AND e.occurred_at >= %s
          AND e.occurred_at <= %s
          AND e.occurred_at <= now()
        GROUP BY r.full_name
        ORDER BY r.full_name
    """
    with connect() as conn:
        rows = conn.execute(sql, (org_id, start_ts, end_ts)).fetchall()
    return [
        {
            "temsilci": str(n),
            "katildi": int(k),
            "randevu": int(d),
            "katilim_orani": _ratio(k, d),
        }
        for n, k, d in rows
    ]


def _workdays_in(window: DateWindow) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT count(*)::int
            FROM generate_series(
                %s::date, %s::date, interval '1 day'
            ) AS d
            WHERE extract(isodow FROM d) < 6
            """,
            (window.start, window.end),
        ).fetchone()
    return int(row[0]) if row else 1


def rep_snapshot(
    rep_id: str, window: DateWindow | None = None
) -> dict[str, Any]:
    """Temsilcinin seçilen penceresi ve bir önceki eşit uzunluk."""
    org_id = get_org_id()
    w = window or default_window()
    start_ts, end_ts = _bounds(w)
    span = end_ts - start_ts
    prev_end = start_ts
    prev_start = start_ts - span

    def _window(start_at: datetime, end_at: datetime) -> dict[str, Any]:
        sql = f"""
            SELECT
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {_CEVIRME_E}
              )::int AS arama,
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {_TEMAS_E}
              )::int AS ulasilan_giden,
              count(*) FILTER (WHERE {_DONUS_E})::int AS donus,
              count(*) FILTER (WHERE {_GELEN_E})::int AS gelen,
              {_LEAD_PAYDA_E}::int AS lead_payda,
              {_LEAD_PAY_E}::int AS lead_pay,
              count(*) FILTER (
                WHERE e.channel = 'meeting'
                  AND e.meta->>'randevu_durumu' IN ('katildi', 'katilmadi')
              )::int AS randevu,
              count(*) FILTER (
                WHERE e.channel = 'meeting'
                  AND e.meta->>'randevu_durumu' = 'katildi'
              )::int AS katildi,
              avg({_DUR_E}) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {_TEMAS_E}
              )::float AS avg_sn,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DUR_E})
                FILTER (
                  WHERE e.channel = 'call' AND e.direction = 'outbound'
                    AND {_TEMAS_E}
                )::float AS med_sn
            FROM events e
            WHERE e.org_id = %s
              AND e.rep_id = %s
              AND e.occurred_at >= %s
              AND e.occurred_at < %s
              AND e.occurred_at <= now()
        """
        with connect() as conn:
            row = conn.execute(
                sql, (org_id, rep_id, start_at, end_at)
            ).fetchone()
        arama = int(row[0] or 0) if row else 0
        ulasilan_giden = int(row[1] or 0) if row else 0
        donus = int(row[2] or 0) if row else 0
        gelen = int(row[3] or 0) if row else 0
        lead_payda = int(row[4] or 0) if row else 0
        lead_pay = int(row[5] or 0) if row else 0
        randevu = int(row[6] or 0) if row else 0
        katildi = int(row[7] or 0) if row else 0
        ulasilan = _reach_pay(ulasilan_giden, donus)
        return {
            "arama": arama,
            "donus": donus,
            "gelen": gelen,
            "ulasilan": ulasilan,
            "ulasma_orani": _ratio(lead_pay, lead_payda),
            "randevu": randevu,
            "katildi": katildi,
            "katilim_orani": _ratio(katildi, randevu),
            "temas_randevu_orani": _ratio(randevu, ulasilan_giden),
            "ortalama_sn": float(row[8]) if row and row[8] is not None else None,
            "medyan_sn": float(row[9]) if row and row[9] is not None else None,
        }

    current = _window(start_ts, end_ts)
    previous = _window(prev_start, prev_end)
    with connect() as conn:
        leads_now = conn.execute(
            """
            SELECT count(*)::int
            FROM leads
            WHERE org_id = %s AND owner_rep_id = %s
              AND coalesce(assigned_at, created_at) >= %s
              AND coalesce(assigned_at, created_at) < %s
            """,
            (org_id, rep_id, start_ts, end_ts),
        ).fetchone()
        leads_prev = conn.execute(
            """
            SELECT count(*)::int
            FROM leads
            WHERE org_id = %s AND owner_rep_id = %s
              AND coalesce(assigned_at, created_at) >= %s
              AND coalesce(assigned_at, created_at) < %s
            """,
            (org_id, rep_id, prev_start, prev_end),
        ).fetchone()
    current["lead"] = int(leads_now[0] or 0) if leads_now else 0
    previous["lead"] = int(leads_prev[0] or 0) if leads_prev else 0
    days = max(_workdays_in(w), 1)
    for bucket in (current, previous):
        bucket["arama_gun"] = round(bucket["arama"] / days, 1)
        bucket["donus_gun"] = round(bucket["donus"] / days, 1)
        bucket["gelen_gun"] = round(bucket["gelen"] / days, 1)
        bucket["ulasilan_gun"] = round(bucket["ulasilan"] / days, 1)
        bucket["randevu_gun"] = round(bucket["randevu"] / days, 1)
        bucket["toplanti_gun"] = round(bucket["katildi"] / days, 1)
        bucket["crm_gun"] = round(bucket["arama"] / days, 1)
    return {"current": current, "previous": previous}


def source_temas_gap(window: DateWindow | None = None) -> float | None:
    """Contact Form vs Register temas oranı farkı (puan)."""
    org_id = get_org_id()
    start_ts, end_ts = _bounds(window)
    sql = f"""
        SELECT
          round(100.0 * count(*) FILTER (
            WHERE l.source = %s AND {_has_temas_sql()}
          ) / nullif(count(*) FILTER (WHERE l.source = %s), 0), 1)
          -
          round(100.0 * count(*) FILTER (
            WHERE l.source = %s AND {_has_temas_sql()}
          ) / nullif(count(*) FILTER (WHERE l.source = %s), 0), 1)
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND {_sales_rep_sql()}
          AND coalesce(l.assigned_at, l.created_at) >= %s
          AND coalesce(l.assigned_at, l.created_at) < %s
    """
    with connect() as conn:
        row = conn.execute(
            sql,
            (
                _SOURCE_FORM,
                _SOURCE_FORM,
                _SOURCE_REGISTER,
                _SOURCE_REGISTER,
                org_id,
                start_ts,
                end_ts,
            ),
        ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _ratio(num: int | float | None, den: int | float | None) -> float | None:
    if num is None or den is None:
        return None
    if float(den) == 0:
        return None
    return round(100.0 * float(num) / float(den), 1)


def fmt_duration(sec: float | int | None) -> str:
    """Saniye → '7 dk 42 sn' veya '48 sn'."""
    if sec is None:
        return "—"
    try:
        total = int(round(float(sec)))
    except (TypeError, ValueError):
        return "—"
    if total < 0:
        total = 0
    if total < 60:
        return f"{total} sn"
    minutes, rest = divmod(total, 60)
    if rest == 0:
        return f"{minutes} dk"
    return f"{minutes} dk {rest} sn"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        if value != value:  # NaN
            return "—"
    except (TypeError, ValueError):
        return "—"
    return f"%{float(value):.1f}"


def fmt_num(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def arrow(current: float | None, previous: float | None) -> str:
    """Son değer vs ortalama/önceki. Ok işareti."""
    if current is None or previous is None:
        return "veri yetersiz"
    diff = current - previous
    if abs(diff) < 0.05:
        return "→ 0"
    mark = "↑" if diff > 0 else "↓"
    return f"{mark} {diff:+.1f}"


def mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def latest_event_created_at() -> datetime | None:
    """events.created_at en yenisi — ingest gecikmesi için."""
    org_id = get_org_id()
    with connect() as conn:
        row = conn.execute(
            "SELECT max(created_at) FROM events WHERE org_id = %s",
            (org_id,),
        ).fetchone()
    if not row or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, datetime):
        return value
    return None
