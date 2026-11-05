"""SONAR-41 tutarlılık analizi: satışla biten görüşmeler daha yüksek puan alıyor mu?

Görüşme, müşterinin kişi kaydı (contacts: lead_id ya da thread_id) üzerinden
kazanılmış satışa (deal_packages) bağlanır. Görüşmeden sonraki WINDOW_DAYS
gün içinde (aynı gün dahil) satış kapandıysa görüşme "satışlı"dır. Görüşmeden
önce satın almış müşterinin görüşmeleri (destek, yenileme) analize girmez.

Görüşme puanı beş boyutun (o görüşmede puanlananların) ortalamasıdır.
Kontroller: aynı müşteri ilgisinde (ilgi 4-5) fark; satıştan önceki
görüşmeler (metninde satış yok) — puan "satış oldu" diye şişiyorsa bunlarda
fark kaybolurdu.

İki kapsam: "kocluk" haftalık akışın görüşmeleri (load_trend'le aynı; koçluk
başlangıcından beri, kota seçimi) ve "havuz" tutarlılık havuzu (conv_pool:
satışlıların hepsi + satışsızlardan aylara orantılı örnek). Havuzda satış
oranı örnek ağırlığıyla (meta.agirlik) hesaplanır; ortalamalar grup içinde
ağırlıksızdır (örnek aylara orantılı).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pusula import sonar41
from pusula.config import get_org_id
from pusula.metrics import TZ
from pusula.conversations import COACHING_START, SCORE_LABELS, _ready, dim_scores, week_start_of, weekly_only
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

WINDOW_DAYS = 14
DIMS = sonar41.DIM_KEYS
# (alt, üst, etiket): görüşme puanı dilimleri.
BANDS: tuple[tuple[float, float, str], ...] = (
    (0.0, 2.0, "2'nin altı"),
    (2.0, 2.5, "2 – 2,5"),
    (2.5, 3.0, "2,5 – 3"),
    (3.0, 5.01, "3 ve üstü"),
)
# Satışlı görüşme sayısına göre sonucun güveni.
CONFIDENCE = ((60, "yüksek"), (30, "orta"), (0, "ön sonuç"))


@dataclass(frozen=True)
class Call:
    scores: dict[str, float]
    sold_after: int | None  # satışa kalan gün; None satış yok
    interest: int | None
    weight: float = 1.0


def _r(v: float | None) -> float | None:
    return round(v, 2) if v is not None else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def score(call: Call) -> float:
    return sum(call.scores.values()) / len(call.scores)


def group(calls: list[Call]) -> dict[str, Any]:
    return {
        "n": len(calls),
        "ort": _r(_mean([score(c) for c in calls])),
        "puanlar": {k: _r(_mean([c.scores[k] for c in calls if k in c.scores])) for k in DIMS},
    }


def build(calls: list[Call], since: date) -> dict[str, Any]:
    """Saf: satışlı / satışsız karşılaştırması, puan dilimine göre satış oranı, kontroller."""
    sold = [c for c in calls if c.sold_after is not None]
    unsold = [c for c in calls if c.sold_after is None]
    s, u = group(sold), group(unsold)
    bands = []
    for lo, hi, label in BANDS:
        inside = [c for c in calls if lo <= score(c) < hi]
        n_sold = sum(1 for c in inside if c.sold_after is not None)
        w_all = sum(c.weight for c in inside)
        w_sold = sum(c.weight for c in inside if c.sold_after is not None)
        bands.append({
            "etiket": label, "alt": lo, "ust": min(hi, 5.0), "n": len(inside), "satis": n_sold,
            "oran": round(100.0 * w_sold / w_all, 1) if w_all else None,
        })
    hot_s = [c for c in sold if (c.interest or 0) >= 4]
    hot_u = [c for c in unsold if (c.interest or 0) >= 4]
    earlier = [c for c in sold if (c.sold_after or 0) > 0]
    gaps = {k: _r(s["puanlar"][k] - u["puanlar"][k]) if s["puanlar"][k] is not None and u["puanlar"][k] is not None else None for k in DIMS}
    ranked = sorted((k for k in DIMS if gaps[k] is not None), key=lambda k: -(gaps[k] or 0))
    return {
        "since": since,
        "pencere_gun": WINDOW_DAYS,
        "score_labels": SCORE_LABELS,
        "n": len(calls),
        "satisli": s,
        "satissiz": u,
        "fark": _r(s["ort"] - u["ort"]) if s["ort"] is not None and u["ort"] is not None else None,
        "boyut_fark": gaps,
        "en_ayirici": ranked[:2],
        "en_az_ayirici": ranked[-1] if ranked else None,
        "dilimler": bands,
        "kontrol": {
            "ilgili_satisli": group(hot_s),
            "ilgili_satissiz": group(hot_u),
            "satis_oncesi": group(earlier),
        },
        "guven": next(label for limit, label in CONFIDENCE if len(sold) >= limit),
    }


def load_validity() -> dict[str, Any]:
    org = get_org_id()
    since = week_start_of(COACHING_START)
    with connect() as conn:
        _ready(conn)
        rows = conn.execute(
            f"""
            WITH items AS (
                SELECT c.id, c.thread_id, c.lead_id, c.analysis, c.status, c.meta, c.occurred_at,
                       (c.occurred_at AT TIME ZONE 'Europe/Istanbul')::date AS gun
                FROM conv_items c
                WHERE c.org_id = %s AND c.kind = 'call'
                  AND (c.meta ? 'havuz' OR (c.status = 'analiz_edildi'
                       AND NOT coalesce((c.meta->>'talep')::boolean, false) AND {weekly_only()}))
            ),
            won AS (
                SELECT DISTINCT i.id, coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date) - i.gun AS fark
                FROM items i
                JOIN contacts ct ON ct.org_id = %s
                 AND ((i.lead_id IS NOT NULL AND ct.lead_id = i.lead_id) OR (i.thread_id IS NOT NULL AND ct.thread_id = i.thread_id))
                JOIN deal_packages d ON d.org_id = ct.org_id AND d.contact_id = ct.contact_id AND d.stage = %s
            )
            SELECT i.analysis, i.status, i.meta, i.occurred_at,
                   (SELECT min(w.fark) FROM won w WHERE w.id = i.id AND w.fark BETWEEN 0 AND %s),
                   EXISTS (SELECT 1 FROM won w WHERE w.id = i.id AND w.fark < 0)
            FROM items i
            """,
            (org, org, WON_STAGE, WINDOW_DAYS),
        ).fetchall()
    coaching: list[Call] = []
    pool: list[Call] = []
    state: dict[str, int] = {"toplam": 0, "analiz": 0, "sirada": 0, "hata": 0}
    first: date | None = None
    for analysis, status, meta, at, sold_after, customer_before in rows:
        meta = meta or {}
        in_pool = "havuz" in meta
        if in_pool:
            state["toplam"] += 1
            key = "analiz" if status == "analiz_edildi" else "sirada" if status in ("bekliyor", "yazildi") else "hata"
            state[key] += 1
        a = analysis or {}
        if status != "analiz_edildi" or customer_before or not a.get("analiz_edilebilir"):
            continue
        scores = dim_scores(a)
        if not scores:
            continue
        interest = (a.get("musteri") or {}).get("ilgi")
        call = Call(scores, sold_after, int(interest) if interest else None)
        weekly = not meta.get("talep") and not meta.get("havuz_disi")
        day = at.astimezone(TZ).date()
        if weekly and day >= since:
            coaching.append(call)
        if in_pool:
            weight = float(meta.get("agirlik") or 1.0) if meta.get("havuz") == "genel" else 1.0
            pool.append(Call(scores, sold_after, call.interest, weight))
            first = day if first is None or day < first else first
    return {
        "kocluk": build(coaching, since),
        "havuz": build(pool, first) if pool else None,
        "havuz_durum": state,
    }
