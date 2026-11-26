"""Kamp → Premium dönüşümü: lead'den kişiye Kamp ile dönüp sonra Premium alanlar.

Kişi: son MONTHS ayda oluşturulmuş contact; ilk kazanılmış deal'i (oluşturulma
sırasıyla) Kamp. Premium: ondan sonra oluşturulan, adında "Premium" geçen
kazanılmış deal (Rexven-Premium, Re-Co-Premium, Plus'tan geçiş...). Premium ile
gelip Kamp'ı sonradan alanlar ve Premium-Kamp paketi bu sayıya girmez.

Kamp satışından WAIT_DAYS gün geçmemişse kamp henüz yapılmamış olabilir:
"süreçte" sayılır, orana girmez. Almayanın nedeni kamptan sonraki görüşmelerden:
analiz edilmiş görüşmede itiraz varsa o; yoksa kamptan sonra kaç kez arandığı;
yanında CRM lead statüsü.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.metrics import TZ
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

MONTHS = 5
WAIT_DAYS = 30
UNKNOWN = "Diğer CRM kullanıcısı"

DURUM = ("aldi", "almadi", "surecte")


def reason(row: dict[str, Any]) -> str:
    """Almayan / süreçteki kişi için okunur neden. Saf."""
    parts: list[str] = []
    objections = row.get("itirazlar") or []
    if objections:
        o = objections[0]
        text = f"İtiraz: {o.get('tip') or 'belirsiz'}"
        if o.get("alinti"):
            text += f" — “{o['alinti']}”"
        parts.append(text)
    calls = int(row.get("arama_sonra") or 0)
    if not calls:
        parts.append("Kamptan sonra Premium için aranmadı")
    elif not objections:
        last = row.get("son_arama")
        parts.append(f"Kamptan sonra {calls} kez arandı" + (f" (son {last:%d.%m})" if last else ""))
    if row.get("statu"):
        parts.append(f"CRM: {row['statu']}")
    return " · ".join(parts)


def build(rows: Sequence[dict[str, Any]], names: dict[str, str], today: date) -> dict[str, Any]:
    """Saf: temsilci satırları (en çok kampla dönen önce) ve kişi listeleri."""
    reps: dict[str, dict[str, Any]] = {}
    for r in rows:
        kamp: date = r["kamp_gun"]
        prem: date | None = r.get("premium_gun")
        if prem:
            durum = "aldi"
        elif (today - kamp).days < WAIT_DAYS:
            durum = "surecte"
        else:
            durum = "almadi"
        owner = str(r.get("owner") or "")
        rep = reps.setdefault(owner, {"rep_id": owner, "name": names.get(owner, UNKNOWN),
                                      "giden": 0, "aldi": 0, "almadi": 0, "surecte": 0, "kisiler": []})
        rep["giden"] += 1
        rep[durum] += 1
        rep["kisiler"].append({
            "ad": r.get("ad") or "İsimsiz kişi",
            "kamp_gun": kamp,
            "premium_gun": prem,
            "gun": (prem - kamp).days if prem else None,
            "durum": durum,
            "neden": "" if durum == "aldi" else reason(r),
        })
    order = {"aldi": 0, "almadi": 1, "surecte": 2}
    for rep in reps.values():
        rep["kisiler"].sort(key=lambda k: (order[k["durum"]], k["kamp_gun"]))
    out = sorted(reps.values(), key=lambda r: (r["name"] == UNKNOWN, -r["giden"], r["name"]))
    total = {k: sum(r[k] for r in out) for k in ("giden", "aldi", "almadi", "surecte")}
    decided = total["aldi"] + total["almadi"]
    days = [k["gun"] for r in out for k in r["kisiler"] if k["gun"] is not None]
    return {
        "bas": today - timedelta(days=round(MONTHS * 30.4)),
        "bekleme_gun": WAIT_DAYS,
        "temsilciler": out,
        "toplam": total,
        "oran": total["aldi"] / decided if decided else None,
        "ort_gun": round(sum(days) / len(days)) if days else None,
    }


def load_kamp_premium(today: date, names: dict[str, str]) -> dict[str, Any]:
    start = today - timedelta(days=round(MONTHS * 30.4))
    with connect() as conn:
        rows = conn.execute(
            """
            WITH won AS (
                SELECT d.*, row_number() OVER (PARTITION BY d.contact_id ORDER BY d.created_at) AS rn
                FROM deal_packages d
                WHERE d.org_id = %s AND d.stage = %s AND d.contact_id IS NOT NULL
            )
            SELECT f.owner_rep_id, coalesce(nullif(btrim(ct.full_name), ''), l.full_name), f.created_at,
                   (SELECT min(p.created_at) FROM won p
                    WHERE p.contact_id = f.contact_id AND p.package ILIKE '%%premium%%'
                      AND p.created_at > f.created_at),
                   l.status,
                   (SELECT count(*) FROM events e
                    WHERE e.org_id = ct.org_id AND e.thread_id = ct.thread_id AND e.channel = 'call'
                      AND e.occurred_at > f.created_at),
                   (SELECT max(e.occurred_at) FROM events e
                    WHERE e.org_id = ct.org_id AND e.thread_id = ct.thread_id AND e.channel = 'call'
                      AND e.occurred_at > f.created_at),
                   (SELECT c.analysis->'itirazlar' FROM conv_items c
                    WHERE c.org_id = ct.org_id AND c.thread_id = ct.thread_id AND c.status = 'analiz_edildi'
                      AND c.occurred_at > f.created_at
                      AND jsonb_array_length(coalesce(c.analysis->'itirazlar', '[]'::jsonb)) > 0
                    ORDER BY c.occurred_at DESC LIMIT 1)
            FROM won f
            JOIN contacts ct ON ct.org_id = f.org_id AND ct.contact_id = f.contact_id
            LEFT JOIN leads l ON l.org_id = ct.org_id AND l.lead_id = ct.lead_id
            WHERE f.rn = 1 AND f.package = 'Kamp' AND ct.created_at >= %s
            """,
            (get_org_id(), WON_STAGE, start),
        ).fetchall()

    def day(v: datetime | None) -> date | None:
        return v.astimezone(TZ).date() if v else None

    return build(
        [
            {"owner": o, "ad": n, "kamp_gun": day(k), "premium_gun": day(p), "statu": s,
             "arama_sonra": c, "son_arama": day(lc), "itirazlar": it}
            for o, n, k, p, s, c, lc, it in rows
        ],
        names,
        today,
    )
