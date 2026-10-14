"""Raporlar: satış hunisi, temsilci metrik tablosu (sıralama) ve ciro.

Huni dönem içindeki faaliyetten kurulur (kohort değil): aranan lead →
ulaşılan lead → planlanan toplantı → katılan toplantı → satış (kazanılan
deal) → ciro. Adım oranları: ulaşma = ulaşılan/aranan, toplantıya dönüş =
toplantı/ulaşılan, katılım = katılan/(katılan+katılmayan). Satışların çoğu
toplantısız (telefonda) kapandığından satış katılan toplantıya değil
ulaşılan lead'e oranlanır (satış/katılan %100'ü aşıyordu). Ekip ortalaması
dönemde faaliyeti olan temsilcilerin ortalamasıdır.

Veri kapsamı: aramalar Mayıs, toplantılar Haziran 2026'dan beri tam; dönem
REPORT_START'tan önce başlayamaz. WhatsApp Temmuz-Eylül 2026'da eksik. Hesaplar pusula.metrics, occupancy, panel_ciro ve
panel_data'dadır; burası birleştirir.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pusula.ciro_trend import load_trend
from pusula.config import get_org_id
from pusula.lead_reach import reach_bucket_sql
from pusula.metrics import Span, combine, finish, query_kpis, ratio
from pusula.occupancy import attach, occupancy_rows
from pusula.panel_ciro import REAL_SALES_START, add_months, month_first, month_label, workdays_inclusive
from pusula.sifir_satis import WON_STAGE
from pusula.panel_data import DateWindow, connect, load_reps, sales_cycle

SUM_KEYS = ("atanan", "satis", "ciro")
REPORT_START = date(2026, 6, 1)


# Ciro ve satış prim kartıyla aynı kaynaktan (deal_packages): TL'ye çevrilmiş
# tutar, kapanış tarihi dönemde olan tüm "Kapandı Kazanıldı" deal'ler (ileri
# tarihli kapanış dahil; yöneticinin aylık toplam tanımı).
_WON_DAY = "coalesce(closing_date, (created_at AT TIME ZONE 'Europe/Istanbul')::date)"
_WON_DAY_D = "coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date)"


def won_by_rep(start: date, end: date) -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT owner_rep_id, count(*)::int, coalesce(sum(amount), 0)::float
            FROM deal_packages
            WHERE org_id = %s AND stage = %s AND {_WON_DAY} BETWEEN %s AND %s
            GROUP BY 1
            """,
            (get_org_id(), WON_STAGE, start, end),
        ).fetchall()
    return {str(r): {"satis": n, "ciro": c} for r, n, c in rows}


def won_team_monthly(rep_ids: list[str], today: date, months: int = 12) -> list[dict[str, Any]]:
    # Nisan 2026 öncesi CRM taşıma dönemi; satış verisi güvenilir değil (panel_ciro ile aynı).
    first = max(add_months(month_first(today), -(months - 1)), REAL_SALES_START)
    months = (today.year - first.year) * 12 + today.month - first.month + 1
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT date_trunc('month', {_WON_DAY})::date, count(*)::int, coalesce(sum(amount), 0)::float
            FROM deal_packages
            WHERE org_id = %s AND stage = %s AND owner_rep_id = ANY(%s) AND {_WON_DAY} >= %s
            GROUP BY 1
            """,
            (get_org_id(), WON_STAGE, rep_ids, first),
        ).fetchall()
    by = {m: (n, c) for m, n, c in rows}
    out = []
    for i in range(months):
        m = add_months(first, i)
        n, c = by.get(m, (0, 0.0))
        out.append(
            {
                "ay": m,
                "ay_etiket": month_label(m),
                "satis": n,
                "ciro": round(c, 2),
                "ortalama": round(c / n, 2) if n else None,
                "kisi_basi": round(c / max(len(rep_ids), 1), 2),
            }
        )
    return out


def _assigned(span: Span) -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT owner_rep_id, count(*) FROM leads
            WHERE org_id = %s AND assigned_at >= %s AND assigned_at < %s
            GROUP BY 1
            """,
            (get_org_id(), span.start, span.end),
        ).fetchall()
    return {str(rep_id): int(n) for rep_id, n in rows}


def derive(row: dict[str, Any], work_days: int) -> dict[str, Any]:
    """Huni oranları ve günlük ortalamalar. Saf; ham sayımlar finish() çıktısı."""
    out = dict(row)
    out["toplanti_donus"] = ratio(out.get("randevu"), out.get("lead_pay"))
    out["satis_orani"] = ratio(out.get("satis"), out.get("lead_pay"))
    out["ort_satis"] = round(out["ciro"] / out["satis"], 2) if out.get("satis") else None
    days = max(work_days, 1)
    out["gunluk_arama"] = round(float(out.get("arama") or 0) / days, 1)
    out["gunluk_ciro"] = round(float(out.get("ciro") or 0) / days, 2)
    out["work_days"] = work_days
    return out


