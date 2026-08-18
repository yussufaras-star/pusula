"""Haftalık yönetici raporu — Cliq DM (gölge mod).

Metrikler (özetlenen hafta / bir önceki), yalnız sayı:
  a) Kayıt disiplini — 10 sn+ outbound'ta call_result dolu %
  b) Lead durumu — dokunulmamış / denendi / ulaşıldı (Zoho status)
  c) Görüşme süresi — ULASILDI lead'lerde 10 sn+ outbound ort/medyan
  d) Dönülmemiş randevu — Randevu Alındı, sonrası ulaşılmış çağrı yok
  e) Arama verimi — 1/2/3. denemede temas %; 15:00+ temas % ve çağrı
  f) Haftanın hareketi — çağrı, 10 sn+ görüşme, yeni/tutulan/bozulan taahhüt
  g) Satış — sıfır / tekrar / atıfsız; medyan döngü (güvenilir sıfır)
  h) Temsilci özeti — FAALİYET (4 hafta) / SONUÇ (90 gün); sıfır 90 gün sırası

Temas (çağrı): scheduled değil, category <> 'not_reached' (süre yok).
Süre eşiği yalnız kayıt disiplini ve 10 sn+ görüşme satırında.
Hafta: pazartesi 00:00 — sonraki pazartesi 00:00 (orgs.timezone).
Varsayılan: tamamlanmış son hafta (pazartesi sabahı → önceki Pzt–Paz).
--hafta bu: içinde bulunulan (devam eden) takvim haftası.
events.occurred_at; leads.created_at kullanılmaz.

Kullanım:
    python scripts/weekly_report.py
    python scripts/weekly_report.py --hafta bu
    python scripts/weekly_report.py --debug
    python scripts/weekly_report.py --apply

Varsayılan dry-run. --apply: CLIQ_WEBHOOK_URL + PUSULA_SHADOW_EMAIL.
Alıcı listesi: PUSULA_REPORT_RECIPIENTS (virgüllü email/userid).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from pusula.config import get_org_id
from pusula.freshness import print_call_stale_warning
from pusula.lead_reach import (
    DENENDI,
    DOKUNULMAMIS,
    ULASILDI,
    reach_bucket_sql,
)
from pusula.sifir_satis import (
    ATIFSIZ,
    SIFIR,
    TEKRAR,
    classified_deals_cte,
    won_stage_sql,
)
from pusula.temas import (
    TEMAS_MIN_SEC,
    duration_sec,
    is_temas_sql,
    outcome_join,
)

_DUR = duration_sec("e")
_JOIN = outcome_join("e")
_TEMAS = is_temas_sql("e")
_REACH = reach_bucket_sql("l.status")
# Oran paydası bunun altındaysa "—" / "veri yetersiz".
_MIN_SAMPLE = 20
_MONTHS_LONG = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


@dataclass(frozen=True)
class WeekBounds:
    start: datetime
    end: datetime  # exclusive
    label: str  # örn. 11-17 Ağu


def _parse_recipients() -> list[str]:
    raw = os.environ.get("PUSULA_REPORT_RECIPIENTS") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _org_tz(conn: psycopg.Connection, org_id: str) -> ZoneInfo:
    row = conn.execute(
        "SELECT timezone FROM orgs WHERE org_id = %s", (org_id,)
    ).fetchone()
    name = str(row[0]) if row and row[0] else "Europe/Istanbul"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Istanbul")


def _week_bounds(tz: ZoneInfo, *, weeks_ago: int = 0) -> WeekBounds:
    """Pazartesi 00:00 — +7 gün (org TZ). weeks_ago=0 içinde bulunulan hafta."""
    now = datetime.now(tz)
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    monday = monday - timedelta(weeks=weeks_ago)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=tz)
    end = start + timedelta(days=7)
    months = (
        "Oca", "Şub", "Mar", "Nis", "May", "Haz",
        "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara",
    )
    last = end.date() - timedelta(days=1)
    label = (
        f"{start.day}-{last.day} {months[start.month - 1]}"
        if start.month == last.month
        else (
            f"{start.day} {months[start.month - 1]}-"
            f"{last.day} {months[last.month - 1]}"
        )
    )
    return WeekBounds(start=start, end=end, label=label)


def _hafta_to_weeks_ago(hafta: str) -> int:
    """bu=0 (devam eden), onceki=1 (tamamlanmış son hafta)."""
    if hafta == "bu":
        return 0
    if hafta == "onceki":
        return 1
    raise ValueError(f"bilinmeyen --hafta: {hafta!r}")


def _week_label_long(week: WeekBounds) -> str:
    """örn. 10-16 Ağustos"""
    start = week.start.date()
    last = week.end.date() - timedelta(days=1)
    if start.month == last.month:
        return f"{start.day}-{last.day} {_MONTHS_LONG[start.month - 1]}"
    return (
        f"{start.day} {_MONTHS_LONG[start.month - 1]}-"
        f"{last.day} {_MONTHS_LONG[last.month - 1]}"
    )


def _window_str(week: WeekBounds) -> str:
    last = week.end.date() - timedelta(days=1)
    return f"{week.start.date()} .. {last}"


def _pct_value(num: int, den: int) -> float | None:
    if den < _MIN_SAMPLE:
        return None
    return 100.0 * num / den


def _fmt_pct_short(num: int, den: int) -> str:
    pct = _pct_value(num, den)
    if pct is None:
        return "veri yetersiz"
    return f"%{pct:.0f}"


def _fmt_cycle_days(days: float) -> str:
    """14 gün."""
    n = max(0, int(round(float(days))))
    return f"{n} gün"


def _fmt_talk_sec(seconds: float) -> str:
    """3 dk 20 sn; saniye iki haneli."""
    total = max(0, int(round(float(seconds))))
    minutes, secs = divmod(total, 60)
    return f"{minutes} dk {secs:02d} sn"


def _fmt_per100(n: int, portfolio: int) -> str:
    """Portföy başına oran (/100 lead). Payda <20 → veri yetersiz."""
    if portfolio < _MIN_SAMPLE:
        return "veri yetersiz"
    return f"{100.0 * n / portfolio:.2f}/100"


def _fmt_count_port(
    n: int, portfolio: int, *, show_rate: bool = True
) -> str:
    if not show_rate:
        return f"{n} (—)"
    return f"{n} ({_fmt_per100(n, portfolio)})"


def _four_week_window(week: WeekBounds) -> tuple[datetime, datetime]:
    """Özetlenen hafta + önceki 3 takvim haftası."""
    return week.start - timedelta(weeks=3), week.end


def _days_window(week: WeekBounds, days: int) -> tuple[datetime, datetime]:
    """week.end geriye `days` gün (sağ uç hariç)."""
    return week.end - timedelta(days=days), week.end


def _latest_lead_owner_join(table_alias: str = "c") -> str:
    """Thread'teki en son lead'in owner_rep_id'si (lo)."""
    return f"""
    JOIN LATERAL (
        SELECT owner_rep_id
        FROM leads
        WHERE org_id = {table_alias}.org_id
          AND thread_id = {table_alias}.thread_id
        ORDER BY assigned_at DESC NULLS LAST
        LIMIT 1
    ) lo ON true
    """


@dataclass(frozen=True)
class RepOzet:
    name: str
    portfolio: int
    sifir_90: int
    calls_week: int
    calls: int
    talks10: int
    talk_avg: float | None
    talk_med: float | None
    cycle_med: float | None
    cycle_n: int
    disc_filled: int
    disc_total: int
    acik: int
    bozulan: int
    donulmemis: int
    low_activity: bool


