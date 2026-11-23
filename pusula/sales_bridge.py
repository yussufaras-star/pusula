"""Ciro farkının kaynağı: iki dönemin cirosu huni adımlarına ayrıştırılır.

Ciro = iş günü × günlük aranan lead × ulaşma × satışa dönüş × ortalama satış.
Her çarpan Raporlar'daki huniyle aynı tanımdır (pusula.sales_report): aranan
lead benzersiz lead (lead_payda), ulaşma lead_pay/lead_payda, satışa dönüş
satış/ulaşılan lead. Kimlik tam olduğundan ayrıştırma logaritmik ortalama
(LMDI) ile yapılır: her çarpanın TL etkisi toplanınca farkın kendisini verir,
sıra seçimine bağlı değildir.

Süren dönem bugün hariç (tamamlanmış günler) ölçülür; hizalamada geçmiş
dönem aynı gün sayısına kesilir (1-22 Kasım ↔ 1-22 Ekim).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.panel_ciro import fmt_tl
from pusula.panel_data import connect
from pusula.sales_report import _WON_DAY
from pusula.sifir_satis import WON_STAGE

Range = tuple[date, date]

FACTORS: tuple[tuple[str, str, str], ...] = (
    ("is_gunu", "İş günü", "gün"),
    ("gunluk_lead", "Günlük aranan lead", "lead/gün"),
    ("ulasma", "Ulaşma oranı", "oran"),
    ("donusum", "Satışa dönüş", "oran"),
    ("ort_satis", "Ortalama satış", "tl"),
)

# Görüşme kalitesi: ciro kimliğinde yok, yanında okunur. Doluluk bilerek yok
# (Kasım'da tanım/veri kayması şüphesi).
QUALITY: tuple[tuple[str, str, str], ...] = (
    ("sure_ort", "Ort. görüşme süresi", "sure"),
    ("call_sec", "Toplam konuşma", "sure_toplam"),
    ("giden_temas", "Ulaşılan görüşme", "adet"),
    ("randevu", "Toplantı", "adet"),
    ("katilim_orani", "Toplantıya katılım", "yuzde"),
)


def _completed(first: date, last: date, today: date) -> Range:
    """Bugüne uzanan dönem dünde biter; yalnız bugünse bugün kalır."""
    if last >= today and today > first:
        return first, today - timedelta(days=1)
    return first, min(last, today)


def aligned_ranges(a: Range, b: Range, today: date, *, align: bool = True) -> tuple[Range, Range]:
    """Ölçülecek kapalı aralıklar. Süren taraf varsa öteki aynı gün sayısına kesilir."""
    a_run, b_run = a[1] >= today, b[1] >= today
    a_eff = _completed(*a, today)
    b_eff = _completed(*b, today)
    if align and a_run and not b_run:
        b_eff = (b_eff[0], min(b_eff[1], b_eff[0] + (a_eff[1] - a_eff[0])))
    elif align and b_run and not a_run:
        a_eff = (a_eff[0], min(a_eff[1], a_eff[0] + (b_eff[1] - b_eff[0])))
    return a_eff, b_eff


def _div(x: Any, y: Any) -> float | None:
    x, y = float(x or 0), float(y or 0)
    return x / y if y else None


def factors(row: dict[str, Any]) -> dict[str, float | None]:
    """Raporlar satırından (derive çıktısı) çarpanlar. Çarpımları ciroya eşittir."""
    days = float(row.get("work_days") or 0)
    return {
        "is_gunu": days or None,
        "gunluk_lead": _div(row.get("lead_payda"), days),
        "ulasma": _div(row.get("lead_pay"), row.get("lead_payda")),
        "donusum": _div(row.get("satis"), row.get("lead_pay")),
        "ort_satis": _div(row.get("ciro"), row.get("satis")),
    }


def _logmean(x: float, y: float) -> float:
    return x if math.isclose(x, y) else (x - y) / (math.log(x) - math.log(y))


def decompose(fa: dict[str, float | None], fb: dict[str, float | None]) -> dict[str, float] | None:
    """Her çarpanın TL etkisi (A − B). Bir tarafta sıfır/eksik çarpan varsa None."""
    values = [(fa[k], fb[k]) for k, _, _ in FACTORS]
    if any(not x or not y for x, y in values):
        return None
    ciro_a = math.prod(x for x, _ in values)
    ciro_b = math.prod(y for _, y in values)
    weight = _logmean(ciro_a, ciro_b)
    return {k: weight * math.log(x / y) for (k, _, _), (x, y) in zip(FACTORS, values)}


def movers(a_reps: Sequence[dict[str, Any]], b_reps: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tüm temsilciler, en çok düşenden en çok artana. İki dönemde de cirosu olmayan atlanır."""
    b_by = {r["rep_id"]: r for r in b_reps}
    out = []
    for ra in a_reps:
        rb = b_by.get(ra["rep_id"]) or {}
        ciro_a, ciro_b = float(ra.get("ciro") or 0), float(rb.get("ciro") or 0)
        if not ciro_a and not ciro_b:
            continue
        out.append(
            {
                "rep_id": ra["rep_id"],
                "name": ra.get("name"),
                "ciro_a": round(ciro_a, 2),
                "ciro_b": round(ciro_b, 2),
                "fark": round(ciro_a - ciro_b, 2),
                "satis_a": int(ra.get("satis") or 0),
                "satis_b": int(rb.get("satis") or 0),
                "donusum_a": _div(ra.get("satis"), ra.get("lead_pay")),
                "donusum_b": _div(rb.get("satis"), rb.get("lead_pay")),
                "sure_a": ra.get("sure_ort"),
                "sure_b": rb.get("sure_ort"),
            }
        )
    out.sort(key=lambda r: r["fark"])
    return out


