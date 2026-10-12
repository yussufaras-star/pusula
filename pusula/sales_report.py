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

from pusula.config import get_org_id
from pusula.metrics import Span, combine, finish, query_kpis, ratio
from pusula.occupancy import attach, occupancy_rows
from pusula.panel_ciro import ciro_by_rep, ciro_team_monthly, workdays_inclusive
from pusula.panel_data import DateWindow, connect, load_reps, sales_cycle

SUM_KEYS = ("atanan", "satis", "ciro")
REPORT_START = date(2026, 6, 1)


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
    ciro = {row["rep_id"]: row for row in ciro_by_rep("sales", start, min(end, today))}
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
        "ciro_monthly": ciro_team_monthly(),
    }
