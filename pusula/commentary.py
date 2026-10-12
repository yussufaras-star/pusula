"""Yöneticiye günün durumu hakkında kısa yapay zekâ yorumu.

Girdi canlı kokpit yükünün özeti (temsilci adları ve sayılar; müşteri bilgisi
yok). Yalnız yöneticiye gösterilir; temsilciye gitmediği için onay gerekmez.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pusula import llm
from pusula.metrics import TZ

SYSTEM = """Sen Rexven telesatış operasyon yöneticisinin analistisin. Sana \
canlı panelin özet verisi JSON olarak geliyor. Yöneticiye Türkçe, kısa bir \
durum yorumu yaz.

Biçim: 3-5 madde, her biri "- " ile başlayan tek cümle. Önce en önemli risk, \
sonra iyi giden bir şey, en sonda önümüzdeki saat için yöneticinin \
yapabileceği somut bir hamle (kiminle konuşmalı, neye bakmalı). Başlık, \
giriş veya kapanış cümlesi yazma.

Kurallar:
- Yalnız verideki sayılara dayan; tahmin veya uydurma yok.
- "hedef_kaynagi": "ortalama" ise yöneticinin girdiği bir hedef yoktur, kıyas \
geçmiş ortalamayadır; bunu "hedef" diye sunma, "ortalama" de.
- İzinli temsilcileri performans yorumuna katma.
- Örneklem küçükse (ör. 3 aramadan oran) oranı yorumlama."""


def _target_view(target: dict[str, Any]) -> dict[str, Any]:
    return {
        metric: {
            "gerceklesen": item.get("actual"),
            "bu_saate_kadar_beklenen": item.get("by_now"),
            "gunluk": item.get("daily"),
            "hedef_kaynagi": "ortalama" if item.get("source") == "ortalama" else "yonetici",
        }
        for metric, item in (target or {}).items()
    }


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    now = payload["now"]
    local = now.astimezone(TZ) if isinstance(now, datetime) else now
    by_rep = (payload.get("meetings") or {}).get("by_rep", {})
    team = payload["team"]
    return {
        "saat": f"{local:%H:%M}" if isinstance(local, datetime) else str(local),
        "ekip": {
            "hedefler": _target_view(team.get("target") or {}),
            "ulasma_orani": team["today"].get("ulasma_orani"),
            "doluluk": team["today"].get("doluluk"),
            "kayip_zaman_dk": team["today"].get("kayip_dk"),
            "sonucu_girilmemis_toplanti": team["today"].get("sonuc_girilmedi"),
        },
        "temsilciler": [
            {
                "ad": card["name"],
                "durum": card.get("status"),
                "izinli": card.get("on_leave"),
                "hareketsiz_dk": card.get("idle_min"),
                "hedefler": _target_view(card.get("target") or {}),
                "ulasma_orani": card["today"].get("ulasma_orani"),
                "aramalarin_payda": card["today"].get("lead_payda"),
                "kayip_zaman_dk": card["today"].get("kayip_dk"),
                "toplantilar": {
                    k: (by_rep.get(card["rep_id"]) or {}).get(k)
                    for k in ("planlanan", "gelen", "gelmeyen", "sonuc_girilmemis", "aranmayan")
                },
            }
            for card in payload["reps"]
        ],
    }


def commentary(payload: dict[str, Any]) -> str | None:
    facts = summarize(payload)
    return llm.complete(
        SYSTEM,
        "Panel özeti:\n" + json.dumps(facts, ensure_ascii=False, default=str),
        max_tokens=3000,
    )
