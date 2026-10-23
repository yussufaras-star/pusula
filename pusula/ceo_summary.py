"""Günlük CEO özeti (satış): son iş gününün sonucu, ay hedefi ve tempo, huni,
bugünün toplantıları, ekipte yalnız istisnalar; pazartesi görüşme kalitesi.

Sayıları kod hesaplar. Ciro ve yeni müşteri OKR'daki gibi tüm şirket; huni ve
ekip satış temsilcileri. Her sabah CEO'ya Cliq'ten kişisel mesaj, panelde aynısı.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id

from pusula.ingest.lead_inflow import load_inflow
from pusula.leaves import load_leaves
from pusula.meetings import meetings_payload
from pusula.metrics import Span
from pusula.okr_template import PLAN_2026
from pusula.panel_ciro import is_workday, workdays_inclusive
from pusula.panel_data import connect
from pusula.sales_report import build_sales_report, new_customers_by_owner, won_by_rep

RECIPIENT = "mehmet.tek@rexven.com"
# Doluluğu bunun altında kalan temsilci istisna olarak yazılır.
LOW_DOLULUK = 60.0
# Huni kıyası: son 4 haftanın aynı günü.
COMPARE_WEEKS = 4
FUNNEL_KEYS = ("gelen_lead", "arama", "ulasilan", "randevu", "katildi", "satis")
FUNNEL_LABEL = {
    "gelen_lead": "gelen lead",
    "arama": "arama",
    "ulasilan": "ulaşılan",
    "randevu": "toplantı planlanan",
    "katildi": "toplantıya gelen",
    "satis": "yeni müşteri",
}
PANEL_URL = "https://rexven-pusula.netlify.app/#/ceo"

_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
           "Kasım", "Aralık")
_DAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def report_day(today: date) -> date:
    """Raporlanan gün: bugünden önceki son iş günü (pazartesi → cumartesi)."""
    day = today - timedelta(days=1)
    while not is_workday(day):
        day -= timedelta(days=1)
    return day


def month_plan(day: date) -> dict[str, float] | None:
    """Ayın yeni müşteri ve ciro hedefi (yıl başı planı; 2026 dışında yok)."""
    if day.year != 2026:
        return None
    return {"satis": PLAN_2026["satis"][day.month - 1], "ciro": PLAN_2026["ciro"][day.month - 1]}


def pace(actual: float, done_days: int, total_days: int) -> float | None:
    """Bu tempoyla ay sonu: iş günü başına ortalama × ayın iş günü."""
    if done_days <= 0:
        return None
    return actual / done_days * total_days


def _sales(first: date, last: date) -> dict[str, float]:
    won = won_by_rep(first, last)
    return {
        "satis": float(sum(new_customers_by_owner(first, last).values())),
        "ciro": float(sum(v["ciro"] for v in won.values())),
    }


def _funnel(day: date, now: datetime) -> tuple[dict[str, float | None], list[dict[str, Any]]]:
    report = build_sales_report(day, day, now)
    team = report["team"]
    inflow = load_inflow(day, day)
    row = {
        "gelen_lead": float(inflow) if inflow is not None else None,
        "arama": float(team.get("arama") or 0),
        "ulasilan": float(team.get("ulasilan") or 0),
        "randevu": float(team.get("randevu") or 0),
        "katildi": float(team.get("katildi") or 0),
        "satis": float(sum(new_customers_by_owner(day, day).values())),
    }
    return row, report["reps"]


def exceptions(reps: list[dict[str, Any]], on_leave: set[str]) -> list[str]:
    """Yalnız sapan temsilciler: izinli, hiç arama yok, doluluk düşük."""
    out: list[str] = []
    for r in sorted(reps, key=lambda x: x.get("name") or ""):
        name = r.get("name") or r.get("rep_id")
        if r.get("rep_id") in on_leave:
            out.append(f"{name} izinli")
        elif not r.get("arama"):
            out.append(f"{name} hiç arama yapmadı")
        elif r.get("doluluk") is not None and r["doluluk"] < LOW_DOLULUK:
            out.append(f"{name} doluluk %{r['doluluk']:.0f}")
    return out


def build_summary(today: date, now: datetime) -> dict[str, Any]:
    day = report_day(today)
    yesterday = today - timedelta(days=1)
    # Sonuç: raporlanan günden dün dahil (pazartesi cumartesi + pazar satışları).
    last = _sales(day, yesterday)
    month_first = day.replace(day=1)
    next_month = (month_first + timedelta(days=32)).replace(day=1)
    mtd = _sales(month_first, yesterday)
    plan = month_plan(day)
    done_days = workdays_inclusive(month_first, day)
    total_days = workdays_inclusive(month_first, next_month - timedelta(days=1))

    funnel, reps = _funnel(day, now)
    past = [_funnel(day - timedelta(weeks=w), now)[0] for w in range(1, COMPARE_WEEKS + 1)]
    compare = {}
    for key in FUNNEL_KEYS:
        values = [p[key] for p in past if p.get(key) is not None]
        compare[key] = round(sum(values) / len(values), 1) if values else None
    leave = {rep for rep, d in load_leaves(Span.days(day, day)) if d == day}

    meetings = meetings_payload(today, now)["by_rep"]
    out: dict[str, Any] = {
        "today": today,
        "day": day,
        "son": last,
        "ay": {
            "ad": _MONTHS[day.month - 1],
            **mtd,
            "hedef": plan,
            "tempo_satis": pace(mtd["satis"], done_days, total_days),
            "tempo_ciro": pace(mtd["ciro"], done_days, total_days),
            "is_gunu": done_days,
            "toplam_is_gunu": total_days,
        },
        "huni": funnel,
        "huni_kiyas": compare,
        "bugun_toplanti": sum(int(s.get("planlanan") or 0) for s in meetings.values()),
        "istisnalar": exceptions(reps, leave),
        "kalite": None,
    }
    if today.weekday() == 0:
        out["kalite"] = weekly_quality(today - timedelta(days=7))
    return out


def weekly_quality(week_start: date) -> dict[str, Any] | None:
    """Geçen haftanın görüşme kalitesi: ortalama, en sık bug, en iyi örnek."""
    from pusula import conversations, sonar41

    week = conversations.load_week(week_start, None)
    stats = (week.get("team") or {}).get("stats") or {}
    if not stats.get("analiz"):
        return None
    bugs: dict[str, int] = {}
    for b in week.get("bugs") or []:
        bugs[b["kod"]] = bugs.get(b["kod"], 0) + 1
    top = max(bugs.items(), key=lambda kv: kv[1]) if bugs else None
    best = (week.get("ornekler") or [None])[0]
    return {
        "analiz": stats["analiz"],
        "puan": stats.get("puan_ort"),
        "bug": {"kod": top[0], "ad": sonar41.BEHAVIORS[top[0]]["name"], "adet": top[1]} if top else None,
        "ornek": {"temsilci": best["rep_name"], "puan": best["puan"]} if best else None,
    }


# --- metin ----------------------------------------------------------------------


def tl(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}".replace(".", ",") + " M TL"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.0f} B TL"
    return f"{value:.0f} TL"


def _n(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", ".")


def _vs(value: float | None, ref: float | None) -> str:
    if value is None or not ref:
        return ""
    change = (value - ref) / ref * 100
    arrow = "↑" if change >= 5 else "↓" if change <= -5 else "→"
    return f" ({arrow} ort. {_n(ref)})"


def render(s: dict[str, Any]) -> str:
    day: date = s["day"]
    ay = s["ay"]
    hedef = ay["hedef"]
    since = "" if s["today"] - day == timedelta(days=1) else " (pazar dahil)"
    lines = [f"*Satış özeti · {day.day} {_MONTHS[day.month - 1]} {_DAYS[day.weekday()]}*", ""]
    lines.append(f"*Sonuç{since}:* {_n(s['son']['satis'])} yeni müşteri · {tl(s['son']['ciro'])} ciro")
    if hedef:
        lines.append(
            f"*{ay['ad']}:* {_n(ay['satis'])} / {_n(hedef['satis'])} yeni müşteri "
            f"(%{ay['satis'] / hedef['satis'] * 100:.0f}) · {tl(ay['ciro'])} / {tl(hedef['ciro'])} ciro "
            f"(%{ay['ciro'] / hedef['ciro'] * 100:.0f})"
        )
    else:
        lines.append(f"*{ay['ad']}:* {_n(ay['satis'])} yeni müşteri · {tl(ay['ciro'])} ciro")
    if ay["tempo_satis"] is not None:
        lines.append(
            f"Bu tempoyla ay sonu: ~{_n(ay['tempo_satis'])} yeni müşteri · ~{tl(ay['tempo_ciro'])} "
            f"({ay['is_gunu']}/{ay['toplam_is_gunu']} iş günü)"
        )
    lines += ["", f"*Huni ({_DAYS[day.weekday()].lower()}, son {COMPARE_WEEKS} haftanın aynı günüyle):*"]
    for key in FUNNEL_KEYS:
        value = s["huni"].get(key)
        if value is None:
            continue
        lines.append(f"• {FUNNEL_LABEL[key]}: {_n(value)}{_vs(value, s['huni_kiyas'].get(key))}")
    lines += ["", f"*Bugün:* {_n(s['bugun_toplanti'])} toplantı planlı"]
    lines.append("*Ekip:* " + ("; ".join(s["istisnalar"]) if s["istisnalar"] else "normal, sapan temsilci yok"))
    k = s.get("kalite")
    if k:
        parts = [f"{_n(k['analiz'])} görüşme"]
        if k.get("puan") is not None:
            parts.append(f"ortalama {k['puan']:.1f}".replace(".", ",") + "/5")
        if k.get("bug"):
            parts.append(f"en sık hata {k['bug']['kod']} {k['bug']['ad']} ({k['bug']['adet']})")
        if k.get("ornek"):
            parts.append(f"en iyi görüşme {k['ornek']['temsilci']}")
        lines += ["", "*Geçen hafta görüşme kalitesi:* " + " · ".join(parts)]
    lines += ["", f"Ayrıntı: {PANEL_URL}"]
    return "\n".join(lines)


def cliq_card(s: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Cliq kartı: (bildirim metni, kart başlığı, bölümler). Telefonda tek bakışta okunur."""
    day: date = s["day"]
    ay = s["ay"]
    hedef = ay["hedef"]
    title = f"Satış özeti · {day.day} {_MONTHS[day.month - 1]} {_DAYS[day.weekday()]}"
    since = "" if s["today"] - day == timedelta(days=1) else " (pazar dahil)"
    result = [
        {"Yeni müşteri": _n(s["son"]["satis"])},
        {"Ciro": tl(s["son"]["ciro"])},
    ]
    month = [{"Yeni müşteri": f"{_n(ay['satis'])}" + (f" / {_n(hedef['satis'])} (%{ay['satis'] / hedef['satis'] * 100:.0f})" if hedef else "")},
             {"Ciro": f"{tl(ay['ciro'])}" + (f" / {tl(hedef['ciro'])} (%{ay['ciro'] / hedef['ciro'] * 100:.0f})" if hedef else "")}]
    if ay["tempo_satis"] is not None:
        month.append({"Tempoyla ay sonu": f"~{_n(ay['tempo_satis'])} müşteri · ~{tl(ay['tempo_ciro'])}"})
    rows = []
    for key in FUNNEL_KEYS:
        value = s["huni"].get(key)
        if value is None:
            continue
        ref = s["huni_kiyas"].get(key)
        rows.append({"Adım": FUNNEL_LABEL[key].capitalize(), "Gün": _n(value),
                     "4 hafta ort.": _n(ref) + (_arrow(value, ref) if ref else "")})
    slides: list[dict[str, Any]] = [
        {"type": "label", "title": f"Sonuç{since}", "data": result},
        {"type": "label", "title": f"{ay['ad']} ({ay['is_gunu']}/{ay['toplam_is_gunu']} iş günü)", "data": month},
        {"type": "table", "title": "Huni", "data": {"headers": ["Adım", "Gün", "4 hafta ort."], "rows": rows}},
        {"type": "label", "title": "Bugün ve ekip", "data": [
            {"Planlı toplantı": _n(s["bugun_toplanti"])},
            {"Ekip": "; ".join(s["istisnalar"]) if s["istisnalar"] else "normal"},
        ]},
    ]
    k = s.get("kalite")
    if k:
        data = [{"Analiz edilen": _n(k["analiz"])}]
        if k.get("puan") is not None:
            data.append({"Ortalama": f"{k['puan']:.1f}".replace(".", ",") + " / 5"})
        if k.get("bug"):
            data.append({"En sık hata": f"{k['bug']['kod']} {k['bug']['ad']} ({k['bug']['adet']})"})
        if k.get("ornek"):
            data.append({"En iyi görüşme": k["ornek"]["temsilci"]})
        slides.append({"type": "label", "title": "Geçen hafta görüşme kalitesi", "data": data})
    slides.append({"type": "text", "data": f"[Ayrıntı panelde]({PANEL_URL})"})
    note = f"{_n(s['son']['satis'])} yeni müşteri · {tl(s['son']['ciro'])}"
    return note, {"title": title, "theme": "modern-inline"}, slides


