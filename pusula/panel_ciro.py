"""Ciro: kazanılan anlaşmalar, ilk/tekrar ayrımı yok.

Kaynak deals. owner_rep_id. Satış ekibi ile satış sonrası ekip ayrılır.
Satışa dönme oranı hesaplarına dokunulmaz.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pusula.config import get_org_id
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

_TZ = ZoneInfo("Europe/Istanbul")
CIRO_START = datetime(2026, 1, 1, tzinfo=_TZ)
# Gerçek satış dönemi; ingest artıkları (Ocak–Mart 2026) hariç.
REAL_SALES_START = date(2026, 4, 1)
MONTH_COUNT = 12
SUNDAY = 6

PeriodKey = Literal[
    "bu_ay",
    "gecen_ay",
    "son_3_ay",
    "son_6_ay",
    "tum_zamanlar",
    "ozel",
]

CIRO_PERIODS: tuple[tuple[PeriodKey, str], ...] = (
    ("bu_ay", "Bu ay"),
    ("gecen_ay", "Geçen ay"),
    ("son_3_ay", "Son 3 ay"),
    ("son_6_ay", "Son 6 ay"),
    ("tum_zamanlar", "Tüm zamanlar"),
    ("ozel", "Özel aralık"),
)

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


def month_first(day: date) -> date:
    return date(day.year, day.month, 1)


def month_last(day: date) -> date:
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def add_months(day: date, delta: int) -> date:
    """Ayın birine kaydır."""
    year = day.year
    month = day.month + delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return date(year, month, 1)


def is_workday(day: date) -> bool:
    """Pazar hariç her gün; cumartesi iş günüdür."""
    return day.weekday() != SUNDAY


def workdays_inclusive(start: date, end: date) -> int:
    """Kapalı aralıkta pazar dışı gün sayısı."""
    if end < start:
        return 0
    n = 0
    cur = start
    one = timedelta(days=1)
    while cur <= end:
        if is_workday(cur):
            n += 1
        cur += one
    return n


def workdays_in_month(year: int, month: int) -> int:
    start = date(year, month, 1)
    return workdays_inclusive(start, month_last(start))


def nth_workday(month_start: date, n: int) -> date | None:
    """Ayın n. iş günü. n < 1 veya ay yetmezse None."""
    if n < 1:
        return None
    last = month_last(month_start)
    seen = 0
    cur = month_first(month_start)
    one = timedelta(days=1)
    while cur <= last:
        if is_workday(cur):
            seen += 1
            if seen == n:
                return cur
        cur += one
    return None


def clamp_ciro_start(start: date) -> date:
    return start if start >= REAL_SALES_START else REAL_SALES_START


def resolve_ciro_period(
    key: str,
    today: date,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> tuple[date, date]:
    """Seçilen dönemin kapalı [start, end] aralığı. Nisan 2026 öncesi kesilir."""
    allowed = {item[0] for item in CIRO_PERIODS}
    if key not in allowed:
        key = "bu_ay"
    month0 = month_first(today)
    if key == "bu_ay":
        start, end = month0, today
    elif key == "gecen_ay":
        prev = add_months(month0, -1)
        start, end = prev, month_last(prev)
    elif key == "son_3_ay":
        start, end = add_months(month0, -2), today
    elif key == "son_6_ay":
        start, end = add_months(month0, -5), today
    elif key == "tum_zamanlar":
        start, end = REAL_SALES_START, today
    elif key == "ozel":
        start = custom_start or month0
        end = custom_end or today
        if start > end:
            start, end = end, start
    else:
        start, end = month0, today
    start = clamp_ciro_start(start)
    if end > today:
        end = today
    if end < start:
        end = start
    return start, end


def _window_ts(start: date, end: date) -> tuple[datetime, datetime]:
    """[start 00:00, end+1 00:00) Istanbul; bitiş now()'u aşmaz."""
    start_ts = datetime.combine(start, time.min, tzinfo=_TZ)
    end_ts = datetime.combine(end + timedelta(days=1), time.min, tzinfo=_TZ)
    now = datetime.now(_TZ)
    if end_ts > now:
        end_ts = now
    return start_ts, end_ts


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


def month_label(value: datetime | date) -> str:
    if isinstance(value, datetime):
        local = value if value.tzinfo is None else value.astimezone(_TZ)
        return f"{_MONTH_TR[local.month - 1]} {local.year}"
    return f"{_MONTH_TR[value.month - 1]} {value.year}"


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


