"""CEO özeti kartı: tek, renkli, telefonda okunur görsel (HTML → PNG).

Panel aynı HTML'i gösterir; sabah işi Playwright ile PNG'ye çevirip Cliq'e gönderir.
"""

from __future__ import annotations

from datetime import date, timedelta
from html import escape
from typing import Any

from pusula.ceo_summary import _DAYS, _MONTHS, COMPARE_WEEKS, FUNNEL_KEYS, FUNNEL_LABEL, _n, tl

WIDTH = 720
GOOD = "#0f8a3c"
BAD = "#c8372d"
WARN = "#c77c02"
INK = "#1d2433"
MUTED = "#6b7385"
ACCENT = "#2a78d6"


def _pct(a: float, b: float | None) -> float | None:
    return a / b * 100 if b else None


def _bar(label: str, actual: float, target: float | None, tempo: float | None, fmt: Any) -> str:
    """Hedef çubuğu: dolu kısım gerçekleşen, çizgi bu tempoyla ay sonu."""
    if not target:
        return f'<div class="bar-row"><div class="bar-label">{label}</div><div class="bar-val">{fmt(actual)}</div></div>'
    share = min(actual / target, 1.0) * 100
    ahead = tempo is not None and tempo >= target
    color = GOOD if ahead else WARN
    mark = "" if tempo is None else f'<span class="mark" style="left:{min(tempo / target, 1.3) / 1.3 * 100:.1f}%"></span>'
    note = "" if tempo is None else (
        f'<span style="color:{color}">bu tempoyla ay sonu yaklaşık {fmt(tempo)}'
        f'({"hedefin üstünde" if ahead else "hedefin altında"})</span>'
    )
    return f"""
    <div class="bar-row">
      <div class="bar-label">{label}</div>
      <div class="bar-val"><b>{fmt(actual)}</b> / {fmt(target)} <span class="pct" style="color:{color}">%{_pct(actual, target):.0f}</span></div>
    </div>
    <div class="track"><span class="fill" style="width:{share / 1.3:.1f}%;background:{color}"></span>{mark}
      <span class="goal" style="left:{100 / 1.3:.1f}%"></span></div>
    <div class="bar-note">{note}</div>"""


def _arrow(value: float, ref: float | None) -> str:
    if not ref:
        return ""
    change = (value - ref) / ref * 100
    if change >= 5:
        return f'<span style="color:{GOOD}">▲ %{change:.0f}</span>'
    if change <= -5:
        return f'<span style="color:{BAD}">▼ %{-change:.0f}</span>'
    return f'<span style="color:{MUTED}">≈</span>'