def _arrow(value: float, ref: float) -> str:
    change = (value - ref) / ref * 100
    return " ↑" if change >= 5 else " ↓" if change <= -5 else ""


# --- kayıt ve gönderim ------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ceo_summaries (
    org_id      text NOT NULL DEFAULT 'rexven',
    day         date NOT NULL,
    payload     jsonb NOT NULL,
    body        text NOT NULL,
    sent_to     text,
    sent_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, day)
);
ALTER TABLE ceo_summaries ENABLE ROW LEVEL SECURITY;
"""


def save(conn: Any, summary: dict[str, Any], body: str) -> None:
    conn.execute(SCHEMA_SQL)
    conn.execute(
        """
        INSERT INTO ceo_summaries (org_id, day, payload, body) VALUES (%s, %s, %s::jsonb, %s)
        ON CONFLICT (org_id, day) DO UPDATE SET payload = EXCLUDED.payload, body = EXCLUDED.body, created_at = now()
        """,
        (get_org_id(), summary["today"], json.dumps(summary, ensure_ascii=False, default=str), body),
    )
    conn.commit()


def sent_today(conn: Any, today: date) -> bool:
    conn.execute(SCHEMA_SQL)
    row = conn.execute(
        "SELECT sent_at FROM ceo_summaries WHERE org_id = %s AND day = %s", (get_org_id(), today)
    ).fetchone()
    return bool(row and row[0])


def mark_sent(conn: Any, today: date, to: str) -> None:
    conn.execute(
        "UPDATE ceo_summaries SET sent_to = %s, sent_at = now() WHERE org_id = %s AND day = %s",
        (to, get_org_id(), today),
    )
    conn.commit()


def load_recent(limit: int = 14) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        conn.commit()
        rows = conn.execute(
            """
            SELECT day, payload, body, sent_to, sent_at, created_at FROM ceo_summaries
            WHERE org_id = %s ORDER BY day DESC LIMIT %s
            """,
            (get_org_id(), limit),
        ).fetchall()
    return [
        {"day": d, "payload": p, "body": b, "sent_to": t, "sent_at": s, "created_at": c}
        for d, p, b, t, s, c in rows
    ]
