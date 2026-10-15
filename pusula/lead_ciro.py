"""Yeni lead ↔ ciro korelasyonu: günlük / haftalık / aylık / yıllık.

Lead: Zoho'da oluşturulduğu gün (lead_attribution.zoho_created_at; kişiye
dönüştürülenler dahil). Ciro: kazanılan deal'ler (deal_packages, TL, kapanış
günü) — Re-Co-Premium hariç: yenileme mevcut müşteriden gelir, yeni lead'le
ilgisi yoktur. Tüm sahipler sayılır (ayrılan temsilcilerin geçmiş satışları
düşmesin).

Korelasyon Pearson r: aynı dönem (gecikme 0) ve lead'in satışa dönme süresi
için gecikmeli (bu dönemin lead'i ↔ N dönem sonraki ciro). Süren dönem
(yarım) seride gösterilir ama hesaba girmez. r ilişkiyi gösterir, nedeni
değil; az noktayla (< MIN_POINTS) hesaplanmaz.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import sqrt
from typing import Any, Iterable

from pusula.config import get_org_id
from pusula.panel_ciro import add_months, month_first, month_label
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

# deal_packages Ekim 2025'te başlar (CRM); öncesi tek tük kayıt.
SERIES_START = date(2025, 10, 1)
RENEWAL_PACKAGE = "Re-Co-Premium"
GRANS = ("gun", "hafta", "ay", "yil")
DAYS_BACK = 90
WEEKS_BACK = 26
MAX_LAG = {"gun": 14, "hafta": 4, "ay": 2, "yil": 0}
MIN_POINTS = 5

_WON_DAY = "coalesce(closing_date, (created_at AT TIME ZONE 'Europe/Istanbul')::date)"

Daily = dict[date, float]


def bucket(day: date, gran: str) -> date:
    if gran == "hafta":
        return day - timedelta(days=day.weekday())
    if gran == "ay":
        return month_first(day)
    if gran == "yil":
        return date(day.year, 1, 1)
    return day


def next_bucket(start: date, gran: str) -> date:
    if gran == "hafta":
        return start + timedelta(weeks=1)
    if gran == "ay":
        return add_months(start, 1)
    if gran == "yil":
        return date(start.year + 1, 1, 1)
    return start + timedelta(days=1)


def first_bucket(gran: str, today: date) -> date:
    if gran == "gun":
        return max(SERIES_START, today - timedelta(days=DAYS_BACK - 1))
    if gran == "hafta":
        return max(bucket(SERIES_START, "hafta"), bucket(today, "hafta") - timedelta(weeks=WEEKS_BACK - 1))
    return bucket(SERIES_START, gran)


def label(start: date, gran: str) -> str:
    if gran == "ay":
        return month_label(start)
    if gran == "yil":
        return str(start.year)
    return start.isoformat()


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < MIN_POINTS or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if not sxx or not syy:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / sqrt(sxx * syy), 3)


def strength(r: float | None) -> str | None:
    """Yorum: |r| ≥ 0,7 güçlü, ≥ 0,4 orta, ≥ 0,2 zayıf, altı ilişki yok."""
    if r is None:
        return None
    a = abs(r)
    if a < 0.2:
        return "yok"
    kind = "güçlü" if a >= 0.7 else "orta" if a >= 0.4 else "zayıf"
    return f"{kind} {'pozitif' if r > 0 else 'negatif'}"


def series(leads: Daily, ciro: Daily, sales: Daily, gran: str, today: date) -> list[dict[str, Any]]:
    """Saf: dönem başına lead adedi, ciro, satış; süren dönem işaretli."""
    agg: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for i, src in enumerate((leads, ciro, sales)):
        for day, v in src.items():
            agg[bucket(day, gran)][i] += v
    rows = []
    b = first_bucket(gran, today)
    current = bucket(today, gran)
    while b <= current:
        n, c, s = agg.get(b, [0.0, 0.0, 0.0])
        rows.append({
            "bas": b, "bit": next_bucket(b, gran) - timedelta(days=1), "etiket": label(b, gran),
            "lead": int(n), "ciro": round(c, 2), "satis": int(s),
            "ciro_per_lead": round(c / n, 2) if n else None,
            "suruyor": b == current,
        })
        b = next_bucket(b, gran)
    return rows


def lags(rows: list[dict[str, Any]], gran: str) -> list[dict[str, Any]]:
    """Saf: gecikme k için r(lead[t], ciro[t+k]); yalnız tamamlanmış dönemler."""
    done = [r for r in rows if not r["suruyor"]]
    out = []
    for k in range(MAX_LAG[gran] + 1):
        xs = [float(r["lead"]) for r in done[: len(done) - k]] if k else [float(r["lead"]) for r in done]
        ys = [float(r["ciro"]) for r in done[k:]]
        r = pearson(xs, ys)
        out.append({"gecikme": k, "r": r, "n": len(xs), "yorum": strength(r)})
    return out


def build(leads: Daily, ciro: Daily, sales: Daily, gran: str, today: date) -> dict[str, Any]:
    rows = series(leads, ciro, sales, gran, today)
    by_lag = lags(rows, gran)
    same = by_lag[0]
    measured = [x for x in by_lag if x["r"] is not None]
    best = max(measured, key=lambda x: x["r"]) if measured else None
    done = [r for r in rows if not r["suruyor"]]
    total_lead = sum(r["lead"] for r in done)
    total_ciro = sum(r["ciro"] for r in done)
    return {
        "gran": gran,
        "seri": rows,
        "r": same["r"],
        "yorum": same["yorum"],
        "n": same["n"],
        "gecikmeler": by_lag,
        "en_iyi": best,
        "lead_basi_ciro": round(total_ciro / total_lead, 2) if total_lead else None,
        "min_nokta": MIN_POINTS,
    }


def _daily(rows: Iterable[tuple[Any, ...]]) -> tuple[Daily, Daily]:
    a: Daily = {}
    b: Daily = {}
    for day, x, y in rows:
        if day:
            a[day] = float(x)
            b[day] = float(y)
    return a, b


def load_lead_ciro(gran: str, today: date) -> dict[str, Any]:
    org_id = get_org_id()
    # Yıllık/haftalık ilk dönem SERIES_START'tan önce başlayabilir; iki taraf da
    # aynı günden sayılsın (2025 = Ekim–Aralık).
    start = max(first_bucket(gran, today), SERIES_START)
    end = next_bucket(bucket(today, gran), gran)
    with connect() as conn:
        has = conn.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'lead_attribution' AND column_name = 'zoho_created_at'
            """
        ).fetchone()
        if not has:
            return {"available": False, "gran": gran}
        lead_rows = conn.execute(
            """
            SELECT (zoho_created_at AT TIME ZONE 'Europe/Istanbul')::date, count(*), 0
            FROM lead_attribution
            WHERE org_id = %s AND zoho_created_at >= %s AND zoho_created_at < %s
            GROUP BY 1
            """,
            (org_id, start, end),
        ).fetchall()
        # Günlük seride ileri tarihli kapanış bugünden sonraya düşer; ay/yıl
        # toplamı onları sayar (yöneticinin aylık toplam tanımı).
        last = today if gran == "gun" else end - timedelta(days=1)
        deal_rows = conn.execute(
            f"""
            SELECT {_WON_DAY}, coalesce(sum(amount), 0)::float, count(*)
            FROM deal_packages
            WHERE org_id = %s AND stage = %s AND package IS DISTINCT FROM %s
              AND {_WON_DAY} BETWEEN %s AND %s
            GROUP BY 1
            """,
            (org_id, WON_STAGE, RENEWAL_PACKAGE, start, last),
        ).fetchall()
    leads, _ = _daily(lead_rows)
    ciro, sales = _daily(deal_rows)
    return {"available": True, **build(leads, ciro, sales, gran, today)}
