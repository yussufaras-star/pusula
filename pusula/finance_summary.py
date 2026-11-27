"""Finans Operasyonu Yönetici Özeti (günlük): konsolide ve şirket şirket nakit, son iş gününden
beri giriş-çıkış ve büyük hareketler, ay başından akış, 13 haftalık projeksiyonun en
düşük haftası, uyarılar (gecikmiş ödeme, eski/hatalı hesap verisi, açık aksiyon).

Tutarlar USD (hareketler işlem gününün, bakiyeler son kurla); şirket satırında yerel
para da yazılır. Sayıları kod hesaplar; #headquarters'a görsel kart gider.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pusula.ceo_summary import _DAYS, _MONTHS, report_day
from pusula.finance import analytics, store
from pusula.finance.fx import load_fx

TITLE = "Finans Operasyonu Yönetici Özeti"
TABLE = "fin_summaries"
BIG_MOVES = 3
ZERO = Decimal("0")


def _usd(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def cash_on(accounts: list[Any], txs: list[Any], fx: Any, day: date) -> dict[str, Decimal]:
    """Şirket → gün sonu nakit (USD, o günün kuru): güncel bakiyeden sonraki hareketler düşülür."""
    out: dict[str, Decimal] = {}
    for a in accounts:
        if not a.active:
            continue
        after = sum((t.amount for t in txs if t.account_id == a.id and t.booked_at > day), ZERO)
        out[a.company_id] = out.get(a.company_id, ZERO) + fx.to_usd(a.current_balance - after, a.currency, day)
    return out


def short_day(day: date | datetime) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]}"


def _stale_label(name: str, last: date | datetime | None) -> str:
    return f"{name} ({short_day(last) if last else 'hiç güncellenmedi'})"


def build_summary(conn: Any, today: date, now: datetime) -> dict[str, Any]:
    first = report_day(today)
    last = today - timedelta(days=1)
    month_first = last.replace(day=1)
    since = min(month_first, first) - timedelta(days=1)
    companies = sorted(store.load_companies(conn), key=lambda c: c.sort)
    accounts = [a for a in store.load_accounts(conn) if a.active]
    txs = store.load_transactions(conn, since, None)
    categories = store.load_categories(conn)
    planned = store.load_planned(conn)
    decisions = store.load_decisions(conn)
    feeds = analytics.stale_feeds(store.load_wallets(conn), store.load_receivables(conn), today)
    fx = load_fx(conn, since)
    kinds = analytics.category_kinds(categories)
    names = {c.id: c.name for c in categories}

    cash_now = analytics.cash_usd(accounts, fx)
    before = cash_on(accounts, txs, fx, first - timedelta(days=1))
    period = [t for t in txs if first <= t.booked_at <= last and not analytics.is_transfer(t, kinds)]
    inflow = sum((fx.to_usd(t.amount, t.currency, t.booked_at) for t in period if t.amount > 0), ZERO)
    outflow = sum((-fx.to_usd(t.amount, t.currency, t.booked_at) for t in period if t.amount < 0), ZERO)
    moves = sorted(period, key=lambda t: abs(fx.to_usd(t.amount, t.currency, t.booked_at)), reverse=True)[:BIG_MOVES]
    flows = analytics.monthly_flows([t for t in txs if month_first <= t.booked_at <= last], fx, kinds)
    month = flows[-1] if flows else None
    proj = analytics.projection(cash_now, planned, fx, today, weeks=13)
    low = analytics.lowest_week(proj)
    alerts = analytics.overview_alerts(accounts, planned, proj, fx, now)
    company_names = {c.id: c.name for c in companies}

    rows = []
    for c in companies:
        own = [a for a in accounts if a.company_id == c.id]
        if not own or all(analytics.last_update(a) is None for a in own):
            # Bağlı hesabı ya da hiç bakiyesi olmayan şirket de listede durur (ör. GmbH); tutarı boş.
            rows.append({"id": c.id, "ad": c.name, "para": c.base_currency, "usd": None, "yerel": None,
                         "degisim": None, "eski_veri": None, "eski_hesaplar": [], "hesap_yok": not own,
                         "veri_yok": bool(own)})
            continue
        usd = analytics.cash_usd(own, fx)
        stale = [(a.name, analytics.last_update(a)) for a in alerts.stale if a.company_id == c.id]
        dated = sorted((last.date(), name) for name, last in stale if last)
        rows.append({
            "id": c.id,
            "ad": c.name,
            "para": c.base_currency,
            "usd": _usd(usd),
            "yerel": _usd(usd / fx.to_usd(Decimal("1"), c.base_currency)) if c.base_currency != "USD" else None,
            "degisim": _usd(usd - before.get(c.id, ZERO)),
            "eski_veri": dated[0][0].isoformat() if dated else None,
            # Bakiyesi eski kalan her hesap tarihiyle; tarihsiz (hiç yüklenmemiş) olan tarihsiz.
            "eski_hesaplar": [{"ad": name, "tarih": last.date().isoformat() if last else None} for name, last in stale],
        })
    total_before = sum(before.values(), ZERO)
    return {
        "today": today,
        "first": first,
        "last": last,
        "nakit": _usd(cash_now),
        "nakit_degisim": _usd(cash_now - total_before),
        "giris": _usd(inflow),
        "cikis": _usd(outflow),
        "hareketler": [
            {
                "sirket": company_names.get(t.company_id, t.company_id),
                "metin": (t.counterparty or t.description or "")[:60],
                "kategori": names.get(t.category_id or "", "kategorisiz"),
                "usd": _usd(fx.to_usd(t.amount, t.currency, t.booked_at)),
            }
            for t in moves
        ],
        "ay": {
            "ad": _MONTHS[last.month - 1],
            "giris": _usd(month.inflow) if month else 0.0,
            "cikis": _usd(month.outflow) if month else 0.0,
            "net": _usd(month.net) if month else 0.0,
        },
        "sirketler": rows,
        "projeksiyon": {
            "en_dusuk_hafta": low.week.isoformat() if low else None,
            "en_dusuk": _usd(low.close) if low else None,
            "acik_hafta": alerts.cash_gap.week.isoformat() if alerts.cash_gap else None,
        },
        "uyarilar": {
            "geciken": len(alerts.overdue),
            "geciken_usd": _usd(alerts.overdue_usd),
            "hata": [a.name for a in alerts.sync_errors],
            "eski": [_stale_label(a.name, analytics.last_update(a)) for a in alerts.stale]
            + [_stale_label(f"{company_names.get(f.company_id, f.company_id)} {f.label}", f.as_of) for f in feeds],
            "aksiyon": sum(1 for d in decisions if d.status == "aksiyon"),
        },
        "kur_eksik": sorted(fx.missing),
    }


# --- metin -----------------------------------------------------------------------


def usd(value: float | None, sign: bool = False) -> str:
    if value is None:
        return "—"
    prefix = ("+" if value > 0 else "−" if value < 0 else "") if sign else ("−" if value < 0 else "")
    v = abs(value)
    # "B" İngilizce okuyana milyar gibi gelir; bin / Mn yazılır.
    if v >= 1_000_000:
        return f"{prefix}${v / 1_000_000:.2f}".replace(".", ",") + " Mn"
    if v >= 1_000:
        return f"{prefix}${v / 1_000:.1f}".replace(".", ",") + " bin"
    return f"{prefix}${v:.0f}"


def no_data(r: dict[str, Any]) -> str:
    return "veri yok (bağlı hesap yok)" if r.get("hesap_yok") else "veri yok (bakiye hiç yüklenmedi)"


def stale_note(r: dict[str, Any]) -> str:
    """Şirket satırında bakiyesi eski kalan hesaplar; eski kayıtlarda yalnız en eski tarih vardı."""
    items = r.get("eski_hesaplar")
    if items:
        parts = [f"{i['ad']} {short_day(date.fromisoformat(i['tarih'])) if i['tarih'] else 'hiç güncellenmedi'}" for i in items]
        return "eski bakiye: " + ", ".join(parts)
    return f"veri {r['eski_veri']} tarihli" if r.get("eski_veri") else ""


def period_label(s: dict[str, Any]) -> str:
    first = date.fromisoformat(str(s["first"])[:10])
    last = date.fromisoformat(str(s["last"])[:10])
    if first == last:
        return f"{first.day} {_MONTHS[first.month - 1]} {_DAYS[first.weekday()]}"
    return f"{first.day} {_MONTHS[first.month - 1]} – {last.day} {_MONTHS[last.month - 1]}"


def render(s: dict[str, Any]) -> str:
    """Düz metin (panel yedeği ve kanal hatasında). Cliq'te ~ üstü çizer; kullanılmaz."""
    lines = [f"*{TITLE} · {period_label(s)}*", ""]
    lines.append(f"*Toplam nakit:* {usd(s['nakit'])} ({usd(s['nakit_degisim'], sign=True)})")
    lines.append(f"*Giriş / çıkış:* {usd(s['giris'])} / {usd(s['cikis'])}")
    for r in s["sirketler"]:
        if r.get("hesap_yok") or r.get("veri_yok"):
            lines.append(f"• {r['ad']}: {no_data(r)}")
            continue
        note = stale_note(r)
        lines.append(f"• {r['ad']}: {usd(r['usd'])} ({usd(r['degisim'], sign=True)})" + (f" · {note}" if note else ""))
    ay = s["ay"]
    lines.append(f"*{ay['ad']}:* giriş {usd(ay['giris'])} · çıkış {usd(ay['cikis'])} · net {usd(ay['net'], sign=True)}")
    p = s["projeksiyon"]
    if p["en_dusuk"] is not None:
        lines.append(f"*13 hafta en düşük:* {usd(p['en_dusuk'])} ({p['en_dusuk_hafta']} haftası)")
    u = s["uyarilar"]
    notes = []
    if p["acik_hafta"]:
        notes.append(f"nakit açığı {p['acik_hafta']} haftası")
    if u["geciken"]:
        notes.append(f"{u['geciken']} gecikmiş planlı kalem ({usd(u['geciken_usd'])})")
    if u["eski"]:
        notes.append("eski veri: " + ", ".join(u["eski"]))
    if u["hata"]:
        notes.append("senkron hatası: " + ", ".join(u["hata"]))
    if u["aksiyon"]:
        notes.append(f"{u['aksiyon']} açık aksiyon kararı")
    lines.append("*Uyarılar:* " + ("; ".join(notes) if notes else "yok"))
    return "\n".join(lines)
