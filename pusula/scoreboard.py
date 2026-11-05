"""Haftalık puan tablosu: temsilciler SONAR-41 beş boyutunda (K1-K5).

Yalnız yönetici görür; isterse kart görseli Cliq kanalına gönderilir.
Sıralama iki türlü: gelişime göre (varsayılan) ve puana göre. Gelişim =
haftanın puanı − temsilcinin önceki BASE_WEEKS haftasındaki görüşmelerinin
ortalaması (havuz: hafta ortalamalarının ortalaması değil, görüşmelerin).
Böylece puanı düşük temsilci de "en çok gelişen" olabilir.

Puan: görüşmenin boyut puanı = o boyutta puanlanan davranışların ortalaması
(conversations.dim_scores); haftanın boyut puanı görüşmelerin ortalaması.
Haftada MIN_CALLS'tan az analizli görüşme gürültülüdür: o hafta (ya da
kıyas dönemi) "yetersiz veri" sayılır, sıralamaya ve liderliğe girmez.
Kaynak load_trend'le aynı: analiz edilmiş, özel talep olmayan görüşmeler.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from pusula import sonar41
from pusula.config import get_org_id
from pusula.conversations import (
    COACHING_START, SCORE_LABELS, _ready, dim_scores, week_bounds, week_start_of, weekly_only,
)
from pusula.panel_data import connect

DIMS = sonar41.DIM_KEYS
BASE_WEEKS = 4
MIN_CALLS = 8
# Liderlik/gelişim eşitliği: 0,05'ten küçük fark aynı sayılır.
TIE = 0.05

Call = tuple[str, date, dict[str, float]]  # rep_id, hafta başı, boyut puanları


def _r(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


def pool(calls: list[dict[str, float]]) -> dict[str, Any]:
    """Saf: görüşmelerin boyut ortalamaları ve beşinin ortalaması."""
    by_dim: dict[str, list[float]] = defaultdict(list)
    for scores in calls:
        for k, v in scores.items():
            by_dim[k].append(v)
    dims = {k: _r(sum(by_dim[k]) / len(by_dim[k])) if by_dim.get(k) else None for k in DIMS}
    present = [v for v in dims.values() if v is not None]
    return {"n": len(calls), "puanlar": dims, "ort": _r(sum(present) / len(present)) if present else None}


def _delta(cur: float | None, base: float | None) -> float | None:
    return _r(cur - base) if cur is not None and base is not None else None


def _top(rows: list[dict[str, Any]], value: Any) -> list[str]:
    """En yüksek değerdeki temsilci adları (TIE içinde eşit); pozitif olmayan gelişim lider sayılmaz."""
    vals = [(value(r), r["name"]) for r in rows if value(r) is not None]
    if not vals:
        return []
    best = max(v for v, _ in vals)
    return [name for v, name in vals if best - v < TIE]


def build(calls: list[Call], names: dict[str, str], week: date) -> dict[str, Any]:
    """Saf: seçilen haftanın tablosu."""
    first = week - timedelta(weeks=BASE_WEEKS)
    cur: dict[str, list[dict[str, float]]] = defaultdict(list)
    base: dict[str, list[dict[str, float]]] = defaultdict(list)
    prev: dict[str, list[dict[str, float]]] = defaultdict(list)
    for rep_id, wk, scores in calls:
        if wk == week:
            cur[rep_id].append(scores)
        elif first <= wk < week:
            base[rep_id].append(scores)
            if wk == week - timedelta(weeks=1):
                prev[rep_id].append(scores)
    rows = []
    for rep_id in sorted(set(cur) | set(base)):
        now, before, last = pool(cur.get(rep_id, [])), pool(base.get(rep_id, [])), pool(prev.get(rep_id, []))
        enough = now["n"] >= MIN_CALLS
        has_base = before["n"] >= MIN_CALLS
        gelisim = {k: _delta(now["puanlar"][k], before["puanlar"][k]) if enough and has_base else None for k in DIMS}
        scored = [(k, v) for k, v in now["puanlar"].items() if v is not None]
        moved = [(k, v) for k, v in gelisim.items() if v is not None]
        rows.append({
            "rep_id": rep_id,
            "name": names.get(rep_id, rep_id),
            "n": now["n"],
            "puanlar": now["puanlar"],
            "ort": now["ort"],
            "onceki_n": before["n"],
            "onceki_puanlar": before["puanlar"],
            "onceki_ort": before["ort"],
            "gecen_hafta_ort": last["ort"] if last["n"] >= MIN_CALLS else None,
            "gelisim": gelisim,
            "gelisim_ort": _delta(now["ort"], before["ort"]) if enough and has_base else None,
            "yeterli": enough,
            "kiyas_var": has_base,
            # Koçluk cümlesi için: en çok ilerlediği ve en düşük kaldığı boyut.
            "en_iyi_gelisim": max(moved, key=lambda kv: kv[1])[0] if moved and max(v for _, v in moved) > 0 else None,
            "odak": min(scored, key=lambda kv: kv[1])[0] if enough and scored else None,
        })
    ranked = [r for r in rows if r["yeterli"]]
    by_growth = sorted(
        ranked, key=lambda r: (r["gelisim_ort"] is None, -(r["gelisim_ort"] or 0), -(r["ort"] or 0), r["name"])
    )
    by_score = sorted(ranked, key=lambda r: (-(r["ort"] or 0), r["name"]))
    for i, r in enumerate(by_growth):
        r["sira_gelisim"] = i + 1 if r["gelisim_ort"] is not None else None
    for i, r in enumerate(by_score):
        r["sira_puan"] = i + 1
    for r in rows:
        r.setdefault("sira_gelisim", None)
        r.setdefault("sira_puan", None)

    liderler = {
        k: {
            "puan": _top(ranked, lambda r, k=k: r["puanlar"][k]),
            "gelisim": _top(ranked, lambda r, k=k: g if (g := r["gelisim"][k]) is not None and g > 0 else None),
        }
        for k in DIMS
    }
    team_now = pool([s for v in cur.values() for s in v])
    team_base = pool([s for v in base.values() for s in v])
    return {
        "week_start": week,
        "week_end": week + timedelta(days=6),
        "kiyas_bas": first,
        "min_gorusme": MIN_CALLS,
        "kiyas_hafta": BASE_WEEKS,
        "score_labels": SCORE_LABELS,
        "temsilciler": rows,
        "en_cok_gelisen": _top(ranked, lambda r: g if (g := r["gelisim_ort"]) is not None and g > 0 else None),
        "liderler": liderler,
        "ekip": {
            "n": team_now["n"],
            "puanlar": team_now["puanlar"],
            "ort": team_now["ort"],
            "onceki_puanlar": team_base["puanlar"],
            "onceki_ort": team_base["ort"],
            "gelisim": {k: _delta(team_now["puanlar"][k], team_base["puanlar"][k]) for k in DIMS},
            "gelisim_ort": _delta(team_now["ort"], team_base["ort"]),
        },
        "kocluk_baslangic": week_start_of(COACHING_START),
    }


def load_scoreboard(week: date) -> dict[str, Any]:
    start, _ = week_bounds(week - timedelta(weeks=BASE_WEEKS))
    _, end = week_bounds(week)
    with connect() as conn:
        _ready(conn)
        rows = conn.execute(
            f"""
            SELECT c.rep_id, c.occurred_at, c.analysis FROM conv_items c
            JOIN reps r ON r.org_id = c.org_id AND r.rep_id = c.rep_id
            WHERE c.org_id = %s AND c.status = 'analiz_edildi' AND r.category = 'sales'
              AND NOT coalesce((c.meta->>'talep')::boolean, false) AND {weekly_only()}
              AND c.occurred_at >= %s AND c.occurred_at < %s
            """,
            (get_org_id(), start, end),
        ).fetchall()
        names = conn.execute(
            "SELECT rep_id, full_name FROM reps WHERE org_id = %s AND category = 'sales'", (get_org_id(),)
        ).fetchall()
    from pusula.metrics import TZ

    calls: list[Call] = []
    for rep_id, at, analysis in rows:
        if not (analysis or {}).get("analiz_edilebilir"):
            continue
        scores = dim_scores(analysis)
        if scores:
            moment = at.astimezone(TZ) if isinstance(at, datetime) else at
            calls.append((str(rep_id), week_start_of(moment.date()), scores))
    return build(calls, {str(k): str(v) for k, v in names}, week)