def team_rows(reps: list[dict[str, Any]], work_days: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """(ekip toplamı, temsilci ortalaması). Ortalama faaliyeti olanlar üzerinden."""
    active = [r for r in reps if (r.get("arama") or 0) > 0 or (r.get("satis") or 0) > 0]
    total = finish(combine(active), n_reps=len(active))
    for key in SUM_KEYS:
        total[key] = sum(r.get(key) or 0 for r in active)
    total = derive(total, work_days)
    n = max(len(active), 1)
    avg = dict(total)
    for key, value in total.items():
        if key in ("n_reps", "work_days") or not isinstance(value, (int, float)):
            continue
        if key in COUNT_LIKE:
            avg[key] = round(value / n, 2)
    avg = derive(finish(avg, n_reps=1), work_days)
    # Oranlar kişi başına bölünmez; finish() küçük sayımları tam sayıya
    # yuvarladığından ekip toplamının oranı aynen alınır.
    for key in RATE_KEYS:
        avg[key] = total.get(key)
    return total, avg


RATE_KEYS = (
    "ulasma_orani", "katilim_orani", "doluluk", "sure_ort", "toplanti_donus",
    "satis_orani", "ort_satis",
)

# Temsilci başına bölünen sayımlar; oranlar bölünmez, yeniden hesaplanır.
COUNT_LIKE = {
    "arama", "giden_temas", "donus", "gelen", "ulasilan", "lead_payda", "lead_pay",
    "randevu", "katildi", "katilmadi", "sonuc_girilmedi", "whatsapp", "sure_toplam",
    "call_sec", "meet_dk", "dolu_dk", "mola_dk", "kayip_dk", "girilmemis_dk",
    "toplanti_dk", *SUM_KEYS,
}


def build_sales_report(start: date, end: date, now: datetime) -> dict[str, Any]:
    start = max(start, REPORT_START)
    span = Span.days(start, end)
    today = now.date()
    reps = load_reps()
    rep_ids = [rep.rep_id for rep in reps]
    kpis = {
        str(row["rep_id"]): row
        for row in attach(
            query_kpis(span, group="rep", now=now),
            occupancy_rows(span, group="rep", rep_ids=rep_ids, now=now),
        )
    }
    ciro = won_by_rep(start, end)
    assigned = _assigned(span)
    cycle_rows, cycle_team = sales_cycle(DateWindow(start=start, end=end))
    cycle = {row["temsilci"]: row for row in cycle_rows}
    work_days = workdays_inclusive(start, min(end, today))

    rows = []
    for rep in reps:
        row = finish(kpis.get(rep.rep_id) or combine([]))
        sale = ciro.get(rep.rep_id) or {}
        row.update(
            {
                "rep_id": rep.rep_id,
                "name": rep.full_name,
                "atanan": assigned.get(rep.rep_id, 0),
                "satis": int(sale.get("satis") or 0),
                "ciro": float(sale.get("ciro") or 0),
                "dongu_gun": (cycle.get(rep.full_name) or {}).get("medyan_gun"),
            }
        )
        rows.append(derive(row, work_days))
    total, avg = team_rows(rows, work_days)
    total["dongu_gun"] = avg["dongu_gun"] = cycle_team.get("medyan_gun")
    return {
        "start": start,
        "end": end,
        "data_start": REPORT_START,
        "work_days": work_days,
        "reps": rows,
        "team": total,
        "avg": avg,
        "ciro_monthly": won_team_monthly(rep_ids, today),
        # Dönem seçiminden bağımsız: haftalık/aylık seri ve aynı güne kadar kıyas.
        "ciro_trend": load_trend(rep_ids, today),
    }


# --- kaynak verimi -----------------------------------------------------------

SOURCE_DIMS: dict[str, tuple[str, str]] = {
    "lead_source": ("Lead kaynağı", "nullif(btrim(l.source), '')"),
    "utm_source": ("Reklam kaynağı (UTM)", "a.utm_source"),
    "utm_campaign": ("Kampanya", "a.utm_campaign"),
    "utm_content": ("Reklam içeriği", "a.utm_content"),
}


def yield_row(src: str | None, counts: dict[str, Any]) -> dict[str, Any]:
    """Saf: bir kaynağın sayımları → oranlar. Payda dönemde atanan lead."""
    lead = int(counts.get("lead") or 0)
    worked = lead - int(counts.get("dokunulmamis") or 0)
    ciro = float(counts.get("ciro") or 0)
    satan = int(counts.get("satan_lead") or 0)
    return {
        "source": src,
        "lead": lead,
        "dokunulmamis": int(counts.get("dokunulmamis") or 0),
        "ulasilamadi": int(counts.get("denendi") or 0),
        "ulasildi": int(counts.get("ulasildi") or 0),
        "musteri": int(counts.get("musteri") or 0),
        "satan_lead": satan,
        "satis": int(counts.get("satis") or 0),
        "ciro": round(ciro, 2),
        "islenme": ratio(worked, lead),
        "ulasma": ratio(counts.get("ulasildi"), worked),
        "satis_orani": ratio(satan, lead),
        "lead_basi_ciro": round(ciro / lead, 2) if lead else None,
    }


def source_yield(dim: str, *, rep_id: str | None, span: Span) -> dict[str, Any]:
    """Dönemde atanan lead'ler kaynak bazında: işlenme, ulaşma, satışa dönüş,
    lead başı ciro. Satış: lead'in müşteri kaydına (contacts) bağlı, atamadan
    sonra kapanan kazanılmış deal'ler (bugüne kadar). Statü bugünkü statü."""
    label, expr = SOURCE_DIMS[dim]
    org_id = get_org_id()
    rep_sql = " AND l.owner_rep_id = %s" if rep_id else ""
    with connect() as conn:
        exists = conn.execute(
            "SELECT to_regclass('lead_attribution') IS NOT NULL, to_regclass('deal_packages') IS NOT NULL"
        ).fetchone()
        has_attr, has_deals = bool(exists and exists[0]), bool(exists and exists[1])
        if (dim != "lead_source" and not has_attr) or not has_deals:
            return {"dim": dim, "label": label, "available": False, "rows": []}
        join = (
            "LEFT JOIN lead_attribution a ON a.org_id = l.org_id AND a.lead_id = l.lead_id"
            if has_attr
            else ""
        )
        params: list[Any] = [org_id, span.start, span.end, *([rep_id] if rep_id else []), org_id, org_id, WON_STAGE]
        rows = conn.execute(
            f"""
            WITH cohort AS (
                SELECT l.lead_id, l.thread_id, l.org_id,
                       (l.assigned_at AT TIME ZONE 'Europe/Istanbul')::date AS atandi,
                       {expr} AS src, {reach_bucket_sql('l.status')} AS kova
                FROM leads l
                JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
                {join}
                WHERE l.org_id = %s AND r.category = 'sales'
                  AND l.assigned_at >= %s AND l.assigned_at < %s {rep_sql}
            ),
            conv AS (
                SELECT c.lead_id, c.atandi, ct.contact_id
                FROM cohort c
                JOIN LATERAL (
                    SELECT contact_id FROM contacts ct
                    WHERE ct.org_id = %s AND (ct.lead_id = c.lead_id OR ct.thread_id = c.thread_id)
                    ORDER BY ct.created_at NULLS LAST
                    LIMIT 1
                ) ct ON true
            ),
            won AS (
                SELECT cv.lead_id, count(*)::int AS n, coalesce(sum(d.amount), 0)::float AS ciro
                FROM conv cv
                JOIN deal_packages d ON d.org_id = %s AND d.contact_id = cv.contact_id
                 AND d.stage = %s AND {_WON_DAY_D} >= cv.atandi
                GROUP BY 1
            )
            SELECT c.src, count(*)::int,
                   count(*) FILTER (WHERE c.kova = 'dokunulmamis')::int,
                   count(*) FILTER (WHERE c.kova = 'denendi')::int,
                   count(*) FILTER (WHERE c.kova = 'ulasildi')::int,
                   count(cv.lead_id)::int, count(w.lead_id)::int,
                   coalesce(sum(w.n), 0)::int, coalesce(sum(w.ciro), 0)::float
            FROM cohort c
            LEFT JOIN conv cv ON cv.lead_id = c.lead_id
            LEFT JOIN won w ON w.lead_id = c.lead_id
            GROUP BY 1
            """,
            params,
        ).fetchall()
    keys = ("lead", "dokunulmamis", "denendi", "ulasildi", "musteri", "satan_lead", "satis", "ciro")
    out = [yield_row(r[0], dict(zip(keys, r[1:]))) for r in rows]
    total = yield_row(None, {k: sum(float(r[i + 1] or 0) for r in rows) for i, k in enumerate(keys)})
    out.sort(key=lambda r: -r["lead"])
    return {"dim": dim, "label": label, "available": True, "rows": out, "total": total}
