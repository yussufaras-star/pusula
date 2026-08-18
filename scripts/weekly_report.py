"""Haftalık yönetici raporu — Cliq DM (gölge mod).

Metrikler (özetlenen hafta / bir önceki), yalnız sayı:
  a) Kayıt disiplini — 10 sn+ outbound'ta call_result dolu %
  b) Hiç aranmamış lead — active/stale/aging, sayılabilir outbound yok
  c) Dönülmemiş randevu — Randevu Alındı, 10 sn+ temas yok
  d) Arama verimi — 1/2/3. denemede temas %; 15:00+ temas % ve çağrı
  e) Haftanın hareketi — çağrı, 10 sn+, bağlanmadı, yeni/tutulan/bozulan taahhüt

Temas: pusula.temas (send_nudges ile aynı).
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
from pusula.temas import (
    CALL_MIN_SEC,
    TEMAS_MIN_SEC,
    TEMAS_MIN_SEC_OLD,
    duration_sec,
    is_countable_call_sql,
    is_temas_sql,
    outcome_join,
)

_DUR = duration_sec("e")
_JOIN = outcome_join("e")
_COUNTABLE = is_countable_call_sql("e")
_TEMAS = is_temas_sql("e")
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


def _disiplin_row(
    name: str,
    new_num: int,
    new_den: int,
    old_num: int,
    old_den: int,
) -> str:
    """Abdullah Benli — %12 (eski %0, 95 görüşme)."""
    if new_den < _MIN_SAMPLE:
        return f"{name} — veri yetersiz ({new_den} görüşme)"
    new_s = _fmt_pct_short(new_num, new_den)
    old_s = _fmt_pct_short(old_num, old_den)
    return f"{name} — {new_s} (eski {old_s}, {new_den} görüşme)"


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
) -> tuple[list[tuple[str, int, int, int, int]], int]:
    """(name, filled_new, total_new, filled_old, total_old)."""
    rows = conn.execute(
        f"""
        SELECT r.full_name,
          count(*) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC}
              AND nullif(e.meta->>'call_result', '') IS NOT NULL
          )::int AS filled_new,
          count(*) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC}
          )::int AS total_new,
          count(*) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC_OLD}
              AND nullif(e.meta->>'call_result', '') IS NOT NULL
          )::int AS filled_old,
          count(*) FILTER (
            WHERE {_DUR} >= {TEMAS_MIN_SEC_OLD}
          )::int AS total_old
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
    out = [
        (str(n), int(fn), int(tn), int(fo), int(to))
        for n, fn, tn, fo, to in rows
    ]
    if debug:
        for name, filled, total, _fo, _to in out:
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
    return out, sum(t for _, _, t, _, _ in out)


def metric_hic_aranmamis(
    conn: psycopg.Connection, org_id: str
) -> tuple[list[tuple[str, int]], int]:
    """Snapshot: deneme (süre > 0) outbound yok."""
    rows = conn.execute(
        f"""
        SELECT r.full_name, count(*)::int
        FROM leads l
        JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
        WHERE l.org_id = %s
          AND l.pusula_state IN ('active', 'stale', 'aging')
          AND l.owner_rep_id IS NOT NULL
          AND l.thread_id IS NOT NULL
          AND r.category = 'sales' AND r.active = true
          AND NOT EXISTS (
            SELECT 1 FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND e.occurred_at <= now()
              AND {_COUNTABLE}
          )
        GROUP BY r.full_name
        ORDER BY 2 DESC, r.full_name
        """,
        (org_id,),
    ).fetchall()
    out = [(str(n), int(c)) for n, c in rows]
    return out, sum(c for _, c in out)


