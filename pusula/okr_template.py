"""Önerilen OKR taslağı: 2026 (yıl başı planı) ve 2027 yılı + dört çeyrek.

2026 rakamları yöneticinin yıl başı planıdır (PLAN_2026). 2027 ve 2026 Q4
kaldıraç hedefleri 2026 gerçekleşmesinden türetildi (pusula.sales_report, 19 Ekim):
Q3 ciro 6,92 M TL, 190 satış, ort. satış 36,4 bin TL, ulaşma %66,3,
katılım %36,3, toplantıya dönüş %15,6, satış oranı %4,4, doluluk %38,5,
ort. görüşme 236 sn, döngü 8,6 gün. Ekim'in ilk 15 iş günü 1,95 M TL
(çeyreklik koşu ~10 M). Taslaktır: yönetici panelden değiştirir.

Doerr kuralları: hedef nitel ve ilham verici, anahtar sonuç sayısal;
ciro gibi "olmazsa olmaz"lar taahhüt, dönüşüm sıçramaları iddialı.
"""

from __future__ import annotations

from typing import Any


def _kr(title: str, metric: str | None, start: float, target: float, unit: str = "sayi", direction: str = "up") -> dict[str, Any]:
    return {"title": title, "metric": metric, "start": start, "target": target, "unit": unit, "direction": direction}


# 2026 yıl başı satış planı (yönetici, Ocak 2026): aylık yeni müşteri, ciro ve
# lead hedefleri. Plandaki "Hedef Contacts" lead→contact dönüşümü içindi;
# lead artık doğrudan temsilciye aktığından lead hedefi olarak alınır.
# Huni varsayımı: ulaşma %60, take (satış / ulaşılan lead) %3,2.
PLAN_2026 = {
    "ciro": [
        4_464_050, 4_464_050, 4_511_040, 4_745_990, 3_430_270, 3_900_170,
        2_819_400, 3_900_170, 4_839_970, 3_148_330, 5_920_740, 4_370_070,
    ],
    "satis": [95, 95, 96, 101, 73, 83, 60, 83, 103, 67, 126, 93],
    "lead": [4948, 4948, 5000, 5260, 3802, 4323, 3125, 4323, 5365, 3490, 6563, 4844],
}
PLAN_2026_QUARTER_LEADS = {1: 14_896, 2: 13_385, 3: 12_813, 4: 14_896}
PLAN_2026_YEAR = {"ciro": 50_514_250, "satis": 1075, "lead": 55_990}
REACH_PLAN = 60.0
TAKE_PLAN = 3.2


def _mn(v: float) -> str:
    return f"{v / 1_000_000:.2f}".replace(".", ",") + " M TL"


def _plan_krs(months: slice, *, leads: float | None = None) -> list[dict[str, Any]]:
    ciro = PLAN_2026["ciro"][months]
    satis = PLAN_2026["satis"][months]
    lead = PLAN_2026["lead"][months]
    lead_target = leads if leads is not None else sum(lead)
    return [
        {**_kr(f"Ciro {_mn(sum(ciro))}", "ciro", 0, sum(ciro), "tl"), "phasing": ciro},
        {**_kr(f"{sum(satis)} yeni müşteri (satış)", "satis", 0, sum(satis), "adet"), "phasing": satis},
        {**_kr(f"{lead_target:,.0f} lead gelir".replace(",", "."), "gelen_lead", 0, lead_target, "adet"), "phasing": lead},
    ]


def _funnel_krs() -> list[dict[str, Any]]:
    return [
        _kr(f"Ulaşma oranı en az %{REACH_PLAN:.0f}", "ulasma_orani", 0, REACH_PLAN, "yuzde"),
        _kr("Take oranı (yeni müşteri / ulaşılan lead) en az %3,2", "satis_orani", 0, TAKE_PLAN, "yuzde"),
    ]


MONTH_NAMES = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _month_note(q: int) -> str:
    idx = range(3 * q - 3, 3 * q)
    parts = [f"{MONTH_NAMES[i]} {_mn(PLAN_2026['ciro'][i])} / {PLAN_2026['satis'][i]} satış" for i in idx]
    return "Yıl başı planı, aylık: " + " · ".join(parts) + "."