def mix_rows(a: dict[str, tuple[int, float]], b: dict[str, tuple[int, float]]) -> list[dict[str, Any]]:
    """Paket bazında adet ve ciro, iki taraf yan yana; ciro farkı büyükten küçüğe."""
    out = []
    for package in set(a) | set(b):
        (na, ca), (nb, cb) = a.get(package, (0, 0.0)), b.get(package, (0, 0.0))
        out.append(
            {
                "paket": package,
                "adet_a": na,
                "adet_b": nb,
                "ciro_a": round(ca, 2),
                "ciro_b": round(cb, 2),
                "ort_a": round(ca / na, 2) if na else None,
                "ort_b": round(cb / nb, 2) if nb else None,
                "fark": round(ca - cb, 2),
            }
        )
    out.sort(key=lambda r: abs(r["fark"]), reverse=True)
    return out


def won_by_package(first: date, last: date, rep_ids: Sequence[str]) -> dict[str, tuple[int, float]]:
    """Kazanılan deal'ler paket bazında (Raporlar cirosuyla aynı kaynak ve gün)."""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT coalesce(nullif(btrim(package), ''), 'Paketsiz'), count(*)::int,
                   coalesce(sum(amount), 0)::float
            FROM deal_packages
            WHERE org_id = %s AND stage = %s AND owner_rep_id = ANY(%s)
              AND {_WON_DAY} BETWEEN %s AND %s
            GROUP BY 1
            """,
            (get_org_id(), WON_STAGE, list(rep_ids), first, last),
        ).fetchall()
    return {str(p): (int(n), float(c)) for p, n, c in rows}


MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
          "Eylül", "Ekim", "Kasım", "Aralık")


def period_label(first: date, last: date) -> str:
    """1–22 Kasım · 23 Kasım · 27 Ekim – 2 Kasım."""
    if first == last:
        return f"{first.day} {MONTHS[first.month - 1]}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day}–{last.day} {MONTHS[last.month - 1]}"
    return f"{first.day} {MONTHS[first.month - 1]} – {last.day} {MONTHS[last.month - 1]}"


def tl_short(v: float) -> str:
    """955 B TL · 1,51 Mn TL; işaretsiz."""
    v = abs(v)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}".replace(".", ",") + " Mn TL"
    if v >= 1_000:
        return f"{round(v / 1000)} B TL"
    return fmt_tl(v)


def _signed(v: float) -> str:
    return f"{'−' if v < 0 else '+'}{tl_short(v)}"


def _mmss(sec: float) -> str:
    s = round(sec)
    return f"{s // 60} dk {s % 60:02d} sn"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"%{v * 100:.1f}".replace(".", ",")


FACTOR_TEXT: dict[str, Callable[[float, float], str]] = {
    "is_gunu": lambda a, b: f"İş günü {round(b)} → {round(a)}",
    "gunluk_lead": lambda a, b: f"Günde aranan lead {round(b)} → {round(a)}",
    "ulasma": lambda a, b: f"Ulaşma oranı {_pct(b)} → {_pct(a)}",
    "donusum": lambda a, b: f"Ulaşılan lead'den satışa dönüş {_pct(b)} → {_pct(a)}",
    "ort_satis": lambda a, b: f"Satış başına ortalama tutar {tl_short(b)} → {tl_short(a)}",
}

MIN_SHARE = 0.05  # farkın (ya da B cirosunun çeyreğinin) %5'inden küçük etki tespit sayılmaz


def _scale(bridge: dict[str, Any]) -> float:
    return max(abs(bridge["fark"]), 0.25 * bridge["b"]["ciro"], 1)


def _extremes(reps: Sequence[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    down = min(reps, key=lambda m: m["fark"], default=None)
    up = max(reps, key=lambda m: m["fark"], default=None)
    return (down if down and down["fark"] < 0 else None), (up if up and up["fark"] > 0 else None)


def findings(bridge: dict[str, Any]) -> list[dict[str, str]]:
    """Okunur tespitler: önce aleyhe, sonra lehe etkenler; ton neg/pos. Saf."""
    out: list[dict[str, str]] = []
    scale = _scale(bridge)
    shown = [f for f in bridge["factors"] if f["etki"] is not None and abs(f["etki"]) >= MIN_SHARE * scale]
    for f in sorted(shown, key=lambda f: f["etki"]):
        text = f"{FACTOR_TEXT[f['key']](f['a'], f['b'])}: ciroya etkisi {_signed(f['etki'])}."
        if f["key"] == "ort_satis" and f["etki"] < 0:
            top = next((m for m in bridge["paketler"] if m["fark"] < 0), None)
            if top:
                text += f" En çok düşen paket {top['paket']} ({top['adet_b']} → {top['adet_a']} adet)."
        out.append({"ton": "neg" if f["etki"] < 0 else "pos", "metin": text})
    q = {r["key"]: r for r in bridge["kalite"]}
    sure = q.get("sure_ort")
    if sure and sure["degisim"] is not None and abs(sure["degisim"]) >= 0.1:
        way = "kısaldı" if sure["degisim"] < 0 else "uzadı"
        out.append({
            "ton": "neg" if sure["degisim"] < 0 else "pos",
            "metin": f"Görüşmeler {way}: ortalama {_mmss(sure['b'])} → {_mmss(sure['a'])}.",
        })
    kat = q.get("katilim_orani")
    if kat and kat["degisim"] is not None and abs(kat["degisim"]) >= 5:
        way = "düştü" if kat["degisim"] < 0 else "arttı"
        out.append({
            "ton": "neg" if kat["degisim"] < 0 else "pos",
            "metin": f"Toplantıya katılım {way}: %{kat['b']:.0f} → %{kat['a']:.0f}.",
        })
    down, up = _extremes(bridge["movers"])
    if down:
        out.append({"ton": "neg", "metin": f"En çok düşen temsilci {down['name']}: {_signed(down['fark'])} "
                                           f"({down['satis_b']} → {down['satis_a']} satış)."})
    if up:
        out.append({"ton": "pos", "metin": f"En çok artan temsilci {up['name']}: {_signed(up['fark'])} "
                                           f"({up['satis_b']} → {up['satis_a']} satış)."})
    return out


ADVICE: dict[str, str] = {
    "ort_satis": "Premium sunumunu her nitelikli görüşmede sonuna kadar götürün: fiyatı temsilci kendisi "
                 "söylesin, bütçe yetmezse Plus'tan Premium'a geçiş yolunu sunsun; indirimi ilk itirazda vermeyin.",
    "donusum": "“Düşüneyim” anında neyin tartıldığını sorun (fiyat mı, zaman mı, eş mi?) ve her görüşmeyi "
               "gün ve saati belli bir takiple bitirin; CRM'de tarihsiz takip bırakmayın.",
    "ulasma": "Ulaşmayı artırın: yeni lead'i ilk 5 dakikada arayın, ulaşılamayana aynı gün WhatsApp ile "
              "ikinci temas kurun, aramaları lead'in açtığı saatlere kaydırın.",
    "gunluk_lead": "Arama hacmini geri getirin: temsilci başına günlük aranan lead hedefi koyun ve kayıp "
                   "zamanı Canlı ekranından gün içinde takip edin.",
    "is_gunu": "İş günü eksik; kalan günlerde günlük hedefi bu farkı kapatacak kadar yükseltin.",
}
SURE_ADVICE = ("Görüşmeler kısaldı: keşiften sonra sunuma geçmeden biten görüşmeleri kayıtlardan dinleyin; "
               "nitelikli müşteride sunum ve fiyat konuşulmadan görüşme kapanmasın.")
KATILIM_ADVICE = ("Toplantı katılımını yükseltin: saati görüşmede müşteriyle birlikte belirleyin, linki "
                  "müşteriye bırakmayın, toplantıdan 1 saat önce WhatsApp hatırlatması gönderin.")
MAX_ADVICE = 5


def advice(bridge: dict[str, Any]) -> list[str]:
    """Satışı artırmak için yapılacaklar: aleyhe etkenlerden, büyükten küçüğe. Saf."""
    out: list[str] = []
    scale = _scale(bridge)
    neg = [f for f in bridge["factors"] if f["etki"] is not None and f["etki"] <= -MIN_SHARE * scale]
    out += [ADVICE[f["key"]] for f in sorted(neg, key=lambda f: f["etki"])]
    q = {r["key"]: r for r in bridge["kalite"]}
    sure, kat = q.get("sure_ort") or {}, q.get("katilim_orani") or {}
    if sure.get("degisim") is not None and sure["degisim"] <= -0.1:
        out.append(SURE_ADVICE)
    if kat.get("degisim") is not None and kat["degisim"] <= -5:
        out.append(KATILIM_ADVICE)
    down, up = _extremes(bridge["movers"])
    people = []
    if down:
        people.append(f"{down['name']} ile bu hafta birebir kayıt dinleme yapın; düşüşün en büyük kısmı burada.")
    if up and (out or down):
        people.append(f"{up['name']} adlı temsilcinin iyi görüşmelerini ekip toplantısında dinletin.")
    # Kişi önerileri sınıra takılmasın: genel önerilerden kesilir.
    return out[: MAX_ADVICE - len(people)] + people


def summary_line(bridge: dict[str, Any]) -> str:
    """Kartın büyük satırı: Ciro %39 düştü."""
    delta, rate = bridge["fark"], bridge["fark_orani"]
    if math.isclose(delta, 0, abs_tol=1):
        return "Ciro iki dönemde aynı"
    way = "düştü" if delta < 0 else "arttı"
    return f"Ciro %{abs(round(rate * 100))} {way}" if rate is not None else f"Ciro {_signed(delta)} {way}"


def quality_rows(a_row: dict[str, Any], b_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Kalite ölçüleri yan yana; yüzde ölçüde değişim puan, diğerlerinde oran."""
    out = []
    for key, label, kind in QUALITY:
        va, vb = a_row.get(key), b_row.get(key)
        va = float(va) if va is not None else None
        vb = float(vb) if vb is not None else None
        if va is None or vb is None:
            change = None
        elif kind == "yuzde":
            change = va - vb
        else:
            change = (va - vb) / vb if vb else None
        out.append({"key": key, "label": label, "kind": kind, "a": va, "b": vb, "degisim": change})
    return out


