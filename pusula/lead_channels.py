"""Lead akışı kanal kırılımı: pazarlama için kanal / kampanya / form türü.

Lead: Zoho'da oluşturulduğu gün (lead_attribution.zoho_created_at; kişiye
dönüştürülenler dahil, aynı kişinin tekrar başvurusu da sayılır — "gelen"
tekil sayımdan ~%1 fazladır). Toplu içe aktarım günleri (lead_ciro.
split_imports) sayılmaz.

Kırılımlar:
- kanal: UTM kaynağı gruplu (CHANNELS); UTM'siz lead'ler ayrı satır.
- kampanya: UTM kampanyası; ilk TOP_N dışındakiler "Diğer".
- form: Zoho Lead Source (Register, İletişim formu, WhatsApp...).

Haftalık seri ve % değişim lead_flow'la aynı (pazartesi başlar, süren hafta
işaretli). Kalite son QUALITY_WEEKS tamamlanmış haftanın lead'lerinden,
bugünkü statüye göre (lead_reach.QUALITY_STATUSES): ulaşıldı (hatalı ve
yabancı numara hariç), randevu/fırsat, nitelikli değil, hatalı/yabancı
numara; satış = lead'in kişi kaydına bağlı, lead'den sonra kapanan kazanılmış
deal. Yeni lead'lerin statüsü henüz oturmadığından son haftaların oranları
zamanla değişir.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pusula.ciro_trend import change, week_start
from pusula.config import get_org_id
from pusula.lead_ciro import split_imports
from pusula.lead_reach import DOKUNULMAMIS, ULASILDI, quality_bucket, reach_bucket
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

DIMS = ("kanal", "kampanya", "form")
TOP_N = 8
QUALITY_WEEKS = 4
# Seride bundan az lead'li satır "Diğer"e katılır; bundan az lead üzerinden % hesaplanmaz.
MIN_LEADS = 5
NO_UTM = "UTM yok"
NO_CAMPAIGN = "Kampanya yok"
NONE = (NO_UTM, NO_CAMPAIGN)
OTHER = "Diğer"

# UTM kaynağı (küçük harf, www./m./l. öneki atılmış) → kanal.
CHANNELS: dict[str, str] = {
    "meta": "Meta",
    "facebook": "Meta",
    "facebook.com": "Meta",
    "fb": "Meta",
    "instagram": "Instagram",
    "instagram.com": "Instagram",
    "ig": "Instagram",
    "google": "Google",
    "youtube": "YouTube",
    "youtube.com": "YouTube",
    "direct": "Direkt",
    "twitter": "X / Twitter",
    "x.com": "X / Twitter",
    "t.co": "X / Twitter",
    "linkedin.com": "LinkedIn",
    "linkedin": "LinkedIn",
    "chatgpt.com": "ChatGPT",
}

FORMS: dict[str, str] = {
    "Register": "Kayıt (Register)",
    "Contact Form": "İletişim formu",
    "Direkt Tel. Arama": "Telefon",
}


@dataclass(frozen=True)
class Lead:
    day: date
    utm_source: str | None
    utm_campaign: str | None
    source: str | None
    status: str | None
    sold: bool


def channel(utm_source: str | None) -> str:
    raw = (utm_source or "").strip().lower()
    if not raw:
        return NO_UTM
    for prefix in ("www.", "m.", "l."):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return CHANNELS.get(raw, OTHER)


def form(source: str | None) -> str:
    raw = (source or "").strip()
    if not raw:
        return "Belirtilmemiş"
    if raw.startswith("WhatsApp"):
        return "WhatsApp"
    return FORMS.get(raw, raw)


def label(lead: Lead, dim: str) -> str:
    if dim == "kanal":
        return channel(lead.utm_source)
    if dim == "kampanya":
        return (lead.utm_campaign or "").strip() or NO_CAMPAIGN
    return form(lead.source)


def quality(leads: list[Lead]) -> dict[str, Any]:
    """Saf: kalite oranları (lead'e göre %)."""
    n = len(leads)
    q = Counter(quality_bucket(x.status) for x in leads)
    worked = sum(1 for x in leads if reach_bucket(x.status) not in (None, DOKUNULMAMIS))
    reached = sum(1 for x in leads if reach_bucket(x.status) == ULASILDI) - q["hatali"] - q["yabanci"]
    sold = sum(1 for x in leads if x.sold)

    def rate(k: int, d: int) -> float | None:
        return round(100.0 * k / d, 1) if d else None

    return {
        "lead": n,
        "ulasildi": rate(reached, worked),
        "randevu": rate(q["randevu"], n),
        "nitelikli_degil": rate(q["nitelikli_degil"], n),
        "hatali": rate(q["hatali"] + q["yabanci"], n),
        "satis": sold,
        "satis_orani": rate(sold, n),
    }


def build(leads: list[Lead], dim: str, weeks: int, today: date) -> dict[str, Any]:
    """Saf: kırılım satırları (en çok lead gelenden), haftalık seri, değişim, pay, kalite."""
    current = week_start(today)
    first = current - timedelta(weeks=weeks)
    bases = [first + timedelta(weeks=i) for i in range(weeks + 1)]
    done = bases[:-1]
    q_from = current - timedelta(weeks=QUALITY_WEEKS)
    base4 = current - timedelta(weeks=2 * QUALITY_WEEKS)

    shown = [x for x in leads if first <= x.day <= today]
    totals = Counter(label(x, dim) for x in shown)
    keep = {k for k, v in totals.items() if v >= MIN_LEADS or k in NONE}
    if dim == "kampanya":
        keep = set(sorted(keep - {NO_CAMPAIGN}, key=lambda k: -totals[k])[:TOP_N]) | {NO_CAMPAIGN}

    def name(x: Lead) -> str:
        k = label(x, dim)
        return k if k in keep else OTHER

    weekly: dict[str, Counter[date]] = defaultdict(Counter)
    four: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # son 4, önceki 4 tamamlanmış hafta
    q_leads: dict[str, list[Lead]] = defaultdict(list)
    for x in leads:
        k = name(x)
        wk = week_start(x.day)
        if first <= x.day <= today:
            weekly[k][wk] += 1
        if q_from <= x.day < current:
            four[k][0] += 1
            q_leads[k].append(x)
        elif base4 <= x.day < q_from:
            four[k][1] += 1

    def row(k: str, series: Counter[date], last4: int, prev4: int, ql: list[Lead]) -> dict[str, Any]:
        seri = [series.get(b, 0) for b in bases]
        son = seri[-2] if len(seri) > 1 else 0
        onceki = seri[-3] if len(seri) > 2 else 0
        return {
            "ad": k,
            "seri": seri,
            "son": son,
            "onceki": onceki,
            "degisim": change(son, onceki) if onceki >= MIN_LEADS else None,
            "ort4": round(last4 / QUALITY_WEEKS, 1),
            "onceki_ort4": round(prev4 / QUALITY_WEEKS, 1),
            "degisim_ort4": change(last4, prev4) if prev4 >= MIN_LEADS else None,
            "toplam": sum(seri),
            "kalite": quality(ql),
        }

    names = set(weekly) | {k for k in four if any(four[k])}
    rows = [row(k, weekly.get(k, Counter()), *four[k], q_leads.get(k, [])) for k in names]
    total_week: Counter[date] = Counter()
    for c in weekly.values():
        total_week.update(c)
    total = row(
        "Toplam", total_week, sum(v[0] for v in four.values()), sum(v[1] for v in four.values()),
        [x for v in q_leads.values() for x in v],
    )
    for r in rows:
        r["pay"] = round(100.0 * r["son"] / total["son"], 1) if total["son"] else None
    # "Diğer" ve "UTM yok" / "Kampanya yok" sona; gerisi adet sırası.
    tail = {OTHER: 2, NO_UTM: 1, NO_CAMPAIGN: 1}
    rows.sort(key=lambda r: (tail.get(r["ad"], 0), -r["son"], -r["toplam"], r["ad"]))
    total["pay"] = 100.0 if total["son"] else None
    return {
        "available": True,
        "dim": dim,
        "haftalar": [{"bas": b, "bit": b + timedelta(days=6), "suruyor": b == current} for b in bases],
        "son_hafta": done[-1] if done else None,
        "kalite_bas": q_from,
        "kalite_bit": current - timedelta(days=1),
        "satirlar": rows,
        "toplam": total,
        # Kanal: UTM kaynağı dolu lead payı; kampanya: kampanyası dolu lead payı.
        "utm_payi": round(100.0 * (1 - sum(totals.get(k, 0) for k in NONE) / sum(totals.values())), 1)
        if totals and dim != "form" else None,
    }


def load_lead_channels(dim: str, weeks: int, today: date) -> dict[str, Any]:
    org_id = get_org_id()
    first = week_start(today) - timedelta(weeks=max(weeks, 2 * QUALITY_WEEKS))
    with connect() as conn:
        has = conn.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'lead_attribution' AND column_name = 'zoho_created_at'
            """
        ).fetchone()
        if not has:
            return {"available": False, "dim": dim}
        rows = conn.execute(
            """
            WITH base AS (
                SELECT a.lead_id, (a.zoho_created_at AT TIME ZONE 'Europe/Istanbul')::date AS gun,
                       a.utm_source, a.utm_campaign, l.source, l.status, l.thread_id
                FROM lead_attribution a
                LEFT JOIN leads l ON l.org_id = a.org_id AND l.lead_id = a.lead_id
                WHERE a.org_id = %s AND a.zoho_created_at >= %s
            )
            SELECT b.gun, b.utm_source, b.utm_campaign, b.source, b.status,
                   EXISTS (
                       SELECT 1 FROM contacts ct
                       JOIN deal_packages d ON d.org_id = ct.org_id AND d.contact_id = ct.contact_id
                       WHERE ct.org_id = %s AND (ct.lead_id = b.lead_id OR ct.thread_id = b.thread_id)
                         AND d.stage = %s
                         AND coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date) >= b.gun
                   )
            FROM base b
            """,
            (org_id, first, org_id, WON_STAGE),
        ).fetchall()
    leads = [Lead(*r) for r in rows if r[0] is not None]
    # Toplu içe aktarım günleri akış değildir.
    _, imports = split_imports(dict(Counter(x.day for x in leads)))
    skip = {x["gun"] for x in imports}
    leads = [x for x in leads if x.day not in skip]
    return build(leads, dim, weeks, today)
