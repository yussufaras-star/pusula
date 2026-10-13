"""Re-Co (yeniden kontratlama) analizi: mevcut müşteriler satıyor mu, yeniledi mi.

Tanımlar (yönetici, 13 Ekim):
- Müşteri: en az bir "Kapandı Kazanıldı" deal'i olan contact.
- Satıyor: Zoho Contacts "Son 1 Yıllık Satış" > 0 (müşterinin kendi mağazası).
- Yenilemiş: contact'ın en az bir kazanılmış Re-Co-Premium deal'i var.
Gruplar: satıyor · yenilememiş (Re-Co fırsatı), satıyor · yenilemiş, satmıyor.
Kaynak: contact_profiles + deal_packages (saatlik).
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

from pusula.config import get_org_id
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

RECO_PACKAGE = "Re-Co-Premium"
SEGMENTS: dict[str, str] = {
    "satiyor_yenilememis": "Satıyor · yenilememiş",
    "satiyor_yenilemis": "Satıyor · yenilemiş",
    "satmiyor": "Satmıyor",
}
LIST_MAX = 300


def segment(son1: int | None, renewed: bool) -> str:
    if (son1 or 0) > 0:
        return "satiyor_yenilemis" if renewed else "satiyor_yenilememis"
    return "satmiyor"


def priority(abonelik_bit: date | None, today: date) -> tuple[int, int, int]:
    """İş listesi sırası: 30 gün önce bitmiş ile 60 gün sonra bitecek arası en
    üstte (en yakın tarih önce), sonra diğer tarihliler, tarihsiz en sonda."""
    if abonelik_bit is None:
        return (2, 0, 0)
    days = (abonelik_bit - today).days
    return (0 if -30 <= days <= 60 else 1, abs(days), days)


def summarize(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """Saf: müşteri satırları → grup özetleri ve iş listesi."""
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key in SEGMENTS}
    for row in rows:
        groups[segment(row["son1_satis"], (row["reco_n"] or 0) > 0)].append(row)
    soon = today + timedelta(days=30)
    later = today + timedelta(days=90)
    summary = []
    for key, items in groups.items():
        ends = [r["abonelik_bit"] for r in items if r["abonelik_bit"]]
        sales = [r["son1_satis"] for r in items if (r["son1_satis"] or 0) > 0]
        summary.append(
            {
                "key": key,
                "label": SEGMENTS[key],
                "musteri": len(items),
                "abonelik_bitmis": sum(1 for d in ends if d < today),
                "bitis_30": sum(1 for d in ends if today <= d <= soon),
                "bitis_90": sum(1 for d in ends if soon < d <= later),
                "abonelik_yok": len(items) - len(ends),
                "reco_satis": sum(r["reco_n"] or 0 for r in items),
                "reco_ciro": round(sum(r["reco_ciro"] or 0 for r in items), 2),
                "medyan_son1": median(sales) if sales else None,
            }
        )
    todo = sorted(groups["satiyor_yenilememis"], key=lambda r: priority(r["abonelik_bit"], today))
    return {
        "segments": summary,
        "toplam": len(rows),
        "liste": [
            {
                "contact_id": r["contact_id"],
                "name": r["full_name"],
                "owner": r["owner_name"],
                "paket": r["paket"],
                "abonelik_bit": r["abonelik_bit"],
                "gun": (r["abonelik_bit"] - today).days if r["abonelik_bit"] else None,
                "son1_satis": r["son1_satis"],
                "toplam_satis": r["toplam_satis"],
            }
            for r in todo[:LIST_MAX]
        ],
        "liste_toplam": len(todo),
    }


def load_reco(today: date, owner: str | None = None) -> dict[str, Any]:
    org_id = get_org_id()
    with connect() as conn:
        exists = conn.execute(
            "SELECT to_regclass('contact_profiles'), to_regclass('deal_packages')"
        ).fetchone()
        if not exists or not exists[0] or not exists[1]:
            return {"available": False}
        col = conn.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_name = 'deal_packages' AND column_name = 'contact_id'"""
        ).fetchone()
        if not col:
            return {"available": False}
        rows = conn.execute(
            """
            WITH won AS (
                SELECT contact_id,
                       count(*) FILTER (WHERE package = %s) AS reco_n,
                       sum(amount) FILTER (WHERE package = %s) AS reco_ciro
                FROM deal_packages
                WHERE org_id = %s AND stage = %s AND contact_id IS NOT NULL
                GROUP BY 1
            )
            SELECT c.contact_id, c.full_name, c.owner_name, c.paket, c.abonelik_bit,
                   c.son1_satis, c.toplam_satis, w.reco_n, w.reco_ciro
            FROM contact_profiles c
            JOIN won w ON w.contact_id = c.contact_id
            WHERE c.org_id = %s
            """,
            (RECO_PACKAGE, RECO_PACKAGE, org_id, WON_STAGE, org_id),
        ).fetchall()
        month_first = today.replace(day=1)
        month = conn.execute(
            """
            SELECT coalesce(r.full_name, '—'), count(*)::int, coalesce(sum(d.amount), 0)::float
            FROM deal_packages d
            LEFT JOIN reps r ON r.org_id = d.org_id AND r.rep_id = d.owner_rep_id
            WHERE d.org_id = %s AND d.stage = %s AND d.package = %s
              AND coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date) >= %s
            GROUP BY 1 ORDER BY 3 DESC
            """,
            (org_id, WON_STAGE, RECO_PACKAGE, month_first),
        ).fetchall()
    keys = ("contact_id", "full_name", "owner_name", "paket", "abonelik_bit",
            "son1_satis", "toplam_satis", "reco_n", "reco_ciro")
    all_rows = [dict(zip(keys, r)) for r in rows]
    for r in all_rows:
        r["reco_ciro"] = float(r["reco_ciro"] or 0)
    owners: dict[str, int] = {}
    for r in all_rows:
        name = r["owner_name"] or "—"
        owners[name] = owners.get(name, 0) + 1
    chosen = [r for r in all_rows if owner is None or (r["owner_name"] or "—") == owner]
    return {
        "available": True,
        "owner": owner,
        "owners": sorted(owners.items(), key=lambda kv: -kv[1]),
        "bu_ay": [{"owner": o, "satis": n, "ciro": c} for o, n, c in month],
        **summarize(chosen, today),
    }