def _rate_row(
    name: str,
    this_num: int,
    this_den: int,
    last_num: int,
    last_den: int,
    *,
    unit: str = "görüşme",
) -> str:
    """Abdullah Benli — %0 (önceki hafta %70, 68 görüşme)."""
    if this_den < _MIN_SAMPLE:
        return f"{name} — veri yetersiz ({this_den} {unit})"
    this_s = _fmt_pct_short(this_num, this_den)
    last_s = _fmt_pct_short(last_num, last_den)
    return (
        f"{name} — {this_s} "
        f"(önceki hafta {last_s}, {this_den} {unit})"
    )


def _count_cmp(name: str, this_n: int, last_n: int) -> str:
    return f"{name} — {this_n} (önceki hafta {last_n})"


def _post_cliq(webhook_url: str, text: str, userids: str) -> None:
    body = json.dumps(
        {"type": "message", "text": text, "userids": userids},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _sales_reps(
    conn: psycopg.Connection, org_id: str
) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT rep_id, full_name FROM reps
        WHERE org_id = %s AND active = true AND category = 'sales'
        ORDER BY full_name
        """,
        (org_id,),
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def metric_kayit_disiplini(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
    *,
    debug: bool = False,
) -> tuple[list[tuple[str, int, int]], int]:
    """(name, filled, total) — 10 sn+ outbound, call_result dolu."""
    rows = conn.execute(
        f"""
        SELECT r.full_name,
          count(*) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC}
              AND nullif(e.meta->>'call_result', '') IS NOT NULL
          )::int AS filled,
          count(*) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC}
          )::int AS total
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND r.category = 'sales' AND r.active = true
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s AND e.occurred_at < %s
        GROUP BY r.full_name
        ORDER BY
          (count(*) FILTER (
             WHERE {_DUR} >= {TEMAS_MIN_SEC}
               AND nullif(e.meta->>'call_result', '') IS NOT NULL
           )::float
           / nullif(count(*) FILTER (WHERE {_DUR} >= {TEMAS_MIN_SEC}), 0)
          ) ASC NULLS LAST,
          r.full_name
        """,
        (org_id, week.start, week.end),
    ).fetchall()
    out = [(str(n), int(f), int(t)) for n, f, t in rows]
    if debug:
        for name, filled, total in out:
            if filled == 0 and total >= 10:
                samples = conn.execute(
                    f"""
                    SELECT e.id, e.occurred_at,
                           e.meta->>'call_result' AS call_result,
                           {_DUR} AS dur
                    FROM events e
                    JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
                    WHERE e.org_id = %s
                      AND r.full_name = %s
                      AND r.category = 'sales' AND r.active = true
                      AND e.channel = 'call' AND e.direction = 'outbound'
                      AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
                      AND {_DUR} >= {TEMAS_MIN_SEC}
                      AND e.occurred_at <= now()
                      AND e.occurred_at >= %s AND e.occurred_at < %s
                    ORDER BY e.occurred_at
                    LIMIT 5
                    """,
                    (org_id, name, week.start, week.end),
                ).fetchall()
                print(
                    f"debug: kayit disiplini {name} filled=0/{total} "
                    f"— call_result bos (ornek 5):"
                )
                for eid, occurred, cr, dur in samples:
                    print(
                        f"  id={eid} occurred_at={occurred} "
                        f"call_result={cr!r} dur={dur}"
                    )
    return out, sum(t for _, _, t in out)


def metric_lead_durumu(
    conn: psycopg.Connection, org_id: str
) -> tuple[list[tuple[str, int, int, int, int]], dict[str, int]]:
    """Temsilci bazında dokunulmamış / denendi / ulaşıldı + toplam.

    Evren (aktif lead): satış temsilcisine atanmış tüm lead.
    Kovalar yalnız statusu dolu kayıt. tot = aktif lead.
    meta: cat_total (kova), aktif, empty_status, table_leads.
    """
    rows = conn.execute(
        f"""
        SELECT r.full_name,
          count(*) FILTER (WHERE {_REACH} = '{DOKUNULMAMIS}')::int AS dok,
          count(*) FILTER (WHERE {_REACH} = '{DENENDI}')::int AS den,
          count(*) FILTER (WHERE {_REACH} = '{ULASILDI}')::int AS ula,
          count(*)::int AS tot
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND r.category = 'sales' AND r.active = true
        GROUP BY r.full_name
        ORDER BY
          count(*) FILTER (WHERE {_REACH} = '{DOKUNULMAMIS}') DESC,
          count(*) FILTER (WHERE {_REACH} = '{DENENDI}') DESC,
          r.full_name
        """,
        (org_id,),
    ).fetchall()
    out = [
        (str(n), int(dok), int(den), int(ula), int(tot))
        for n, dok, den, ula, tot in rows
    ]
    sums = conn.execute(
        f"""
        SELECT
          count(*) FILTER (WHERE {_REACH} = '{DOKUNULMAMIS}')::int,
          count(*) FILTER (WHERE {_REACH} = '{DENENDI}')::int,
          count(*) FILTER (WHERE {_REACH} = '{ULASILDI}')::int,
          count(*) FILTER (WHERE {_REACH} IS NOT NULL)::int,
          count(*)::int,
          count(*) FILTER (
            WHERE l.status IS NULL OR btrim(l.status) = ''
          )::int
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND r.category = 'sales' AND r.active = true
        """,
        (org_id,),
    ).fetchone()
    table_n = conn.execute(
        "SELECT count(*)::int FROM leads WHERE org_id = %s",
        (org_id,),
    ).fetchone()
    assert sums is not None and table_n is not None
    meta = {
        "dok": int(sums[0] or 0),
        "den": int(sums[1] or 0),
        "ula": int(sums[2] or 0),
        "cat_total": int(sums[3] or 0),
        "aktif": int(sums[4] or 0),
        "empty_status": int(sums[5] or 0),
        "table_leads": int(table_n[0] or 0),
    }
    return out, meta


def metric_gorusme_suresi(
    conn: psycopg.Connection, org_id: str
) -> tuple[list[tuple[str, float, float, int]], dict[str, float | int]]:
    """ULASILDI lead'lerde 10 sn+ outbound süreleri (görüşme başına).

    Temsilci: lead'in owner_rep_id. Ortalama/medyan çağrı satırından.
    """
    rows = conn.execute(
        f"""
        SELECT r.full_name,
          avg({_DUR})::float AS avg_sec,
          percentile_cont(0.5) WITHIN GROUP (
            ORDER BY {_DUR}
          )::float AS med_sec,
          count(*)::int AS n
        FROM events e
        JOIN leads l
          ON l.org_id = e.org_id AND l.thread_id = e.thread_id
        JOIN reps r
          ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE e.org_id = %s
          AND {_REACH} = '{ULASILDI}'
          AND r.category = 'sales' AND r.active = true
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
          AND {_DUR} >= {TEMAS_MIN_SEC}
          AND e.occurred_at <= now()
        GROUP BY r.full_name
        ORDER BY avg({_DUR}) DESC NULLS LAST, r.full_name
        """,
        (org_id,),
    ).fetchall()
    out = [
        (str(n), float(avg_s), float(med_s), int(c))
        for n, avg_s, med_s, c in rows
        if avg_s is not None and med_s is not None
    ]
    team = conn.execute(
        f"""
        SELECT
          avg({_DUR})::float,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DUR})::float,
          count(*)::int
        FROM events e
        JOIN leads l
          ON l.org_id = e.org_id AND l.thread_id = e.thread_id
        JOIN reps r
          ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE e.org_id = %s
          AND {_REACH} = '{ULASILDI}'
          AND r.category = 'sales' AND r.active = true
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
          AND {_DUR} >= {TEMAS_MIN_SEC}
          AND e.occurred_at <= now()
        """,
        (org_id,),
    ).fetchone()
    no_talk = conn.execute(
        f"""
        SELECT count(*)::int
        FROM leads l
        JOIN reps r
          ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND {_REACH} = '{ULASILDI}'
          AND r.category = 'sales' AND r.active = true
          AND NOT EXISTS (
            SELECT 1 FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call' AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND {_DUR} >= {TEMAS_MIN_SEC}
              AND e.occurred_at <= now()
          )
        """,
        (org_id,),
    ).fetchone()
    meta: dict[str, float | int] = {
        "team_avg": float(team[0]) if team and team[0] is not None else 0.0,
        "team_med": float(team[1]) if team and team[1] is not None else 0.0,
        "team_n": int(team[2] or 0) if team else 0,
        "no_talk": int(no_talk[0] or 0) if no_talk else 0,
    }
    return out, meta


def _randevu_open_sql() -> str:
    """Açık dönülmemiş randevu (latest randevu, sonrası ulaşılmış çağrı yok)."""
    return f"""
    WITH randevu AS (
        SELECT
            e.org_id,
            e.thread_id,
            e.rep_id,
            e.occurred_at AS randevu_at,
            row_number() OVER (
                PARTITION BY e.org_id, e.thread_id
                ORDER BY e.occurred_at DESC
            ) AS rn
        FROM events e
        WHERE e.org_id = %s
          AND (
              e.meta->>'outcome_key' = 'meeting_booked'
              OR e.meta->>'call_result' = 'Randevu Alındı'
          )
          AND e.occurred_at <= now()
    ),
    open_r AS (
        SELECT
            r.org_id,
            r.thread_id,
            r.randevu_at,
            coalesce(r.rep_id, t.owner_rep_id) AS rep_id
        FROM randevu r
        JOIN threads t ON t.org_id = r.org_id AND t.thread_id = r.thread_id
        WHERE r.rn = 1
          AND coalesce(r.rep_id, t.owner_rep_id) IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM events e
              {_JOIN}
              WHERE e.org_id = r.org_id
                AND e.thread_id = r.thread_id
                AND e.occurred_at > r.randevu_at
                AND e.occurred_at <= now()
                AND e.channel = 'call'
                AND e.direction = 'outbound'
                AND {_TEMAS}
          )
    )
    """


def metric_donulmemis(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
) -> tuple[dict[str, int], int]:
    """total açık; bu hafta eklenen; bu hafta kapanan."""
    base = _randevu_open_sql()
    total_row = conn.execute(
        base + " SELECT count(*)::int FROM open_r",
        (org_id,),
    ).fetchone()
    total = int(total_row[0] or 0)

    added_row = conn.execute(
        base
        + """
        SELECT count(*)::int FROM open_r
        WHERE randevu_at >= %s AND randevu_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    added = int(added_row[0] or 0)

    # Kapanan: randevu sonrası ilk ulaşılmış çağrı bu haftada.
    closed_row = conn.execute(
        f"""
        WITH randevu AS (
            SELECT
                e.org_id,
                e.thread_id,
                e.occurred_at AS randevu_at,
                row_number() OVER (
                    PARTITION BY e.org_id, e.thread_id
                    ORDER BY e.occurred_at DESC
                ) AS rn
            FROM events e
            WHERE e.org_id = %s
              AND (
                  e.meta->>'outcome_key' = 'meeting_booked'
                  OR e.meta->>'call_result' = 'Randevu Alındı'
              )
              AND e.occurred_at <= now()
        ),
        first_temas AS (
            SELECT r.thread_id, r.randevu_at,
                   min(e.occurred_at) AS temas_at
            FROM randevu r
            JOIN events e
              ON e.org_id = r.org_id AND e.thread_id = r.thread_id
            {_JOIN}
            WHERE r.rn = 1
              AND e.occurred_at > r.randevu_at
              AND e.occurred_at <= now()
              AND e.channel = 'call' AND e.direction = 'outbound'
              AND {_TEMAS}
            GROUP BY r.thread_id, r.randevu_at
        )
        SELECT count(*)::int FROM first_temas
        WHERE temas_at >= %s AND temas_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    closed = int(closed_row[0] or 0)
    n = total + added + closed
    return {"total": total, "added": added, "closed": closed}, n


def metric_arama_verimi(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
    tz: ZoneInfo,
) -> tuple[dict[str, Any], int]:
    """1/2/3. deneme temas oranı; 15:00+ temas oranı ve çağrı sayısı.

    Deneme numarası: thread'in scheduled-olmayan outbound geçmişi (süre yok).
    Hafta filtresi yalnız o hafta gerçekleşen satırları seçer.

    Temas: call_outcomes.category <> 'not_reached'.
    category NULL ise temas sayılmaz.
    """
    temas = "cat IS NOT NULL AND cat <> 'not_reached'"
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                e.thread_id,
                e.occurred_at,
                co.category AS cat,
                row_number() OVER (
                    PARTITION BY e.org_id, e.thread_id
                    ORDER BY e.occurred_at ASC
                ) AS attempt_n,
                (e.occurred_at AT TIME ZONE %s)::time AS local_time
            FROM events e
            {_JOIN}
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND r.category = 'sales' AND r.active = true
              AND e.channel = 'call' AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.thread_id IS NOT NULL
              AND e.occurred_at <= now()
        ),
        calls AS (
            SELECT * FROM ranked
            WHERE occurred_at >= %s AND occurred_at < %s
        )
        SELECT
          count(*) FILTER (WHERE attempt_n = 1)::int AS a1,
          count(*) FILTER (
            WHERE attempt_n = 1 AND {temas}
          )::int AS t1,
          count(*) FILTER (WHERE attempt_n = 2)::int AS a2,
          count(*) FILTER (
            WHERE attempt_n = 2 AND {temas}
          )::int AS t2,
          count(*) FILTER (WHERE attempt_n = 3)::int AS a3,
          count(*) FILTER (
            WHERE attempt_n = 3 AND {temas}
          )::int AS t3,
          count(*) FILTER (WHERE local_time >= time '15:00')::int AS after_n,
          count(*) FILTER (
            WHERE local_time >= time '15:00' AND {temas}
          )::int AS after_t
        FROM calls
        """,
        (str(tz), org_id, week.start, week.end),
    ).fetchone()
    assert rows is not None
    a1, t1, a2, t2, a3, t3, after_n, after_t = (int(x or 0) for x in rows)
    data = {
        "a1": a1, "t1": t1,
        "a2": a2, "t2": t2,
        "a3": a3, "t3": t3,
        "after_n": after_n, "after_t": after_t,
    }
    return data, a1 + a2 + a3