def render_html(s: dict[str, Any]) -> str:
    day = s["day"] if isinstance(s["day"], date) else date.fromisoformat(str(s["day"])[:10])
    today = s["today"] if isinstance(s["today"], date) else date.fromisoformat(str(s["today"])[:10])
    ay = s["ay"]
    hedef = ay.get("hedef")
    since = "" if today - day == timedelta(days=1) else " · pazar dahil"
    funnel_rows = "".join(
        f'<tr><td>{FUNNEL_LABEL[k].capitalize()}</td><td class="num"><b>{_n(s["huni"][k])}</b></td>'
        f'<td class="num muted">{_n(s["huni_kiyas"].get(k))}</td><td class="num">{_arrow(s["huni"][k], s["huni_kiyas"].get(k))}</td></tr>'
        for k in FUNNEL_KEYS
        if s["huni"].get(k) is not None
    )
    team = s.get("istisnalar") or []
    chips = (
        "".join(f'<span class="chip warn">{escape(t)}</span>' for t in team)
        if team
        else '<span class="chip ok">Ekip normal, sapan temsilci yok</span>'
    )
    son = s["son"]
    reco_line = (
        f'<div class="sub">+ {_n(son.get("reco"))} Re-Co · {tl(son.get("reco_ciro"))}</div>'
        if son.get("reco") else '<div class="sub">Re-Co yok</div>'
    )
    month_reco = (
        f'<div class="reco">Re-Co (yenileme): <b>{_n(ay.get("reco"))}</b> satış · {tl(ay.get("reco_ciro"))}</div>'
        if ay.get("reco") is not None else ""
    )
    k = s.get("kalite")
    quality = ""
    if k:
        parts = [f"{_n(k['analiz'])} görüşme"]
        if k.get("puan") is not None:
            parts.append(f"ortalama <b>{k['puan']:.1f}".replace(".", ",") + "</b> / 5")
        if k.get("bug"):
            parts.append(f"en sık hata <b>{escape(k['bug']['kod'])}</b> {escape(k['bug']['ad'])} ({k['bug']['adet']})")
        if k.get("ornek"):
            parts.append(f"en iyi görüşme <b>{escape(k['ornek']['temsilci'])}</b>")
        quality = f'<div class="section"><div class="h">Geçen hafta görüşme kalitesi</div><div class="q">{" · ".join(parts)}</div></div>'
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #eef1f6; font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; color: {INK}; }}
.card {{ width: {WIDTH}px; background: #fff; border-radius: 18px; overflow: hidden; box-shadow: 0 2px 10px rgba(20,30,60,.08); }}
.top {{ background: linear-gradient(120deg, #17305e 0%, {ACCENT} 100%); color: #fff; padding: 22px 28px 20px; }}
.top .t {{ font-size: 13px; letter-spacing: .08em; text-transform: uppercase; opacity: .8; }}
.top .d {{ font-size: 24px; font-weight: 700; margin-top: 2px; }}
.kpis {{ display: flex; gap: 14px; margin-top: 16px; }}
.kpi {{ flex: 1; background: rgba(255,255,255,.14); border-radius: 12px; padding: 12px 14px; }}
.kpi .v {{ font-size: 30px; font-weight: 800; line-height: 1.1; }}
.kpi .l {{ font-size: 12.5px; opacity: .85; margin-top: 2px; }}
.kpi .sub {{ font-size: 12.5px; margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(255,255,255,.25); }}
.reco {{ font-size: 13.5px; color: {MUTED}; margin: -4px 0 14px; }}
.reco b {{ color: {INK}; }}
.section {{ padding: 18px 28px; border-top: 1px solid #e8ecf3; }}
.h {{ font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: {MUTED}; margin-bottom: 10px; }}
.bar-row {{ display: flex; justify-content: space-between; align-items: baseline; font-size: 15px; }}
.bar-val b {{ font-size: 17px; }}
.pct {{ font-weight: 700; margin-left: 6px; }}
.track {{ position: relative; height: 10px; background: #edf0f5; border-radius: 6px; margin: 6px 0 4px; }}
.fill {{ position: absolute; left: 0; top: 0; bottom: 0; border-radius: 6px; }}
.goal {{ position: absolute; top: -3px; bottom: -3px; width: 2px; background: {INK}; opacity: .55; }}
.mark {{ position: absolute; top: -4px; width: 10px; height: 18px; margin-left: -5px; border-radius: 3px; border: 2px solid {INK}; background: #fff; }}
.bar-note {{ font-size: 12.5px; margin-bottom: 12px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
td {{ padding: 6px 0; border-bottom: 1px solid #f0f2f6; }}
tr:last-child td {{ border-bottom: 0; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; width: 90px; }}
.muted {{ color: {MUTED}; }}
th {{ font-size: 11.5px; color: {MUTED}; font-weight: 600; text-align: right; padding-bottom: 4px; }}
th:first-child {{ text-align: left; }}
.row2 {{ display: flex; gap: 20px; align-items: center; }}
.big {{ font-size: 28px; font-weight: 800; color: {ACCENT}; }}
.chip {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 13px; margin: 2px 6px 2px 0; }}
.chip.ok {{ background: #e5f5ea; color: {GOOD}; }}
.chip.warn {{ background: #fdeeed; color: {BAD}; }}
.q {{ font-size: 14px; line-height: 1.5; }}
.foot {{ padding: 10px 28px 14px; font-size: 11.5px; color: {MUTED}; background: #f8f9fc; }}
</style></head><body><div class="card" id="card">
  <div class="top">
    <div class="t">Satış Operasyonu Yönetici Özeti</div>
    <div class="d">{day.day} {_MONTHS[day.month - 1]} {_DAYS[day.weekday()]}{since}</div>
    <div class="kpis">
      <div class="kpi"><div class="v">{_n(s["son"]["satis"])}</div><div class="l">yeni müşteri</div>{reco_line}</div>
      <div class="kpi"><div class="v">{tl(s["son"]["ciro"])}</div><div class="l">ciro</div></div>
      <div class="kpi"><div class="v">{_n(s["bugun_toplanti"])}</div><div class="l">bugün planlı toplantı</div></div>
    </div>
  </div>
  <div class="section">
    <div class="h">{escape(ay["ad"])} · {ay["is_gunu"]}/{ay["toplam_is_gunu"]} iş günü</div>
    {_bar("Yeni müşteri", ay["satis"], hedef["satis"] if hedef else None, ay.get("tempo_satis"), _n)}
    {month_reco}
    {_bar("Ciro", ay["ciro"], hedef["ciro"] if hedef else None, ay.get("tempo_ciro"), tl)}
  </div>
  <div class="section">
    <div class="h">Huni · son {COMPARE_WEEKS} haftanın aynı günüyle</div>
    <table><tr><th></th><th>Gün</th><th>Ort.</th><th>Fark</th></tr>{funnel_rows}</table>
  </div>
  <div class="section">
    <div class="h">Ekip</div>
    {chips}
  </div>
  {quality}
  <div class="foot">Ciro ve yeni müşteri tüm şirket; huni satış ekibi · Pusula</div>
</div></body></html>"""


def render_png(html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH + 40, "height": 800}, device_scale_factor=2)
        page.set_content(html, wait_until="load")
        data = page.locator("#card").screenshot()
        browser.close()
    return data