def _randevu_open_sql(
    *,
    min_sec: int,
    after_cmp: str,
    legacy: bool = False,
) -> str:
    """Açık dönülmemiş randevu thread'leri.

    after_cmp '>=': randevu anındaki çağrı da temas sayılır (10 sn tanımı).
    after_cmp '>': yalnız randevu sonrası (eski 30 sn raporu).
    legacy: eski kategori kuralı (boş outcome temas sayılırdı).
    """
    if legacy:
        temas = f"""
                coalesce(e.meta->>'scheduled', 'false') <> 'true'
                AND {_DUR} >= {min_sec}
                AND coalesce(co.category, '') <> 'not_reached'
        """
    else:
        temas = is_temas_sql("e", min_sec=min_sec)
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
                AND e.occurred_at {after_cmp} r.randevu_at
                AND e.occurred_at <= now()
                AND e.channel = 'call'
                AND e.direction = 'outbound'
                AND {temas}
          )
    )
    """


def metric_donulmemis(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
) -> tuple[dict[str, int], int]:
    """total açık (yeni/eski tanım); bu hafta eklenen; bu hafta kapanan."""
    base_new = _randevu_open_sql(min_sec=TEMAS_MIN_SEC, after_cmp=">=")
    base_old = _randevu_open_sql(
        min_sec=TEMAS_MIN_SEC_OLD, after_cmp=">", legacy=True
    )
    total_row = conn.execute(
        base_new + " SELECT count(*)::int FROM open_r",
        (org_id,),
    ).fetchone()
    total = int(total_row[0] or 0)
    old_row = conn.execute(
        base_old + " SELECT count(*)::int FROM open_r",
        (org_id,),
    ).fetchone()
    total_old = int(old_row[0] or 0)

    added_row = conn.execute(
        base_new
        + """
        SELECT count(*)::int FROM open_r
        WHERE randevu_at >= %s AND randevu_at < %s
        """,
        (org_id, week.start, week.end),
    ).fetchone()
    added = int(added_row[0] or 0)

    # Kapanan: randevu sonrası ilk temas bu haftada (randevu anı hariç).
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
    return {
        "total": total,
        "total_old": total_old,
        "added": added,
        "closed": closed,
    }, n


def metric_arama_verimi(
    conn: psycopg.Connection,
    org_id: str,
    week: WeekBounds,
    tz: ZoneInfo,
) -> tuple[dict[str, Any], int]:
    """1/2/3. deneme temas oranı; 15:00+ temas oranı ve çağrı sayısı.

    Deneme numarası: thread'in süre > 0 outbound geçmişi (bağlanmadı hariç).
    Hafta filtresi yalnız o hafta gerçekleşen satırları seçer.

    Temas: süre >= 10 VE call_outcomes.category <> 'not_reached'.
    category NULL ise temas sayılmaz (SQL üç değerli mantık; coalesce yok).
    """
    # coalesce(cat,'') kullanma — boş outcome'u temas yapıyordu (~%80).
    temas = (
        f"dur >= {TEMAS_MIN_SEC} AND cat IS NOT NULL AND cat <> 'not_reached'"
    )
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                e.thread_id,
                e.occurred_at,
                {_DUR} AS dur,
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
              AND {_DUR} > 0
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
          count(*) FILTER (WHERE {_DUR} > 0)::int AS calls,
          count(*) FILTER (WHERE {_DUR} >= {TEMAS_MIN_SEC})::int AS talks10,
          count(*) FILTER (
            WHERE {_DUR} IS NULL OR {_DUR} = 0
          )::int AS baglanmadi
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
    n_bag = int(calls[2] or 0) if calls else 0

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
        "baglanmadi": n_bag,
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


def _latest_week_ago_with_calls(
    conn: psycopg.Connection, org_id: str, tz: ZoneInfo
) -> int:
    """En son outbound (≥10sn) olan takvim haftasının weeks_ago değeri."""
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
          AND {_DUR} >= {CALL_MIN_SEC}
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
              AND {_DUR} >= {CALL_MIN_SEC}
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
              AND {_DUR} >= {CALL_MIN_SEC}
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
        _, n_last = metric_kayit_disiplini(
            conn, org_id, last_w, debug=False
        )
        counts["kayit_disiplini"] = n_this + n_last
        parts.append("KAYIT DİSİPLİNİ")
        parts.append("10 saniyeden uzun görüşmelerde sonuç girilme oranı")
        this_map = {n: (fn, tn, fo, to) for n, fn, tn, fo, to in this_d}

        def disc_key(n: str) -> tuple[float, str]:
            fn, tn, _fo, _to = this_map.get(n, (0, 0, 0, 0))
            if tn < _MIN_SAMPLE:
                return (999.0, n)
            return (fn / tn, n)

        names = sorted(this_map, key=disc_key)
        if not names:
            parts.append("(veri yok)")
        else:
            for name in names:
                fn, tn, fo, to = this_map[name]
                if tn <= 0 and to <= 0:
                    continue
                parts.append(_disiplin_row(name, fn, tn, fo, to))
        if debug:
            parts.append(f"kayit={n_this} bu / {n_last} onceki hafta")
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"KAYIT DİSİPLİNİ: hata ({exc})")
        parts.append("")

    # b) Hiç aranmamış
    try:
        rows, n = metric_hic_aranmamis(conn, org_id)
        counts["hic_aranmamis"] = n
        parts.append("HİÇ ARANMAMIŞ LEAD")
        parts.append("Aktif/stale/aging, sayılabilir outbound yok")
        if not rows:
            parts.append("(veri yok)")
        else:
            for name, c in rows:
                parts.append(f"{name} — {c}")
        if debug:
            parts.append(f"kayit={n}")
        parts.append("")
    except Exception as exc:
        errors += 1
        parts.append(f"HİÇ ARANMAMIŞ LEAD: hata ({exc})")
        parts.append("")

    # c) Dönülmemiş randevu
    try:
        this_r, n1 = metric_donulmemis(conn, org_id, this_w)
        last_r, n2 = metric_donulmemis(conn, org_id, last_w)
        counts["donulmemis_randevu"] = n1 + n2
        parts.append("DÖNÜLMEMİŞ RANDEVU")
        parts.append("Randevu alınmış, 10 sn+ ulaşılmış görüşme yok")
        parts.append(
            f"Açık toplam — {this_r['total']} (eski {this_r['total_old']})"
        )
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

    # d) Arama verimi
    try:
        this_v, n1 = metric_arama_verimi(conn, org_id, this_w, tz)
        last_v, n2 = metric_arama_verimi(conn, org_id, last_w, tz)
        counts["arama_verimi"] = n1 + n2
        parts.append("ARAMA VERİMİ")
        parts.append("Temas: 10 sn üstü ve ulaşılabilmiş görüşme")
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

    # e) Hareket
    try:
        this_h, n1, this_meta = metric_hareket(conn, org_id, this_w)
        last_h, n2, last_meta = metric_hareket(conn, org_id, last_w)
        counts["haftanin_hareketi"] = n1 + n2
        parts.append("HAFTANIN HAREKETİ")
        rows_h = (
            ("Toplam çağrı", "calls"),
            ("10sn+ görüşme", "talks10"),
            ("Bağlanmadı", "baglanmadi"),
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