def _year_2026() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {
            "key": "y2026", "quarter": 0, "kind": "taahhut", "status": "aktif",
            "title": "2026 planını teslim et: 50,5 M TL ciro, 1.075 yeni müşteri",
            "description": (
                "Ocak'ta koyduğumuz yıllık plan. Ciro, satış ve lead çeyreklerin toplamıdır. "
                "Lead: Zoho Leads modülüne gelen (contact'a dönüştürülenler dahil, 1-2 Nisan taşıması hariç)."
            ),
            "krs": [
                {**_kr("Ciro 50,51 M TL", "ciro", 0, PLAN_2026_YEAR["ciro"], "tl"), "phasing": PLAN_2026["ciro"]},
                {**_kr("1.075 yeni müşteri (satış)", "satis", 0, PLAN_2026_YEAR["satis"], "adet"), "phasing": PLAN_2026["satis"]},
                {**_kr("55.990 lead gelir", "gelen_lead", 0, PLAN_2026_YEAR["lead"], "adet"), "phasing": PLAN_2026["lead"]},
                *_funnel_krs(),
            ],
        },
    ]
    for q in range(1, 5):
        months = slice(3 * q - 3, 3 * q)
        status = "aktif" if q == 4 else "kapandi"
        out.append(
            {
                "quarter": q, "kind": "taahhut", "status": status, "parent": "y2026",
                "title": f"Q{q} planı: {_mn(sum(PLAN_2026['ciro'][months]))} ciro, {sum(PLAN_2026['satis'][months])} yeni müşteri",
                "description": _month_note(q),
                "krs": _plan_krs(months, leads=PLAN_2026_QUARTER_LEADS[q]),
            }
        )
        out.append(
            {
                "quarter": q, "kind": "taahhut", "status": status, "parent": "y2026",
                "title": "Huni plan oranlarında çalışsın: her lead'e ulaş, ulaştığını satışa çevir",
                "description": "Plan varsayımı: lead × %60 ulaşma × %3,2 take = yeni müşteri.",
                "krs": _funnel_krs(),
            }
        )
    # Q4: planın üstüne kaldıraçlar (iddialı). Bazlar 2026 Q3 gerçekleşmesi.
    out += [
        {
            "quarter": 4, "kind": "iddiali", "status": "aktif", "parent": "y2026",
            "title": "Her toplantı satışa yakın bir toplantı olsun",
            "description": "Toplantıya gelmeyen müşteri boşa giden bir saat demek. Katılımı ve dönüşümü birlikte büyüt.",
            "krs": [
                _kr("Toplantı katılım oranı %36'dan %55'e", "katilim_orani", 36, 55, "yuzde"),
                _kr("Ulaşılan lead'den toplantıya dönüş %15,6'dan %22'ye", "toplanti_donus", 15.6, 22, "yuzde"),
            ],
        },
        {
            "quarter": 4, "kind": "iddiali", "status": "aktif", "parent": "y2026",
            "title": "Mesaiyi müşteriyle geçir: disiplinli ve dolu gün",
            "description": "Satış zamanı en kıt kaynağımız. Boşta geçen dakikayı müşteriyle konuşmaya çevir.",
            "krs": [
                _kr("Doluluk %38,5'ten %80'e", "doluluk", 38.5, 80, "yuzde"),
                _kr("Ortalama görüşme süresi 236 sn'den 300 sn'ye", "sure_ort", 236, 300, "sn"),
            ],
        },
    ]
    return out