def _won_window_sql(alias: str = "d") -> str:
    at = _deal_at_sql(alias)
    return f"""
        {alias}.org_id = %s
        AND {alias}.stage = %s
        AND {at} >= %s
        AND {at} < %s
        AND {alias}.owner_rep_id = ANY(%s)
    """


def ciro_by_rep(
    kind: TeamKind, start: date, end: date
) -> list[dict[str, Any]]:
    """Seçilen kapalı aralıkta temsilci, satış, ciro, ortalama."""
    ids = _team_ids(kind)
    org_id = get_org_id()
    start_ts, end_ts = _window_ts(start, end)
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
         AND {_deal_at_sql("d")} < %s
        GROUP BY r.rep_id, r.full_name, t.ord
        ORDER BY t.ord
    """
    with connect() as conn:
        rows = conn.execute(
            sql,
            (list(ids), org_id, WON_STAGE, start_ts, end_ts),
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


def ciro_ytd_by_rep(kind: TeamKind) -> list[dict[str, Any]]:
    """2026 başından bugüne: temsilci, satış, ciro, ortalama."""
    today = datetime.now(_TZ).date()
    return ciro_by_rep(kind, CIRO_START.date(), today)


def ciro_team_totals(start: date, end: date) -> tuple[float, int]:
    """Satış ekibi kapalı aralık toplam ciro ve adet."""
    org_id = get_org_id()
    start_ts, end_ts = _window_ts(start, end)
    sql = f"""
        SELECT coalesce(sum(d.amount), 0)::float, count(*)::int
        FROM deals d
        WHERE {_won_window_sql("d")}
    """
    with connect() as conn:
        row = conn.execute(
            sql,
            (org_id, WON_STAGE, start_ts, end_ts, list(SALES_TEAM_IDS)),
        ).fetchone()
    if not row:
        return 0.0, 0
    return float(row[0] or 0), int(row[1] or 0)


def ciro_monthly_by_rep(
    kind: TeamKind,
    *,
    rep_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Ay × temsilci. start/end doluysa veri olmayan ay basılmaz."""
    ids = _team_ids(kind)
    if rep_id is not None:
        if kind == "sales" and rep_id not in SALES_TEAM_IDS:
            return []
        ids = (rep_id,)
    org_id = get_org_id()
    if start is None or end is None:
        return _ciro_monthly_filled(ids, org_id)
    return _ciro_monthly_windowed(ids, org_id, start, end)


def _ciro_monthly_filled(
    ids: tuple[str, ...], org_id: str
) -> list[dict[str, Any]]:
    """Son 12 ay, boş aylar sıfır. Performans profili için."""
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
    return _monthly_rows(rows)


