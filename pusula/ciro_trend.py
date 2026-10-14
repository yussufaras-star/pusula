"""Ciro trendi: haftalık ve aylık ekip cirosu, önceki döneme göre % değişim.

Kaynak deal_packages (prim kartı ve Raporlar'la aynı: TL, "Kapandı Kazanıldı",
kapanış günü). Seriler:
- Haftalar (pazartesi başlar) son 12 hafta; bu hafta sürüyorsa işaretli.
- Aylar Nisan 2026'dan (CRM öncesi güvenilmez); ay toplamı ileri tarihli
  kapanışları da sayar (yöneticinin aylık toplam tanımı).
- "Aynı güne kadar": bu hafta pazartesi→bugün ile geçen hafta pazartesi→aynı
  gün; bu ay 1→bugün ile geçen ay 1→aynı gün. Süren dönemi tam dönemle
  kıyaslamak yanıltır, bu yüzden karar bu kıyasa bakılarak verilir.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from pusula.config import get_org_id
from pusula.panel_ciro import REAL_SALES_START, add_months, month_first, month_label
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

WEEKS = 12
_WON_DAY = "coalesce(closing_date, (created_at AT TIME ZONE 'Europe/Istanbul')::date)"

Sale = tuple[str, date, int, float]  # rep_id, gün, adet, ciro


def change(cur: float, prev: float) -> float | None:
    """% değişim; önceki 0 ise tanımsız."""
    if not prev:
        return None
    return round(100 * (cur - prev) / prev, 1)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def same_day_last_month(today: date) -> date:
    prev = add_months(month_first(today), -1)
    last = (month_first(today) - timedelta(days=1)).day
    return prev.replace(day=min(today.day, last))


def _sum(sales: Iterable[Sale], start: date, end: date) -> tuple[int, float]:
    n, c = 0, 0.0
    for _, day, cnt, ciro in sales:
        if start <= day <= end:
            n += cnt
            c += ciro
    return n, c


def to_date(sales: list[Sale], today: date) -> dict[str, Any]:
    """Saf: bu hafta / bu ay aynı güne kadar, öncekiyle."""
    wk = week_start(today)
    lw_start, lw_end = wk - timedelta(days=7), today - timedelta(days=7)
    m = month_first(today)
    lm_start, lm_end = add_months(m, -1), same_day_last_month(today)
    out: dict[str, Any] = {}
    for key, (a0, a1, b0, b1) in {
        "hafta": (wk, today, lw_start, lw_end),
        "ay": (m, today, lm_start, lm_end),
    }.items():
        n, c = _sum(sales, a0, a1)
        pn, pc = _sum(sales, b0, b1)
        out[key] = {
            "ciro": round(c, 2), "satis": n, "onceki_ciro": round(pc, 2), "onceki_satis": pn,
            "degisim": change(c, pc), "satis_degisim": change(n, pn),
            "bas": a0, "onceki_bas": b0, "onceki_bit": b1,
        }
    return out


def weekly(sales: list[Sale], today: date, weeks: int = WEEKS) -> list[dict[str, Any]]:
    """Saf: son N hafta (bu hafta dahil), önceki haftaya göre değişim."""
    by: dict[date, list[float]] = defaultdict(lambda: [0, 0.0])
    for _, day, cnt, ciro in sales:
        if day <= today:
            b = by[week_start(day)]
            b[0] += cnt
            b[1] += ciro
    first = week_start(today) - timedelta(weeks=weeks)  # bir fazlası: ilk haftanın değişimi için
    rows = []
    prev = None
    wk = first
    while wk <= week_start(today):
        n, c = by.get(wk, [0, 0.0])
        if wk >= max(first + timedelta(weeks=1), week_start(REAL_SALES_START)):
            rows.append({
                "bas": wk, "bit": wk + timedelta(days=6), "satis": int(n), "ciro": round(c, 2),
                "degisim": change(c, prev) if prev is not None else None,
                "suruyor": wk == week_start(today),
            })
        prev = c
        wk += timedelta(weeks=1)
    return rows


def monthly(sales: list[Sale], today: date) -> list[dict[str, Any]]:
    """Saf: Nisan 2026'dan bu aya; ay toplamı ileri tarihli kapanış dahil."""
    by: dict[date, list[float]] = defaultdict(lambda: [0, 0.0])
    for _, day, cnt, ciro in sales:
        b = by[month_first(day)]
        b[0] += cnt
        b[1] += ciro
    rows = []
    prev = None
    m = REAL_SALES_START
    while m <= month_first(today):
        n, c = by.get(m, [0, 0.0])
        rows.append({
            "ay": m, "ay_etiket": month_label(m), "satis": int(n), "ciro": round(c, 2),
            "degisim": change(c, prev) if prev is not None else None,
            "suruyor": m == month_first(today),
        })
        prev = c
        m = add_months(m, 1)
    return rows


def build_trend(sales: list[Sale], rep_ids: list[str], today: date) -> dict[str, Any]:
    by_rep: dict[str, list[Sale]] = defaultdict(list)
    for s in sales:
        by_rep[s[0]].append(s)
    return {
        "haftalar": weekly(sales, today),
        "aylar": monthly(sales, today),
        "ayni_gune": to_date(sales, today),
        "temsilci": {rid: to_date(by_rep.get(rid, []), today) for rid in rep_ids},
    }


def load_trend(rep_ids: list[str], today: date) -> dict[str, Any]:
    start = min(REAL_SALES_START, week_start(today) - timedelta(weeks=WEEKS + 1))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT owner_rep_id, {_WON_DAY}, count(*)::int, coalesce(sum(amount), 0)::float
            FROM deal_packages
            WHERE org_id = %s AND stage = %s AND owner_rep_id = ANY(%s) AND {_WON_DAY} >= %s
            GROUP BY 1, 2
            """,
            (get_org_id(), WON_STAGE, rep_ids, start),
        ).fetchall()
    sales = [(str(r), d, int(n), float(c)) for r, d, n, c in rows if d]
    return build_trend(sales, rep_ids, today)