def side_payload(first: date, last: date, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "first": first,
        "last": last,
        "ciro": round(float(row.get("ciro") or 0), 2),
        "satis": int(row.get("satis") or 0),
        "lead_payda": int(row.get("lead_payda") or 0),
        "lead_pay": int(row.get("lead_pay") or 0),
        "randevu": int(row.get("randevu") or 0),
        "katilim_orani": row.get("katilim_orani"),
        "factors": factors(row),
    }


def build_bridge(
    a: Range,
    b: Range,
    a_row: dict[str, Any],
    b_row: dict[str, Any],
    *,
    a_reps: Sequence[dict[str, Any]] = (),
    b_reps: Sequence[dict[str, Any]] = (),
    mix: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Saf: iki tarafın Raporlar satırından köprü yükü."""
    sa, sb = side_payload(*a, a_row), side_payload(*b, b_row)
    effects = decompose(sa["factors"], sb["factors"])
    delta = sa["ciro"] - sb["ciro"]
    rows = []
    for key, label, kind in FACTORS:
        va, vb = sa["factors"][key], sb["factors"][key]
        rows.append(
            {
                "key": key,
                "label": label,
                "kind": kind,
                "a": va,
                "b": vb,
                "degisim": (va - vb) / vb if va is not None and vb else None,
                "etki": round(effects[key], 2) if effects else None,
            }
        )
    sa["label"], sb["label"] = period_label(*a), period_label(*b)
    out = {
        "a": sa,
        "b": sb,
        "fark": round(delta, 2),
        "fark_orani": delta / sb["ciro"] if sb["ciro"] else None,
        "ayristi": effects is not None,
        "factors": rows,
        "movers": movers(a_reps, b_reps),
        "paketler": list(mix),
        "kalite": quality_rows(a_row, b_row),
    }
    out["ozet"] = summary_line(out)
    out["tespitler"] = findings(out)
    out["oneriler"] = advice(out)
    return out
