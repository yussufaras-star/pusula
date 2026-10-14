"""Aylık prim hedefi: Premium/Plus satış adedine göre kademe, ciro üzerinden prim.

Kural (yönetici, 13 Ekim):
- Sayılan satış: ay içinde kazanılan (Kapandı Kazanıldı) Rexven-Premium,
  Rexven-Plus, Rexven-Premium-Kamp paketleri; lead'den veya contact'tan
  açılmış tüm deal'ler.
- Oran: 25 satışın altı %2,5 · 25-29 %3 · 30 ve üzeri %5 (yönetici, 14 Ekim).
- Prim = oran × o ay kazanılan TÜM satışların cirosu (Kamp dahil).
- Sonraki adım her zaman en üst kademe (%5, 30 satış): kalan satış, günlük
  gereken ve prim farkı ona göre (yönetici, 13 Ekim).
- Ay sonu tahmini: iş günü başına hız × ayın iş günü (pazar hariç); bugün
  mesainin geçen kısmı kadar sayılır.
Kaynak tablo pusula.ingest.deal_packages (saatlik).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.metrics import TZ, shift_bounds
from pusula.panel_ciro import is_workday, month_first, month_last, workdays_inclusive
from pusula.panel_data import connect, load_reps
from pusula.sifir_satis import WON_STAGE

COUNTED_PACKAGES = ("Rexven-Premium", "Rexven-Plus", "Rexven-Premium-Kamp")
# (en az adet, oran %) büyükten küçüğe.
TIERS: tuple[tuple[int, float], ...] = ((30, 5.0), (25, 3.0), (0, 2.5))


def rate_for(count: float) -> float:
    n = int(count)
    return next(rate for floor, rate in TIERS if n >= floor)


def next_tier(count: int) -> tuple[int, float] | None:
    """Hedef kademe: en üst kademe (eşik, oran); ona ulaşıldıysa None."""
    top = TIERS[0]
    return top if count < top[0] else None


def elapsed_workdays(month: date, now: datetime) -> tuple[float, int]:
    """(geçen iş günü, ayın iş günü). Bugün mesainin geçen oranı kadar."""
    first, last = month_first(month), month_last(month)
    total = workdays_inclusive(first, last)
    today = now.astimezone(TZ).date()
    if today > last:
        return float(total), total
    if today < first:
        return 0.0, total
    done = float(workdays_inclusive(first, today - timedelta(days=1)))
    if is_workday(today):
        bounds = shift_bounds(today)
        if bounds:
            start, end = bounds
            local = now.astimezone(TZ)
            span = (end - start).total_seconds()
            done += min(max((local - start).total_seconds() / span, 0.0), 1.0)
    return done, total


def rep_progress(
    *, counted: int, all_sales: int, ciro: float, elapsed: float, month_wd: int
) -> dict[str, Any]:
    """Saf: tek temsilcinin ilerlemesi, tahmini ve sonraki kademe."""
    rate = rate_for(counted)
    remaining_wd = max(month_wd - elapsed, 0.0)
    if elapsed > 0 and remaining_wd > 0:
        f_counted = counted / elapsed * month_wd
        f_ciro = ciro / elapsed * month_wd
    else:
        f_counted, f_ciro = float(counted), ciro
    f_rate = rate_for(f_counted)
    nxt = next_tier(counted)
    step = None
    if nxt:
        floor, nrate = nxt
        missing = floor - counted
        step = {
            "esik": floor,
            "oran": nrate,
            "kalan": missing,
            "gunluk_gereken": round(missing / remaining_wd, 2) if remaining_wd > 0 else None,
            # Bugünkü ciroyla kademe atlanırsa prim farkı (alt sınır).
            "ek_prim": round((nrate - rate) / 100 * ciro, 2),
        }
    return {
        "sayilan": counted,
        "toplam_satis": all_sales,
        "ciro": round(ciro, 2),
        "oran": rate,
        "prim": round(rate / 100 * ciro, 2),
        "tahmin_sayilan": round(f_counted, 1),
        "tahmin_ciro": round(f_ciro, 2),
        "tahmin_oran": f_rate,
        "tahmin_prim": round(f_rate / 100 * f_ciro, 2),
        "sonraki": step,
    }


def as_of_moment(day: date) -> datetime:
    """Geçmiş gün görünümü: o günün sonu (tahmin o güne kadarki hızla)."""
    return datetime(day.year, day.month, day.day, 23, 59, tzinfo=TZ)


def load_commission(month: date, now: datetime, asof: date | None = None) -> dict[str, Any]:
    """asof: paneldeki geçmiş gün; ay başından o güne kadarki satışlar, tahmin o güne göre."""
    first, last = month_first(month), month_last(month)
    upper = min(asof, last) if asof else last
    if asof:
        now = as_of_moment(asof)
    reps = load_reps()
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('deal_packages')").fetchone()
        rows = (
            conn.execute(
                """
                SELECT owner_rep_id,
                       count(*) FILTER (WHERE package = ANY(%s))::int,
                       count(*)::int,
                       coalesce(sum(amount), 0)::float,
                       coalesce(sum(amount) FILTER (WHERE package = ANY(%s)), 0)::float
                FROM deal_packages
                WHERE org_id = %s AND stage = %s
                  AND coalesce(closing_date, (created_at AT TIME ZONE 'Europe/Istanbul')::date)
                      BETWEEN %s AND %s
                GROUP BY 1
                """,
                (list(COUNTED_PACKAGES), list(COUNTED_PACKAGES), get_org_id(), WON_STAGE, first, upper),
            ).fetchall()
            if exists and exists[0]
            else []
        )
    by_rep = {str(r[0]): r for r in rows}
    elapsed, month_wd = elapsed_workdays(month, now)
    out = []
    for rep in reps:
        _, counted, all_sales, ciro, counted_ciro = by_rep.get(rep.rep_id, (rep.rep_id, 0, 0, 0.0, 0.0))
        item = rep_progress(
            counted=int(counted), all_sales=int(all_sales), ciro=float(ciro),
            elapsed=elapsed, month_wd=month_wd,
        )
        out.append(
            {
                "rep_id": rep.rep_id,
                "name": rep.full_name,
                **item,
                # Prim tabanı tüm ciro; kırılım yalnız görünürlük için.
                "ciro_sayilan": round(float(counted_ciro), 2),
                "ciro_diger": round(float(ciro) - float(counted_ciro), 2),
                "diger_satis": int(all_sales) - int(counted),
            }
        )
    return {
        "month": first,
        "asof": asof,
        "available": bool(exists and exists[0]),
        "elapsed_workdays": round(elapsed, 2),
        "month_workdays": month_wd,
        "packages": COUNTED_PACKAGES,
        "tiers": [{"esik": floor, "oran": rate} for floor, rate in sorted(TIERS)],
        "reps": out,
    }


# --- teşhis: "satış yansımıyor" sorusuna ekrandan cevap ---------------------------


def classify_deal(stage: str | None, package: str | None, closing: date | None, month: date) -> dict[str, Any]:
    """Saf: bir deal bu ayın prim kartına nasıl girer. ciro: ciroya girer mi;
    sayilan: Premium/Plus adedine girer mi; neden: girmiyorsa sebebi."""
    first, last = month_first(month), month_last(month)
    if stage != WON_STAGE:
        return {"ciro": False, "sayilan": False, "neden": f"Aşama: {stage or 'boş'} (kazanılmadı)"}
    if closing is not None and not first <= closing <= last:
        return {"ciro": False, "sayilan": False, "neden": f"Kapanış tarihi {closing:%d.%m.%Y} (bu ay değil)"}
    if package not in COUNTED_PACKAGES:
        why = "Paket alanı boş" if not package else f"{package} adede girmez"
        return {"ciro": True, "sayilan": False, "neden": f"{why}; ciroya girer"}
    return {"ciro": True, "sayilan": True, "neden": None}


def _deal_rows(conn: Any, where: str, params: list[Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT d.deal_id, d.package, d.stage, d.amount::float, d.closing_date,
               (d.created_at AT TIME ZONE 'Europe/Istanbul')::date, d.owner_rep_id,
               coalesce(cp.full_name, ct.full_name), d.updated_at
        FROM deal_packages d
        LEFT JOIN contact_profiles cp ON cp.org_id = d.org_id AND cp.contact_id = d.contact_id
        LEFT JOIN contacts ct ON ct.org_id = d.org_id AND ct.contact_id = d.contact_id
        WHERE d.org_id = %s {where}
        ORDER BY coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date) DESC
        LIMIT 200
        """,
        [get_org_id(), *params],
    ).fetchall()
    keys = ("deal_id", "paket", "asama", "tutar", "kapanis", "olusturma", "owner_rep_id", "musteri", "guncellendi")
    return [dict(zip(keys, r)) for r in rows]