def _ciro_monthly_windowed(
    ids: tuple[str, ...], org_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    """Penceredeki aylar; satış yoksa satır yok. Nisan 2026 öncesi yok."""
    start_ts, end_ts = _window_ts(start, end)
    floor = datetime(
        REAL_SALES_START.year, REAL_SALES_START.month, REAL_SALES_START.day
    )
    sql = f"""
        WITH won AS (
            SELECT
              d.owner_rep_id AS rep_id,
              date_trunc(
                'month',
                {_deal_at_sql("d")} AT TIME ZONE 'Europe/Istanbul'
              ) AS month_start,
              count(*)::int AS satis,
              coalesce(sum(d.amount), 0)::float AS ciro
            FROM deals d
            WHERE {_won_window_sql("d")}
            GROUP BY 1, 2
        ),
        months AS (
            SELECT DISTINCT month_start
            FROM won
            WHERE month_start >= %s::timestamp
        ),
        roster AS (
            SELECT r.rep_id, r.full_name, t.ord
            FROM unnest(%s::text[]) WITH ORDINALITY AS t(rep_id, ord)
            JOIN reps r ON r.org_id = %s AND r.rep_id = t.rep_id
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
        org_id,
        WON_STAGE,
        start_ts,
        end_ts,
        list(ids),
        floor,
        list(ids),
        org_id,
    )
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return _monthly_rows(rows)


def _monthly_rows(rows: list[Any]) -> list[dict[str, Any]]:
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


def with_monthly_team_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Her ayın temsilcilerinden sonra ekip toplamı satırı."""
    if not rows:
        return []
    grouped: dict[datetime, list[dict[str, Any]]] = {}
    order: list[datetime] = []
    for row in rows:
        month = row.get("ay")
        if not isinstance(month, datetime):
            continue
        if month not in grouped:
            grouped[month] = []
            order.append(month)
        grouped[month].append(row)
    out: list[dict[str, Any]] = []
    for month in order:
        group = grouped[month]
        out.extend(group)
        satis = sum(int(r["satis"]) for r in group)
        total = sum(float(r["ciro"]) for r in group)
        out.append(
            {
                "rep_id": "",
                "temsilci": "Ekip toplamı",
                "ay": month,
                "ay_etiket": group[0]["ay_etiket"],
                "satis": satis,
                "ciro": total,
                "ortalama": _avg_deal(total, satis),
                "is_total": True,
            }
        )
    return out


def ciro_team_monthly(
    start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
    """Satış ekibi aylık toplam ciro."""
    rows = ciro_monthly_by_rep("sales", start=start, end=end)
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
        if start is not None and n == 0:
            continue
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


def ciro_rep_monthly(
    rep_id: str,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Tek temsilcinin aylık cirosu (satış ekibi üyesi)."""
    return ciro_monthly_by_rep("sales", rep_id=rep_id, start=start, end=end)


def ciro_month_forecast(today: date | None = None) -> dict[str, Any] | None:
    """Ay içi izdüşüm: gün başına ciro × ayın iş günü. Yalnız ekip toplamı."""
    day = today or datetime.now(_TZ).date()
    month_start = month_first(day)
    if month_start < REAL_SALES_START:
        return None
    elapsed = workdays_inclusive(month_start, day)
    month_wd = workdays_in_month(day.year, day.month)
    mtd, satis = ciro_team_totals(month_start, day)
    daily = (mtd / float(elapsed)) if elapsed > 0 else None
    forecast = (daily * float(month_wd)) if daily is not None else None
    return {
        "today": day,
        "month_start": month_start,
        "mtd_ciro": mtd,
        "satis": satis,
        "elapsed_workdays": elapsed,
        "month_workdays": month_wd,
        "daily_ciro": daily,
        "forecast": forecast,
    }


def ciro_last_complete_months(
    n: int = 3, today: date | None = None
) -> list[dict[str, Any]]:
    """Kapanmış n ay, eskiden yeniye. Nisan 2026 öncesi yok."""
    day = today or datetime.now(_TZ).date()
    cursor = add_months(month_first(day), -1)
    found: list[dict[str, Any]] = []
    while len(found) < n and cursor >= REAL_SALES_START:
        last = month_last(cursor)
        ciro, satis = ciro_team_totals(cursor, last)
        found.append(
            {
                "ay": cursor,
                "ay_etiket": month_label(cursor),
                "start": cursor,
                "end": last,
                "ciro": ciro,
                "satis": satis,
            }
        )
        cursor = add_months(cursor, -1)
    found.reverse()
    return found


def ciro_complete_months_avg(
    n: int = 3, today: date | None = None
) -> dict[str, Any]:
    rows = ciro_last_complete_months(n, today)
    if not rows:
        return {"n": 0, "ortalama": None, "aylar": []}
    total = sum(float(r["ciro"]) for r in rows)
    return {
        "n": len(rows),
        "ortalama": total / float(len(rows)),
        "aylar": rows,
    }


def ciro_same_pace_compare(
    today: date | None = None, prior_months: int = 3
) -> dict[str, Any]:
    """Bu ayın ilk N iş günü vs önceki tam ayların ilk N iş günü."""
    day = today or datetime.now(_TZ).date()
    month_start = month_first(day)
    n_wd = workdays_inclusive(month_start, day)
    this_ciro, this_satis = ciro_team_totals(month_start, day)
    bu_ay: dict[str, Any] = {
        "ay": month_start,
        "ay_etiket": month_label(month_start),
        "start": month_start,
        "end": day,
        "ciro": this_ciro,
        "satis": this_satis,
        "is_current": True,
    }
    prior: list[dict[str, Any]] = []
    cursor = add_months(month_start, -1)
    while len(prior) < prior_months and cursor >= REAL_SALES_START:
        end_d = nth_workday(cursor, n_wd)
        if end_d is None:
            cursor = add_months(cursor, -1)
            continue
        ciro, satis = ciro_team_totals(cursor, end_d)
        prior.append(
            {
                "ay": cursor,
                "ay_etiket": month_label(cursor),
                "start": cursor,
                "end": end_d,
                "ciro": ciro,
                "satis": satis,
                "is_current": False,
            }
        )
        cursor = add_months(cursor, -1)
    prior.reverse()
    return {"n": n_wd, "bu_ay": bu_ay, "onceki": prior}


def ciro_won_month_probe() -> list[dict[str, Any]]:
    """Kazanılan anlaşmalar, ay ay. Doğrulama sorgusu; ekip filtresi yok."""
    sql = """
        SELECT date_trunc('month', coalesce(closed_at, created_at))::date AS ay,
               count(*)::int AS adet,
               count(*) FILTER (WHERE closed_at IS NULL)::int AS closed_at_bos,
               sum(amount) AS ciro
        FROM public.deals
        WHERE stage = %s
        GROUP BY 1
        ORDER BY 1
    """
    with connect() as conn:
        rows = conn.execute(sql, (WON_STAGE,)).fetchall()
    out: list[dict[str, Any]] = []
    for ay, adet, bos, ciro in rows:
        out.append(
            {
                "ay": ay,
                "adet": int(adet),
                "closed_at_bos": int(bos),
                "ciro": float(ciro) if ciro is not None else None,
            }
        )
    return out


def has_prior_year_same_month(rows: list[dict[str, Any]]) -> bool:
    """Bu yılın bir ayı için geçen yıl aynı ayda kayıt var mı."""
    months = set()
    for row in rows:
        ay = row.get("ay")
        if isinstance(ay, datetime):
            months.add((ay.year, ay.month))
        elif isinstance(ay, date):
            months.add((ay.year, ay.month))
    this_year = datetime.now(_TZ).year
    for year, month in months:
        if year == this_year and (this_year - 1, month) in months:
            return True
    return False


def closed_at_bos_ratio(rows: list[dict[str, Any]]) -> float | None:
    total = sum(int(r.get("adet") or 0) for r in rows)
    if total <= 0:
        return None
    bos = sum(int(r.get("closed_at_bos") or 0) for r in rows)
    return float(bos) / float(total)


def ciro_team_year_compare() -> list[dict[str, Any]]:
    """Satış ekibi ay ay: bu yıl, geçen yıl, fark, yüzde. CIRO_START yok."""
    org_id = get_org_id()
    sql = f"""
        SELECT
          date_trunc(
            'month',
            {_deal_at_sql("d")} AT TIME ZONE 'Europe/Istanbul'
          )::date AS ay,
          coalesce(sum(d.amount), 0)::float AS ciro,
          count(*)::int AS adet
        FROM deals d
        WHERE d.org_id = %s
          AND d.stage = %s
          AND d.owner_rep_id = ANY(%s)
        GROUP BY 1
        ORDER BY 1
    """
    with connect() as conn:
        rows = conn.execute(
            sql, (org_id, WON_STAGE, list(SALES_TEAM_IDS))
        ).fetchall()
    by_month: dict[tuple[int, int], dict[str, Any]] = {}
    for ay, ciro, adet in rows:
        if not isinstance(ay, date):
            continue
        by_month[(ay.year, ay.month)] = {
            "ay": ay,
            "ciro": float(ciro or 0),
            "adet": int(adet),
        }
    this_year = datetime.now(_TZ).year
    this_month = datetime.now(_TZ).month
    out: list[dict[str, Any]] = []
    for month in range(1, this_month + 1):
        cur = by_month.get((this_year, month))
        prev = by_month.get((this_year - 1, month))
        this_ciro = float(cur["ciro"]) if cur is not None else 0.0
        prev_ciro = float(prev["ciro"]) if prev is not None else None
        diff: float | None
        pct: float | None
        if prev is None:
            diff = None
            pct = None
        else:
            diff = this_ciro - prev_ciro
            if prev_ciro == 0:
                pct = None
            else:
                pct = round(100.0 * diff / prev_ciro, 1)
        label_dt = datetime(this_year, month, 1, tzinfo=_TZ)
        out.append(
            {
                "ay": datetime(this_year, month, 1, tzinfo=_TZ),
                "ay_etiket": month_label(label_dt),
                "bu_yil": this_ciro,
                "gecen_yil": prev_ciro,
                "fark": diff,
                "yuzde": pct,
            }
        )
    return out


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
