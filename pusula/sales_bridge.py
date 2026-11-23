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
from collections.abc import Sequence
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
MOVERS = 4


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
    """Ciro farkı en büyük temsilciler (mutlak), sıfır farklar atlanır."""
    b_by = {r["rep_id"]: r for r in b_reps}
    out = []
    for ra in a_reps:
        rb = b_by.get(ra["rep_id"]) or {}
        ciro_a, ciro_b = float(ra.get("ciro") or 0), float(rb.get("ciro") or 0)
        if math.isclose(ciro_a, ciro_b):
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
            }
        )
    out.sort(key=lambda r: abs(r["fark"]), reverse=True)
    return out[:MOVERS]


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


def headline(delta: float, ciro_b: float, effects: dict[str, float] | None) -> str:
    """Tek cümle: fark ve en büyük etken. Saf."""
    if math.isclose(delta, 0, abs_tol=1):
        return "Ciro iki dönemde aynı."
    way = "düşük" if delta < 0 else "yüksek"
    pct = f" %{abs(round(100 * delta / ciro_b))}" if ciro_b else ""
    text = f"A dönemi B'ye göre{pct} {way} ({'−' if delta < 0 else '+'}{fmt_tl(abs(delta))})."
    if not effects:
        return text
    labels = {k: label for k, label, _ in FACTORS}
    # Farkla aynı yönde en büyük etken; ters yönde büyük bir etken de anılır.
    same = sorted((v for v in effects.items() if (v[1] < 0) == (delta < 0)), key=lambda v: -abs(v[1]))
    other = sorted((v for v in effects.items() if (v[1] < 0) != (delta < 0)), key=lambda v: -abs(v[1]))
    if same:
        k, v = same[0]
        text += f" En büyük etken {labels[k].lower()} ({'−' if v < 0 else '+'}{fmt_tl(abs(v))})."
    if other and abs(other[0][1]) >= 0.25 * abs(delta):
        k, v = other[0]
        text += f" {labels[k]} ters yönde {'+' if v > 0 else '−'}{fmt_tl(abs(v))} etkiledi."
    return text


def _mix_note(mix: Sequence[dict[str, Any]]) -> str:
    """Ciro farkı en büyük paket; adet B → A."""
    if not mix or math.isclose(mix[0]["fark"], 0, abs_tol=1):
        return ""
    top = mix[0]
    return f" Paketlerde en büyük kayma {top['paket']}: {top['adet_b']} → {top['adet_a']} adet."


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
    return {
        "a": sa,
        "b": sb,
        "fark": round(delta, 2),
        "fark_orani": delta / sb["ciro"] if sb["ciro"] else None,
        "ayristi": effects is not None,
        "factors": rows,
        "movers": movers(a_reps, b_reps),
        "paketler": list(mix),
        "headline": headline(delta, sb["ciro"], effects) + _mix_note(mix),
    }