def metric_hareket(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
) -> tuple[dict[str, int], int, dict[str, str]]:
    """Haftalık hareket + tutulan/bozulan meta (tarih alanı).

    tutulan: o hafta fulfilled sayılan — first_fulfill.occurred_at
      (due_at sonrası ilk scheduled-olmayan call; status bayat olabilir).
    bozulan: o hafta broken sayılan — due_at pencerede, vade geçmiş,
      sonrası call yok (status hâlâ open olsa da).
    """
    calls = conn.execute(
        f"""
        SELECT
          count(*)::int AS calls,
          count(*) FILTER (WHERE {_DUR} >= {TEMAS_MIN_SEC})::int AS talks10
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND r.category = 'sales' AND r.active = true
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s AND e.occurred_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    n_calls = int(calls[0] or 0) if calls else 0
    n_10 = int(calls[1] or 0) if calls else 0

    yeni = conn.execute(
        """
        SELECT count(*)::int
        FROM commitments c
        JOIN events e ON e.id = c.source_event_id
        WHERE c.org_id = %s
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s AND e.occurred_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    n_yeni = int(yeni[0] or 0) if yeni else 0

    tutulan = conn.execute(
        """
        WITH first_fulfill AS (
            SELECT c.id, min(e.occurred_at) AS fulfilled_at
            FROM commitments c
            JOIN events e
              ON e.org_id = c.org_id
             AND e.thread_id = c.thread_id
             AND e.channel = 'call'
             AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
             AND c.due_at IS NOT NULL
             AND e.occurred_at > c.due_at
             AND e.occurred_at <= now()
            WHERE c.org_id = %s
            GROUP BY c.id
        )
        SELECT count(*)::int FROM first_fulfill
        WHERE fulfilled_at >= %s AND fulfilled_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    n_tut = int(tutulan[0] or 0) if tutulan else 0

    bozulan = conn.execute(
        """
        SELECT count(*)::int FROM commitments c
        WHERE c.org_id = %s
          AND c.due_at >= %s AND c.due_at < %s
          AND c.due_at < now()
          AND NOT EXISTS (
              SELECT 1 FROM events e
              WHERE e.org_id = c.org_id
                AND e.thread_id = c.thread_id
                AND e.channel = 'call'
                AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
                AND e.occurred_at > c.due_at
                AND e.occurred_at <= now()
          )
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    n_boz = int(bozulan[0] or 0) if bozulan else 0

    # status sütunu bayat mı? (debug/karşılaştırma)
    status_tut = conn.execute(
        """
        SELECT count(*)::int
        FROM commitments c
        JOIN events e ON e.id = c.fulfilled_event_id
        WHERE c.org_id = %s
          AND c.status = 'fulfilled'
          AND e.occurred_at >= %s AND e.occurred_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    status_boz = conn.execute(
        """
        SELECT count(*)::int FROM commitments c
        WHERE c.org_id = %s
          AND c.status = 'broken'
          AND c.due_at >= %s AND c.due_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()

    data = {
        "calls": n_calls,
        "talks10": n_10,
        "yeni": n_yeni,
        "tutulan": n_tut,
        "bozulan": n_boz,
        "status_tutulan": int(status_tut[0] or 0) if status_tut else 0,
        "status_bozulan": int(status_boz[0] or 0) if status_boz else 0,
    }
    meta = {
        "tutulan_field": "first_fulfill.occurred_at",
        "bozulan_field": "due_at (vade geçmiş, sonrası call yok)",
        "window": _window_str(week),
    }
    return data, n_calls + n_yeni + n_tut + n_boz, meta


def metric_satis(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    """Kazanılan: sıfır / tekrar / atıfsız; temsilci sıfır; medyan döngü."""
    cte = classified_deals_cte()
    won = won_stage_sql("cl")
    week_filter = f"""
        {won}
        AND cl.closed_at IS NOT NULL
        AND cl.closed_at <= now()
        AND cl.closed_at >= %s AND cl.closed_at < %s
    """
    row = conn.execute(
        f"""
        WITH {cte}
        SELECT
          count(*) FILTER (WHERE cl.kind = '{SIFIR}')::int,
          count(*) FILTER (WHERE cl.kind = '{TEKRAR}')::int,
          count(*) FILTER (WHERE cl.kind = '{ATIFSIZ}')::int
        FROM classified cl
        WHERE {week_filter}
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    n_sifir = int(row[0] or 0) if row else 0
    n_tekrar = int(row[1] or 0) if row else 0
    n_atif = int(row[2] or 0) if row else 0

    four_start, four_end = _four_week_window(week)
    atif_4w_row = conn.execute(
        f"""
        WITH {cte}
        SELECT count(*)::int
        FROM classified cl
        WHERE {week_filter}
          AND cl.kind = '{ATIFSIZ}'
        """,
        (org_id, four_start, four_end),
    ).fetchone()
    n_atif_4w = int(atif_4w_row[0] or 0) if atif_4w_row else 0

    per_rep = conn.execute(
        f"""
        WITH {cte}
        SELECT r.full_name, count(*)::int
        FROM classified cl
        JOIN reps r
          ON r.org_id = cl.org_id AND r.rep_id = cl.lead_owner_rep_id
        WHERE {week_filter}
          AND cl.kind = '{SIFIR}'
          AND r.category = 'sales' AND r.active = true
        GROUP BY r.full_name
        ORDER BY count(*) DESC, r.full_name
        """,
        (org_id, week.start, week.end),
    ).fetchall()
    reps = [(str(n), int(c)) for n, c in per_rep]

    cycle = conn.execute(
        f"""
        WITH {cte}
        SELECT
          percentile_cont(0.5) WITHIN GROUP (
            ORDER BY extract(epoch FROM (cl.closed_at - cl.cycle_start_at))
                     / 86400.0
          )::float,
          count(*)::int
        FROM classified cl
        WHERE {week_filter}
          AND cl.kind = '{SIFIR}'
          AND cl.cycle_start_reliable IS TRUE
          AND cl.cycle_start_at IS NOT NULL
          AND cl.closed_at > cl.cycle_start_at
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    med_days = float(cycle[0]) if cycle and cycle[0] is not None else None
    n_cycle = int(cycle[1] or 0) if cycle else 0

    mismatch = conn.execute(
        f"""
        WITH {cte}
        SELECT
          count(*) FILTER (
            WHERE cl.deal_owner_rep_id IS NOT NULL
              AND cl.lead_owner_rep_id IS NOT NULL
          )::int AS both_owners,
          count(*) FILTER (
            WHERE cl.deal_owner_rep_id IS NOT NULL
              AND cl.lead_owner_rep_id IS NOT NULL
              AND cl.deal_owner_rep_id IS DISTINCT FROM cl.lead_owner_rep_id
          )::int AS mismatch
        FROM classified cl
        """,
        (org_id,),
    ).fetchone()
    avg_row = conn.execute(
        """
        SELECT avg(n)::float
        FROM (
            SELECT contact_id, count(*)::int AS n
            FROM deals
            WHERE org_id = %s AND contact_id IS NOT NULL
            GROUP BY contact_id
        ) t
        """,
        (org_id,),
    ).fetchone()

    data: dict[str, Any] = {
        "sifir": n_sifir,
        "tekrar": n_tekrar,
        "atifsiz": n_atif,
        "atifsiz_4w": n_atif_4w,
        "reps": reps,
        "cycle_med": med_days,
        "cycle_n": n_cycle,
    }
    debug = {
        "owner_both": int(mismatch[0] or 0) if mismatch else 0,
        "owner_mismatch": int(mismatch[1] or 0) if mismatch else 0,
        "avg_deals_per_contact": (
            float(avg_row[0]) if avg_row and avg_row[0] is not None else 0.0
        ),
    }
    n = n_sifir + n_tekrar + n_atif
    return data, n, debug


def metric_temsilci_ozeti(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
) -> tuple[list[RepOzet], dict[str, Any]]:
    """Satış temsilcisi satırları; sıra 90 günlük sıfır satış."""
    four_start, four_end = _four_week_window(week)
    cycle_start, cycle_end = _days_window(week, 90)
    lo_join = _latest_lead_owner_join("c")
    cte = classified_deals_cte()
    won = won_stage_sql("cl")
    sales = "r.category = 'sales' AND r.active = true"

    names = [name for _, name in _sales_reps(conn, org_id)]
    if not names:
        return [], {}

    def by_name(rows: list[tuple[Any, ...]]) -> dict[str, int]:
        return {str(r[0]): int(r[1]) for r in rows}

    port_rows = conn.execute(
        f"""
        SELECT r.full_name, count(*)::int
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s AND {sales}
        GROUP BY r.full_name
        """,
        (org_id,),
    ).fetchall()
    portfolio = by_name(port_rows)

    sifir_rows = conn.execute(
        f"""
        WITH {cte}
        SELECT r.full_name, count(*)::int
        FROM classified cl
        JOIN reps r
          ON r.org_id = cl.org_id AND r.rep_id = cl.lead_owner_rep_id
        WHERE {won}
          AND cl.kind = '{SIFIR}'
          AND cl.closed_at IS NOT NULL
          AND cl.closed_at <= now()
          AND cl.closed_at >= %s AND cl.closed_at < %s
          AND {sales}
        GROUP BY r.full_name
        """,
        (org_id, cycle_start, cycle_end),
    ).fetchall()
    sifir_90 = by_name(sifir_rows)

    alltime_row = conn.execute(
        f"""
        WITH {cte}
        SELECT
          count(*) FILTER (WHERE cl.kind = '{SIFIR}')::int,
          count(*) FILTER (WHERE cl.kind = '{TEKRAR}')::int,
          count(*) FILTER (WHERE cl.kind = '{ATIFSIZ}')::int,
          count(*)::int
        FROM classified cl
        WHERE {won}
          AND cl.closed_at IS NOT NULL
          AND cl.closed_at <= now()
        """,
        (org_id,),
    ).fetchone()
    alltime_sifir = int(alltime_row[0] or 0) if alltime_row else 0
    alltime_tekrar = int(alltime_row[1] or 0) if alltime_row else 0
    alltime_atifsiz = int(alltime_row[2] or 0) if alltime_row else 0
    alltime_won = int(alltime_row[3] or 0) if alltime_row else 0

    cycle_rows = conn.execute(
        f"""
        WITH {cte}
        SELECT r.full_name,
          percentile_cont(0.5) WITHIN GROUP (
            ORDER BY extract(epoch FROM (cl.closed_at - cl.cycle_start_at))
                     / 86400.0
          )::float,
          count(*)::int
        FROM classified cl
        JOIN reps r
          ON r.org_id = cl.org_id AND r.rep_id = cl.lead_owner_rep_id
        WHERE {won}
          AND cl.kind = '{SIFIR}'
          AND cl.cycle_start_reliable IS TRUE
          AND cl.cycle_start_at IS NOT NULL
          AND cl.closed_at > cl.cycle_start_at
          AND cl.closed_at IS NOT NULL
          AND cl.closed_at <= now()
          AND cl.closed_at >= %s AND cl.closed_at < %s
          AND {sales}
        GROUP BY r.full_name
        """,
        (org_id, cycle_start, cycle_end),
    ).fetchall()
    cycle_med = {
        str(n): float(m) for n, m, _c in cycle_rows if m is not None
    }
    cycle_n = {str(n): int(c) for n, _m, c in cycle_rows}

    team_cycle = conn.execute(
        f"""
        WITH {cte}
        SELECT
          percentile_cont(0.5) WITHIN GROUP (
            ORDER BY extract(epoch FROM (cl.closed_at - cl.cycle_start_at))
                     / 86400.0
          )::float,
          count(*)::int
        FROM classified cl
        JOIN reps r
          ON r.org_id = cl.org_id AND r.rep_id = cl.lead_owner_rep_id
        WHERE {won}
          AND cl.kind = '{SIFIR}'
          AND cl.cycle_start_reliable IS TRUE
          AND cl.cycle_start_at IS NOT NULL
          AND cl.closed_at > cl.cycle_start_at
          AND cl.closed_at IS NOT NULL
          AND cl.closed_at <= now()
          AND cl.closed_at >= %s AND cl.closed_at < %s
          AND {sales}
        """,
        (org_id, cycle_start, cycle_end),
    ).fetchone()
    team_cycle_med = (
        float(team_cycle[0])
        if team_cycle and team_cycle[0] is not None
        else None
    )
    team_cycle_n = int(team_cycle[1] or 0) if team_cycle else 0

    call_rows = conn.execute(
        f"""
        SELECT r.full_name,
          count(*) FILTER (
            WHERE e.occurred_at >= %s AND e.occurred_at < %s
          )::int AS calls_week,
          count(*)::int AS calls_4w,
          count(*) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC}
          )::int AS talks10,
          avg({_DUR}) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC}
          )::float AS avg_sec,
          (percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DUR})
            FILTER (WHERE {_DUR} >= {TEMAS_MIN_SEC}))::float AS med_sec
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND {sales}
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
          AND e.occurred_at <= now()
          AND e.occurred_at >= %s AND e.occurred_at < %s
        GROUP BY r.full_name
        """,
        (week.start, week.end, org_id, four_start, four_end),
    ).fetchall()
    calls_week = {str(n): int(w) for n, w, _c4, _t, _a, _m in call_rows}
    calls_4w = {str(n): int(c4) for n, _w, c4, _t, _a, _m in call_rows}
    talks10 = {str(n): int(t) for n, _w, _c4, t, _a, _m in call_rows}
    talk_avg = {
        str(n): float(a)
        for n, _w, _c4, _t, a, _m in call_rows
        if a is not None
    }
    talk_med = {
        str(n): float(m)
        for n, _w, _c4, _t, _a, m in call_rows
        if m is not None
    }

    four_bounds = WeekBounds(start=four_start, end=four_end, label="")
    disc_rows, _n_disc = metric_kayit_disiplini(conn, org_id, four_bounds)
    disc = {n: (f, t) for n, f, t in disc_rows}

    acik_rows = conn.execute(
        f"""
        SELECT r.full_name, count(*)::int
        FROM commitments c
        {lo_join}
        JOIN reps r
          ON r.org_id = c.org_id AND r.rep_id = lo.owner_rep_id
        WHERE c.org_id = %s
          AND c.status = 'open'
          AND lo.owner_rep_id IS NOT NULL
          AND {sales}
        GROUP BY r.full_name
        """,
        (org_id,),
    ).fetchall()
    acik = by_name(acik_rows)

    bozulan_rows = conn.execute(
        f"""
        SELECT r.full_name, count(*)::int
        FROM commitments c
        {lo_join}
        JOIN reps r
          ON r.org_id = c.org_id AND r.rep_id = lo.owner_rep_id
        WHERE c.org_id = %s
          AND c.due_at >= %s AND c.due_at < %s
          AND c.due_at < now()
          AND lo.owner_rep_id IS NOT NULL
          AND {sales}
          AND NOT EXISTS (
              SELECT 1 FROM events e
              WHERE e.org_id = c.org_id
                AND e.thread_id = c.thread_id
                AND e.channel = 'call'
                AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
                AND e.occurred_at > c.due_at
                AND e.occurred_at <= now()
          )
        GROUP BY r.full_name
        """,
        (org_id, four_start, four_end),
    ).fetchall()
    bozulan = by_name(bozulan_rows)

    base = _randevu_open_sql()
    don_rows = conn.execute(
        base
        + f"""
        SELECT r.full_name, count(*)::int
        FROM open_r
        JOIN reps r
          ON r.org_id = open_r.org_id AND r.rep_id = open_r.rep_id
        WHERE {sales}
        GROUP BY r.full_name
        """,
        (org_id,),
    ).fetchall()
    donulmemis = by_name(don_rows)

    out: list[RepOzet] = []
    for name in names:
        filled, total = disc.get(name, (0, 0))
        n_week = calls_week.get(name, 0)
        n_4w = calls_4w.get(name, 0)
        avg_4w = (n_4w / 4.0) if n_4w > 0 else 0.0
        low = avg_4w > 0 and n_week < 0.20 * avg_4w
        out.append(
            RepOzet(
                name=name,
                portfolio=portfolio.get(name, 0),
                sifir_90=sifir_90.get(name, 0),
                calls_week=n_week,
                calls=n_4w,
                talks10=talks10.get(name, 0),
                talk_avg=talk_avg.get(name),
                talk_med=talk_med.get(name),
                cycle_med=cycle_med.get(name),
                cycle_n=cycle_n.get(name, 0),
                disc_filled=filled,
                disc_total=total,
                acik=acik.get(name, 0),
                bozulan=bozulan.get(name, 0),
                donulmemis=donulmemis.get(name, 0),
                low_activity=low,
            )
        )
    out.sort(key=lambda r: (-r.sifir_90, r.name))
    meta: dict[str, Any] = {
        "alltime_sifir": alltime_sifir,
        "alltime_tekrar": alltime_tekrar,
        "alltime_atifsiz": alltime_atifsiz,
        "alltime_won": alltime_won,
        "team_cycle_med": team_cycle_med,
        "team_cycle_n": team_cycle_n,
    }
    return out, meta


def _format_temsilci_ozeti(
    rows: list[RepOzet], meta: dict[str, Any]
) -> list[str]:
    """Cliq mobil: sütun hizası yok, temsilci başına yığın."""
    parts = ["TEMSİLCİ ÖZETİ"]
    team_n = int(meta.get("team_cycle_n", 0) or 0)
    team_med = meta.get("team_cycle_med")
    if team_n < _MIN_SAMPLE or team_med is None:
        cycle_s = "veri yetersiz"
    else:
        cycle_s = _fmt_cycle_days(float(team_med))
    parts.append(
        f"Satış döngüsü medyanı {cycle_s}; sonuç metrikleri geçmiş "
        "faaliyetin meyvesidir."
    )
    if not rows:
        parts.append("(veri yok)")
        return parts
    for row in rows:
        p = row.portfolio
        prefix = "(düşük aktivite) " if row.low_activity else ""
        parts.append("")
        parts.append(f"{prefix}{row.name} — {p} aktif lead")
        parts.append("FAALİYET (son 4 hafta)")
        parts.append(f"çağrı — {_fmt_count_port(row.calls, p)}")
        parts.append(f"10 sn+ — {_fmt_count_port(row.talks10, p)}")
        if (
            row.talks10 < _MIN_SAMPLE
            or row.talk_avg is None
            or row.talk_med is None
        ):
            parts.append(f"süre — veri yetersiz ({row.talks10} görüşme)")
        else:
            parts.append(
                "süre — ort. "
                f"{_fmt_talk_sec(row.talk_avg)}, "
                f"medyan {_fmt_talk_sec(row.talk_med)}"
            )
        if row.disc_total < _MIN_SAMPLE:
            parts.append(
                f"disiplin — veri yetersiz ({row.disc_total} görüşme)"
            )
        else:
            parts.append(
                f"disiplin — {_fmt_pct_short(row.disc_filled, row.disc_total)}"
            )
        parts.append(
            "açık / bozulan taahhüt (anlık / 4 hafta) — "
            f"{_fmt_count_port(row.acik, p)} / "
            f"{_fmt_count_port(row.bozulan, p)}"
        )
        parts.append(
            f"dönülmemiş randevu (anlık) — {_fmt_count_port(row.donulmemis, p)}"
        )
        parts.append("SONUÇ (son 90 gün)")
        parts.append(f"sıfır satış — {_fmt_count_port(row.sifir_90, p)}")
        if row.cycle_n < _MIN_SAMPLE or row.cycle_med is None:
            parts.append(
                f"döngü — veri yetersiz ({row.cycle_n} satış)"
            )
        else:
            parts.append(
                f"döngü — {_fmt_cycle_days(row.cycle_med)} "
                f"({row.cycle_n} satış)"
            )
    return parts


def _latest_week_ago_with_calls(
    conn: psycopg.Connection, org_id: str, tz: ZoneInfo
) -> int:
    """En son outbound olan takvim haftasının weeks_ago değeri."""
    row = conn.execute(
        f"""
        SELECT date_trunc(
                 'week',
                 (e.occurred_at AT TIME ZONE %s)
               )::date AS w
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND r.category = 'sales' AND r.active = true
          AND e.channel = 'call' AND e.direction = 'outbound'
          AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
          AND e.occurred_at <= now()
        ORDER BY w DESC
        LIMIT 1
        """,
        (str(tz), org_id),
    ).fetchone()
    if not row or not row[0]:
        return 0
    last_monday: date = row[0]
    today_monday = datetime.now(tz).date() - timedelta(
        days=datetime.now(tz).weekday()
    )
    delta_days = (today_monday - last_monday).days
    return max(0, delta_days // 7)


def build_report(
    conn: psycopg.Connection,
    org_id: str,
    *,
    weeks_ago: int = 0,
    auto_last_data: bool = True,
    debug: bool = False,
) -> tuple[str, dict[str, int], int]:
    """Rapor metni, metrik→kayit_sayisi, hata."""
    errors = 0
    counts: dict[str, int] = {}
    tz = _org_tz(conn, org_id)
    used_ago = weeks_ago
    if auto_last_data:
        probe = _week_bounds(tz, weeks_ago=weeks_ago)
        n_probe = conn.execute(
            f"""
            SELECT count(*)::int FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s AND r.category = 'sales'
              AND e.channel = 'call' AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.occurred_at <= now()
              AND e.occurred_at >= %s AND e.occurred_at < %s
            """,
            (org_id, probe.start, probe.end),
        ).fetchone()
        prev_w = _week_bounds(tz, weeks_ago=weeks_ago + 1)
        n_prev = conn.execute(
            f"""
            SELECT count(*)::int FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s AND r.category = 'sales'
              AND e.channel = 'call' AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.occurred_at <= now()
              AND e.occurred_at >= %s AND e.occurred_at < %s
            """,
            (org_id, prev_w.start, prev_w.end),
        ).fetchone()
        if int(n_probe[0] or 0) == 0 and int(n_prev[0] or 0) == 0:
            latest = _latest_week_ago_with_calls(conn, org_id, tz)
            if latest > weeks_ago:
                used_ago = latest
                if debug:
                    print(
                        f"debug: seçilen haftalarda çağrı yok → "
                        f"son veri haftası weeks_ago={used_ago}"
                    )
    this_w = _week_bounds(tz, weeks_ago=used_ago)
    last_w = _week_bounds(tz, weeks_ago=used_ago + 1)
    this_label = _week_label_long(this_w)
    last_label = _week_label_long(last_w)

    parts: list[str] = [
        "Pusula — haftalık rapor",
        f"{this_label} (kiyas: {last_label})",
        "",
    ]

    # a) Kayıt disiplini
    try:
        this_d, n_this = metric_kayit_disiplini(
            conn, org_id, this_w, debug=debug
        )
        last_d, n_last = metric_kayit_disiplini(
            conn, org_id, last_w, debug=False
        )
        counts["kayit_disiplini"] = n_this + n_last
        parts.append("KAYIT DİSİPLİNİ")
        parts.append("10 saniyeden uzun görüşmelerde sonuç girilme oranı")
        last_map = {n: (f, t) for n, f, t in last_d}
        this_map = {n: (f, t) for n, f, t in this_d}

        def disc_key(n: str) -> tuple[float, str]:
            f, t = this_map.get(n, (0, 0))
            if t < _MIN_SAMPLE:
                return (999.0, n)
            return (f / t, n)

        names = sorted(set(this_map) | set(last_map), key=disc_key)
        if not names:
            parts.append("(veri yok)")
        else:
            for name in names:
                tf, tt = this_map.get(name, (0, 0))
                lf, lt = last_map.get(name, (0, 0))
                if tt <= 0 and lt <= 0:
                    continue
                parts.append(_rate_row(name, tf, tt, lf, lt, unit="görüşme"))
        if debug:
            parts.append(f"kayit={n_this} bu / {n_last} onceki hafta")
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"KAYIT DİSİPLİNİ: hata ({exc})")
        parts.append("")

    # b) Lead durumu
    try:
        rows, meta = metric_lead_durumu(conn, org_id)
        counts["lead_durumu"] = meta["cat_total"]
        parts.append("LEAD DURUMU")
        parts.append(
            "dokunulmamış / denendi / ulaşıldı (Zoho status). "
            "aktif lead = satış sahibi"
        )
        if not rows:
            parts.append("(veri yok)")
        else:
            for name, dok, den, ula, tot in rows:
                if tot <= 0:
                    continue
                parts.append(
                    f"{name} — {dok} / {den} / {ula} ({tot} aktif lead)"
                )
        parts.append(
            f"Toplam — {meta['dok']} / {meta['den']} / {meta['ula']} "
            f"({meta['aktif']} aktif lead)"
        )
        parts.append(
            f"Lead tablosu — {meta['table_leads']} "
            f"(aktif {meta['aktif']}, "
            f"status boş {meta['empty_status']})"
        )
        if meta["dok"] + meta["den"] + meta["ula"] != meta["cat_total"]:
            parts.append(
                "veri yetersiz: kova toplamı statusu dolu satış lead ile örtüşmüyor"
            )
        if debug:
            parts.append(
                f"kayit={meta['cat_total']} "
                f"dok+den+ula={meta['dok'] + meta['den'] + meta['ula']}"
            )
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"LEAD DURUMU: hata ({exc})")
        parts.append("")

    # c) Görüşme süresi (ULASILDI, 10 sn+)
    try:
        rows_gs, gs = metric_gorusme_suresi(conn, org_id)
        counts["gorusme_suresi"] = int(gs["team_n"])
        parts.append("GÖRÜŞME SÜRESİ")
        parts.append("Ulaşılan lead'lerde 10 sn üstü görüşmelerin süresi")
        if not rows_gs:
            parts.append("(veri yok)")
        else:
            for name, avg_s, med_s, n_talk in rows_gs:
                if n_talk < _MIN_SAMPLE:
                    parts.append(f"{name} — veri yetersiz ({n_talk} görüşme)")
                    continue
                parts.append(
                    f"{name} — ort. {_fmt_talk_sec(avg_s)}, "
                    f"medyan {_fmt_talk_sec(med_s)} ({n_talk} görüşme)"
                )
        team_n = int(gs["team_n"])
        if team_n < _MIN_SAMPLE:
            parts.append(f"Ekip ortalaması — veri yetersiz ({team_n} görüşme)")
        else:
            parts.append(
                f"Ekip ortalaması — {_fmt_talk_sec(float(gs['team_avg']))}, "
                f"medyan {_fmt_talk_sec(float(gs['team_med']))}"
            )
        parts.append(
            f"Ulaşıldı, 10 sn+ çağrı yok — {int(gs['no_talk'])}"
        )
        if debug:
            parts.append(f"kayit={team_n} gorusme / no_talk={gs['no_talk']}")
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"GÖRÜŞME SÜRESİ: hata ({exc})")
        parts.append("")

    # d) Dönülmemiş randevu
    try:
        this_r, n1 = metric_donulmemis(conn, org_id, this_w)
        last_r, n2 = metric_donulmemis(conn, org_id, last_w)
        counts["donulmemis_randevu"] = n1 + n2
        parts.append("DÖNÜLMEMİŞ RANDEVU")
        parts.append("Randevu alınmış, sonrası ulaşılmış çağrı yok")
        parts.append(f"Açık toplam — {this_r['total']}")
        parts.append(
            f"Bu hafta eklendi — {this_r['added']} "
            f"(önceki hafta {last_r['added']})"
        )
        parts.append(
            f"Bu hafta kapandı — {this_r['closed']} "
            f"(önceki hafta {last_r['closed']})"
        )
        net = this_r["added"] - this_r["closed"]
        net_s = f"+{net}" if net > 0 else str(net)
        if net > 0:
            note = "yığın büyüyor"
        elif net < 0:
            note = "yığın küçülüyor"
        else:
            note = "yığın aynı"
        parts.append(f"Net değişim — {net_s} ({note})")
        if debug:
            parts.append(f"kayit={n1} bu / {n2} onceki hafta")
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"DÖNÜLMEMİŞ RANDEVU: hata ({exc})")
        parts.append("")

    # e) Arama verimi
    try:
        this_v, n1 = metric_arama_verimi(conn, org_id, this_w, tz)
        last_v, n2 = metric_arama_verimi(conn, org_id, last_w, tz)
        counts["arama_verimi"] = n1 + n2
        parts.append("ARAMA VERİMİ")
        parts.append("Temas: ulaşılabilmiş görüşme (süre yok)")
        parts.append("Deneme sırası lead ömrü boyunca")

        def attempt(label: str, t_key: str, a_key: str) -> str:
            return _rate_row(
                label,
                this_v[t_key],
                this_v[a_key],
                last_v[t_key],
                last_v[a_key],
                unit="çağrı",
            )

        parts.append(attempt("1. deneme temas", "t1", "a1"))
        parts.append(attempt("2. deneme temas", "t2", "a2"))
        parts.append(attempt("3. deneme temas", "t3", "a3"))
        parts.append(
            _rate_row(
                "15:00+ temas",
                this_v["after_t"],
                this_v["after_n"],
                last_v["after_t"],
                last_v["after_n"],
                unit="çağrı",
            )
        )
        if debug:
            parts.append(f"kayit={n1} bu / {n2} onceki hafta")
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"ARAMA VERİMİ: hata ({exc})")
        parts.append("")

    # f) Hareket
    try:
        this_h, n1, this_meta = metric_hareket(conn, org_id, this_w)
        last_h, n2, last_meta = metric_hareket(conn, org_id, last_w)
        counts["haftanin_hareketi"] = n1 + n2
        parts.append("HAFTANIN HAREKETİ")
        rows_h = (
            ("Toplam çağrı", "calls"),
            ("10sn+ görüşme", "talks10"),
            ("Yeni taahhüt", "yeni"),
            ("Tutulan", "tutulan"),
            ("Bozulan", "bozulan"),
        )
        for label, key in rows_h:
            parts.append(_count_cmp(label, this_h[key], last_h[key]))
        if debug:
            parts.append(
                f"tutulan tarih alanı: {this_meta['tutulan_field']} "
                f"[{this_meta['window']}]"
            )
            parts.append(
                f"bozulan tarih alanı: {this_meta['bozulan_field']} "
                f"[{this_meta['window']}]"
            )
            parts.append(f"kıyas penceresi: [{last_meta['window']}]")
            if (
                this_h["tutulan"] == last_h["tutulan"]
                or this_h["bozulan"] == last_h["bozulan"]
            ):
                parts.append(
                    "not: tutulan/bozulan iki haftada aynı; "
                    "hafta filtresi uygulandı (id kümeleri ayrı). "
                    f"status bayat "
                    f"(fulfilled_event={this_h['status_tutulan']}/"
                    f"{last_h['status_tutulan']}, "
                    f"broken+due_at={this_h['status_bozulan']}/"
                    f"{last_h['status_bozulan']})."
                )
            parts.append(f"kayit={n1} bu / {n2} onceki hafta")
            parts.append(
                f"ham tutulan={this_h['tutulan']} / {last_h['tutulan']}, "
                f"bozulan={this_h['bozulan']} / {last_h['bozulan']}"
            )
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"HAFTANIN HAREKETİ: hata ({exc})")
        parts.append("")

    # g) Satış
    try:
        this_s, n1, this_dbg = metric_satis(conn, org_id, this_w)
        last_s, n2, _last_dbg = metric_satis(conn, org_id, last_w)
        counts["satis"] = n1 + n2
        parts.append("SATIŞ")
        parts.append("Bu hafta kazanılan — sıfır / tekrar / atıfsız")
        parts.append(
            f"{this_s['sifir']} / {this_s['tekrar']} / {this_s['atifsiz']} "
            f"(önceki hafta {last_s['sifir']} / {last_s['tekrar']} / "
            f"{last_s['atifsiz']})"
        )
        parts.append("Sıfır satış (lead sahibi)")
        reps = this_s["reps"]
        if not reps:
            parts.append("(veri yok)")
        else:
            for name, c in reps:
                parts.append(f"{name} — {c}")
        n_cyc = int(this_s["cycle_n"])
        med = this_s["cycle_med"]
        if n_cyc < _MIN_SAMPLE or med is None:
            parts.append(
                f"Medyan satış döngüsü — veri yetersiz ({n_cyc} kayıt)"
            )
        else:
            parts.append(
                f"Medyan satış döngüsü — {_fmt_cycle_days(float(med))} "
                f"({n_cyc} kayıt)"
            )
        parts.append("hedef: sıfır satış sayısı artmalı")
        parts.append(
            "atıfsız deal (bu hafta / 4 hafta) — "
            f"{this_s['atifsiz']} / {this_s['atifsiz_4w']}"
        )
        if debug:
            parts.append(
                f"deal sahibi ≠ lead sahibi — "
                f"{this_dbg['owner_mismatch']}/{this_dbg['owner_both']}"
            )
            parts.append(
                f"contact başına ortalama deal — "
                f"{this_dbg['avg_deals_per_contact']:.2f}"
            )
            parts.append(f"kayit={n1} bu / {n2} onceki hafta")
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"SATIŞ: hata ({exc})")
        parts.append("")

    # h) Temsilci özeti
    try:
        ozet, ozet_meta = metric_temsilci_ozeti(conn, org_id, this_w)
        counts["temsilci_ozeti"] = len(ozet)
        parts.extend(_format_temsilci_ozeti(ozet, ozet_meta))
        if debug:
            parts.append(
                "tüm zaman kazanılan — "
                f"{ozet_meta['alltime_won']} "
                f"(sıfır {ozet_meta['alltime_sifir']} / "
                f"tekrar {ozet_meta['alltime_tekrar']} / "
                f"atıfsız {ozet_meta['alltime_atifsiz']})"
            )
            for row in ozet:
                parts.append(
                    f"{row.name}: aktif={row.portfolio} "
                    f"sifir90={row.sifir_90} "
                    f"cagri={row.calls_week}/{row.calls} "
                    f"10sn={row.talks10} dongu_n={row.cycle_n} "
                    f"disiplin={row.disc_total} "
                    f"acik={row.acik} bozulan={row.bozulan} "
                    f"randevu={row.donulmemis} "
                    f"dusuk={row.low_activity}"
                )
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"TEMSİLCİ ÖZETİ: hata ({exc})")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n", counts, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Haftalık yönetici raporu")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Cliq'e gölge DM gönder",
    )
    parser.add_argument(
        "--hafta",
        choices=("onceki", "bu"),
        default="onceki",
        help=(
            "onceki=tamamlanmış son hafta (varsayılan); "
            "bu=içinde bulunulan devam eden hafta"
        ),
    )
    parser.add_argument(
        "--weeks-ago",
        type=int,
        default=None,
        help="Gelişmiş: pazartesi kaydırması (verilirse --hafta yerine geçer)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Kayıt sayıları, ham örnekler ve teknik notlar",
    )
    args = parser.parse_args()
    load_dotenv()

    database_url = (
        os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
    )
    if not database_url:
        print("DATABASE_URL_POOLED / DATABASE_URL yok")
        return 1

    recipients = _parse_recipients()
    if not recipients:
        print(
            "UYARI: PUSULA_REPORT_RECIPIENTS bos — "
            "--apply kimseye gondermez (orn. email1,email2)"
        )
    elif args.debug:
        print(f"PUSULA_REPORT_RECIPIENTS: {len(recipients)} ({', '.join(recipients)})")

    dry_run = not args.apply
    webhook_url = os.environ.get("CLIQ_WEBHOOK_URL")
    shadow_email = os.environ.get("PUSULA_SHADOW_EMAIL")
    if not dry_run:
        missing = [
            n
            for n, v in (
                ("CLIQ_WEBHOOK_URL", webhook_url),
                ("PUSULA_SHADOW_EMAIL", shadow_email),
            )
            if not v
        ]
        if missing:
            print("eksik ortam değişkeni: " + ", ".join(missing))
            return 1
        if not recipients:
            print("PUSULA_REPORT_RECIPIENTS boş — gönderim iptal")
            return 1

    if args.weeks_ago is not None:
        if args.weeks_ago < 0:
            print("--weeks-ago >= 0 olmali")
            return 1
        weeks_ago = args.weeks_ago
        # Explicit kaydırma: otomatik son-veri kayması yok.
        auto_last = False
    else:
        weeks_ago = _hafta_to_weeks_ago(args.hafta)
        # onceki: veri yoksa geri kay; bu: devam eden haftayı olduğu gibi göster.
        auto_last = args.hafta == "onceki"

    org_id = get_org_id()
    try:
        with psycopg.connect(database_url, prepare_threshold=None) as conn:
            print_call_stale_warning(conn, org_id)
            if args.debug:
                reps = _sales_reps(conn, org_id)
                print(f"sales={len(reps)}: " + ", ".join(n for _, n in reps))
                print(
                    f"hafta={args.hafta if args.weeks_ago is None else 'weeks-ago'} "
                    f"weeks_ago={weeks_ago}"
                )
            text, counts, errors = build_report(
                conn,
                org_id,
                weeks_ago=weeks_ago,
                auto_last_data=auto_last,
                debug=args.debug,
            )
    except psycopg.Error as exc:
        print(f"hata: {exc}")
        return 1

    print(text)
    if args.debug:
        print("--- kayit sayilari ---")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        print(f"hata={errors}")

    if dry_run:
        if args.debug:
            print("dry-run: gönderilmedi. Yazmak için --apply kullan.")
        return 0 if errors == 0 else 1

    assert webhook_url and shadow_email
    # Gölge mod: gerçek alıcı yerine shadow; raporda intended listesi.
    header = (
        f"[gölge] intended=" + ", ".join(recipients) + "\n\n"
    )
    try:
        _post_cliq(webhook_url, header + text, shadow_email)
        print(f"gönderilen=1 (shadow={shadow_email})")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"hata (cliq): {exc}")
        return 1
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