def _year_2027() -> list[dict[str, Any]]:
    draft = "taslak"
    return [
        # --- yıllık: çeyrek hedefleri bunlara hizalanır ---
        {
            "key": "y_buyume", "quarter": 0, "kind": "iddiali", "status": draft,
            "title": "Rexven satışını öngörülebilir bir büyüme motoruna dönüştür",
            "description": "2026'da kurduğumuz ölçüm altyapısını 2027'de büyümeye çeviriyoruz.",
            "krs": [
                _kr("Yıllık ciro 50 M TL", "ciro", 0, 50_000_000, "tl"),
                _kr("1.290 satış", "satis", 0, 1290, "adet"),
                _kr("Satış döngüsü medyanı 8,6 günden 5 güne", "dongu_gun", 8.6, 5, "gun", "down"),
            ],
        },
        {
            "key": "y_ekip", "quarter": 0, "kind": "taahhut", "status": draft,
            "title": "Her temsilci her çeyrek kendi rekorunu kıran bir satış profesyoneli olsun",
            "description": "Büyüme kişi eklemekten değil, her temsilcinin verimini artırmaktan gelecek.",
            "krs": [
                _kr("Doluluk yıl boyunca %80", "doluluk", 38.5, 80, "yuzde"),
                _kr("Haftalık OKR check-in uyumu %90", None, 0, 90, "yuzde"),
                _kr("Her temsilcinin her çeyrek en az 1 kişisel OKR'ı (5 temsilci × 4 çeyrek = 20)", None, 0, 20, "adet"),
            ],
        },
        # --- Q1 ---
        {
            "quarter": 1, "kind": "taahhut", "status": draft, "parent": "y_buyume",
            "title": "Yıla güçlü başla: ilk çeyrekte yıllık planın %22'si",
            "krs": [
                _kr("Çeyrek cirosu 11 M TL", "ciro", 0, 11_000_000, "tl"),
                _kr("290 satış", "satis", 0, 290, "adet"),
                _kr("Ortalama satış tutarı 40 bin TL", "ort_satis", 38_000, 40_000, "tl"),
            ],
        },
        {
            "quarter": 1, "kind": "iddiali", "status": draft, "parent": "y_ekip",
            "title": "Huninin ağzını boşa harcama: her lead'e hızlı ve kaliteli temas",
            "krs": [
                _kr("Ulaşma oranı %70", "ulasma_orani", 66, 70, "yuzde"),
                _kr("Atanan lead'in %90'ı ilk 24 saatte aranır", None, 0, 90, "yuzde"),
                _kr("Ortalama görüşme süresi 300 sn", "sure_ort", 260, 300, "sn"),
            ],
        },
        # --- Q2 ---
        {
            "quarter": 2, "kind": "taahhut", "status": draft, "parent": "y_buyume",
            "title": "Büyümeyi hızlandır: Q2'de 12 M TL",
            "krs": [
                _kr("Çeyrek cirosu 12 M TL", "ciro", 0, 12_000_000, "tl"),
                _kr("310 satış", "satis", 0, 310, "adet"),
                _kr("Satış döngüsü medyanı 6 gün", "dongu_gun", 8, 6, "gun", "down"),
            ],
        },
        {
            "quarter": 2, "kind": "iddiali", "status": draft, "parent": "y_buyume",
            "title": "Toplantıları satış makinesine çevir",
            "krs": [
                _kr("Toplantı katılım oranı %60", "katilim_orani", 50, 60, "yuzde"),
                _kr("Ulaşılan lead'den toplantıya dönüş %25", "toplanti_donus", 20, 25, "yuzde"),
                _kr("Satış oranı %6,5", "satis_orani", 5, 6.5, "yuzde"),
            ],
        },
        # --- Q3 ---
        {
            "quarter": 3, "kind": "taahhut", "status": draft, "parent": "y_buyume",
            "title": "Yaz durgunluğunu kır: Q3'te 13 M TL",
            "description": "2026'da Ağustos en zayıf ayımızdı (1,69 M TL). 2027'de yaz çukuru olmayacak.",
            "krs": [
                _kr("Çeyrek cirosu 13 M TL", "ciro", 0, 13_000_000, "tl"),
                _kr("330 satış", "satis", 0, 330, "adet"),
                _kr("En zayıf ayın cirosu en az 4 M TL", None, 0, 4_000_000, "tl"),
            ],
        },
        {
            "quarter": 3, "kind": "iddiali", "status": draft, "parent": "y_buyume",
            "title": "Sepeti büyüt: müşteri başına daha fazla değer",
            "krs": [
                _kr("Ortalama satış tutarı 45 bin TL", "ort_satis", 40_000, 45_000, "tl"),
                _kr("Satışların %30'u üst paket", None, 0, 30, "yuzde"),
                _kr("Satış oranı %7", "satis_orani", 6, 7, "yuzde"),
            ],
        },
        # --- Q4 ---
        {
            "quarter": 4, "kind": "taahhut", "status": draft, "parent": "y_buyume",
            "title": "Yılı rekorla kapat: Q4'te 14 M TL",
            "krs": [
                _kr("Çeyrek cirosu 14 M TL", "ciro", 0, 14_000_000, "tl"),
                _kr("360 satış", "satis", 0, 360, "adet"),
                _kr("Satış döngüsü medyanı 5 gün", "dongu_gun", 6, 5, "gun", "down"),
            ],
        },
        {
            "quarter": 4, "kind": "taahhut", "status": draft, "parent": "y_ekip",
            "title": "2028'e hazır gir: ekip ve süreç olgunluğu",
            "krs": [
                _kr("Doluluk %85", "doluluk", 80, 85, "yuzde"),
                _kr("2028 OKR taslağı 15 Aralık'a kadar hazır (0/1)", None, 0, 1, "sayi"),
                _kr("Yıl sonu her temsilcinin kişisel OKR ortalaması 0,7+", None, 0, 0.7, "sayi"),
            ],
        },
    ]


TEMPLATES = {2026: _year_2026, 2027: _year_2027}


def template_for(year: int) -> list[dict[str, Any]] | None:
    build = TEMPLATES.get(year)
    return build() if build else None