def load_rep_deals(month: date, rep_id: str | None, asof: date | None = None) -> dict[str, Any]:
    """Temsilcinin bu ay kapanan veya bu ay açılan deal'leri, sayılma durumuyla.
    rep_id None: bu ay kazanılan ama sahibi satış ekibinde olmayan deal'ler.
    asof: geçmiş gün görünümü; o günden sonra kapanan/açılanlar listelenmez."""
    first, last = month_first(month), month_last(month)
    upper = min(asof, last) if asof else last
    # Bu ay (seçilen güne kadar) kapanan; ya da bu ay açılıp kapanışı başka ayda/boş
    # olan (neden "bu ay değil" diye görünsün). Seçilen günden sonra kapananlar yok.
    period = (
        " AND (d.closing_date BETWEEN %s AND %s"
        " OR ((d.created_at AT TIME ZONE 'Europe/Istanbul')::date BETWEEN %s AND %s"
        "     AND (d.closing_date IS NULL OR d.closing_date NOT BETWEEN %s AND %s)))"
    )
    with connect() as conn:
        if rep_id:
            items = _deal_rows(
                conn,
                " AND d.owner_rep_id = %s" + period,
                [rep_id, first, upper, first, upper, first, last],
            )
        else:
            sales = [r.rep_id for r in load_reps()]
            items = _deal_rows(
                conn,
                " AND d.stage = %s AND d.closing_date BETWEEN %s AND %s"
                " AND (d.owner_rep_id IS NULL OR NOT d.owner_rep_id = ANY(%s))",
                [WON_STAGE, first, upper, sales],
            )
    for item in items:
        item.update(classify_deal(item["asama"], item["paket"], item["kapanis"], month))
    return {"month": first, "rep_id": rep_id, "asof": asof, "deals": items}


def deals_sync_state() -> tuple[datetime | None, datetime | None]:
    """(son değişen deal'in zamanı, son kontrol). Önbellek anahtarı ve kart notu."""
    from pusula.db import client
    from pusula.ingest.deal_packages import SOURCE

    state = client.get_sync_state(SOURCE)
    if state is None:
        return None, None
    return state.last_synced_at, state.updated_at
