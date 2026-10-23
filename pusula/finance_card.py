"""Finans Yönetici Özeti kartı: tek, renkli görsel (HTML → PNG), satış kartıyla aynı düzen."""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from pusula.ceo_card import BAD, GOOD, INK, MUTED, WIDTH, render_png  # noqa: F401 — render_png dışarı açılır
from pusula.finance_summary import TITLE, period_label, usd

HEAD = "linear-gradient(120deg, #0d3b36 0%, #12806f 100%)"


def _signed(value: float | None) -> str:
    if value is None:
        return "—"
    color = GOOD if value > 0 else BAD if value < 0 else MUTED
    return f'<span style="color:{color}">{usd(value, sign=True)}</span>'


def _week(iso: str | None) -> str:
    if not iso:
        return "—"
    d = date.fromisoformat(iso)
    return f"{d.day}.{d.month:02d} haftası"


def render_html(s: dict[str, Any]) -> str:
    companies = "".join(
        f"""<tr><td>{escape(r["ad"])}{f'<div class="sub warn">veri {escape(r["eski_veri"])} tarihli</div>' if r.get("eski_veri") else ""}</td>
        <td class="num"><b>{usd(r["usd"])}</b>{f'<div class="sub">{r["yerel"]:,.0f} {escape(r["para"])}</div>'.replace(",", ".") if r.get("yerel") else ""}</td>
        <td class="num">{_signed(r["degisim"])}</td></tr>"""
        for r in s["sirketler"]
    )
    moves = "".join(
        f"""<tr><td>{escape(m["metin"] or "—")}<div class="sub">{escape(m["sirket"])} · {escape(m["kategori"])}</div></td>
        <td class="num">{_signed(m["usd"])}</td></tr>"""
        for m in s["hareketler"]
    )
    ay = s["ay"]
    p = s["projeksiyon"]
    u = s["uyarilar"]
    chips = []
    if p.get("acik_hafta"):
        chips.append(("warn", f"Nakit açığı: {_week(p['acik_hafta'])}"))
    if u["geciken"]:
        chips.append(("warn", f"{u['geciken']} gecikmiş planlı kalem · {usd(u['geciken_usd'])}"))
    for name in u["hata"]:
        chips.append(("warn", f"Senkron hatası: {name}"))
    if u["eski"]:
        chips.append(("warn", f"Eski veri: {', '.join(u['eski'])}"))
    if u["aksiyon"]:
        chips.append(("info", f"{u['aksiyon']} açık aksiyon kararı"))
    if s.get("kur_eksik"):
        chips.append(("warn", f"Kur eksik: {', '.join(s['kur_eksik'])}"))
    chip_html = "".join(f'<span class="chip {k}">{escape(t)}</span>' for k, t in chips) or '<span class="chip ok">Uyarı yok</span>'
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #eef1f6; font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; color: {INK}; }}
.card {{ width: {WIDTH}px; background: #fff; border-radius: 18px; overflow: hidden; }}
.top {{ background: {HEAD}; color: #fff; padding: 22px 28px 20px; }}
.top .t {{ font-size: 13px; letter-spacing: .08em; text-transform: uppercase; opacity: .8; }}
.top .d {{ font-size: 24px; font-weight: 700; margin-top: 2px; }}
.kpis {{ display: flex; gap: 14px; margin-top: 16px; }}
.kpi {{ flex: 1; background: rgba(255,255,255,.14); border-radius: 12px; padding: 12px 14px; }}
.kpi .v {{ font-size: 28px; font-weight: 800; line-height: 1.1; }}
.kpi .l {{ font-size: 12.5px; opacity: .85; margin-top: 2px; }}
.kpi .s {{ font-size: 13px; margin-top: 6px; font-weight: 600; }}
.section {{ padding: 18px 28px; border-top: 1px solid #e8ecf3; }}
.h {{ font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: {MUTED}; margin-bottom: 10px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
td {{ padding: 7px 0; border-bottom: 1px solid #f0f2f6; vertical-align: top; }}
tr:last-child td {{ border-bottom: 0; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; padding-left: 12px; }}
.sub {{ font-size: 12px; color: {MUTED}; margin-top: 2px; font-weight: 400; }}
.sub.warn {{ color: {BAD}; }}
.flows {{ display: flex; gap: 14px; }}
.flow {{ flex: 1; border-radius: 12px; padding: 12px 14px; background: #f5f7fb; }}
.flow .v {{ font-size: 20px; font-weight: 700; }}
.flow .l {{ font-size: 12px; color: {MUTED}; }}
.chip {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 13px; margin: 2px 6px 2px 0; }}
.chip.ok {{ background: #e5f5ea; color: {GOOD}; }}
.chip.warn {{ background: #fdeeed; color: {BAD}; }}
.chip.info {{ background: #e8f0fb; color: #2a5fa8; }}
.foot {{ padding: 10px 28px 14px; font-size: 11.5px; color: {MUTED}; background: #f8f9fc; }}
</style></head><body><div class="card" id="card">
  <div class="top">
    <div class="t">{TITLE}</div>
    <div class="d">{period_label(s)}</div>
    <div class="kpis">
      <div class="kpi"><div class="v">{usd(s["nakit"])}</div><div class="l">toplam nakit</div>
        <div class="s">{usd(s["nakit_degisim"], sign=True)} değişim</div></div>
      <div class="kpi"><div class="v">{usd(s["giris"])}</div><div class="l">giriş</div></div>
      <div class="kpi"><div class="v">{usd(s["cikis"])}</div><div class="l">çıkış</div></div>
    </div>
  </div>
  <div class="section">
    <div class="h">Şirketler · nakit ve değişim</div>
    <table>{companies}</table>
  </div>
  {f'<div class="section"><div class="h">En büyük hareketler</div><table>{moves}</table></div>' if moves else ""}
  <div class="section">
    <div class="h">{escape(ay["ad"])} · ay başından</div>
    <div class="flows">
      <div class="flow"><div class="v" style="color:{GOOD}">{usd(ay["giris"])}</div><div class="l">giriş</div></div>
      <div class="flow"><div class="v" style="color:{BAD}">{usd(ay["cikis"])}</div><div class="l">çıkış</div></div>
      <div class="flow"><div class="v">{_signed(ay["net"])}</div><div class="l">net</div></div>
      <div class="flow"><div class="v">{usd(p.get("en_dusuk"))}</div><div class="l">13 haftada en düşük kasa · {_week(p.get("en_dusuk_hafta"))}</div></div>
    </div>
  </div>
  <div class="section">
    <div class="h">Uyarılar</div>
    {chip_html}
  </div>
  <div class="foot">Tutarlar USD; hareketler işlem gününün kuruyla. Değişim kur ve şirket içi transferleri de içerir · Pusula</div>
</div></body></html>"""
