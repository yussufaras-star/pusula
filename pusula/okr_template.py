"""Önerilen OKR taslağı: 2026 Ç4 (süren çeyrek) ve 2027 yılı + dört çeyrek.

Rakamlar 2026 gerçekleşmesinden türetildi (pusula.sales_report, 19 Ekim):
Ç3 ciro 6,92 M TL, 190 satış, ort. satış 36,4 bin TL, ulaşma %66,3,
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


def _q4_2026() -> list[dict[str, Any]]:
    return [
        {
            "quarter": 4, "kind": "taahhut", "status": "aktif",
            "title": "Eylül ivmesini kalıcı yap: yılı rekor çeyrekle kapat",
            "description": "Eylül'de 3,36 M TL ile en iyi ayımızı yaptık. Bu tempo tesadüf değil, standart olmalı.",
            "krs": [
                _kr("Çeyrek cirosu 10 M TL", "ciro", 0, 10_000_000, "tl"),
                _kr("270 satış kapat", "satis", 0, 270, "adet"),
                _kr("Ortalama satış tutarını 36,4 binden 40 bin TL'ye çıkar", "ort_satis", 36_400, 40_000, "tl"),
            ],
        },
        {
            "quarter": 4, "kind": "iddiali", "status": "aktif",
            "title": "Her toplantı satışa yakın bir toplantı olsun",
            "description": "Toplantıya gelmeyen müşteri boşa giden bir saat demek. Katılımı ve dönüşümü birlikte büyüt.",
            "krs": [
                _kr("Toplantı katılım oranı %36'dan %55'e", "katilim_orani", 36, 55, "yuzde"),
                _kr("Ulaşılan lead'den toplantıya dönüş %15,6'dan %22'ye", "toplanti_donus", 15.6, 22, "yuzde"),
                _kr("Satış oranı (satış / ulaşılan lead) %4,4'ten %6'ya", "satis_orani", 4.4, 6, "yuzde"),
            ],
        },
        {
            "quarter": 4, "kind": "iddiali", "status": "aktif",
            "title": "Mesaiyi müşteriyle geçir: disiplinli ve dolu gün",
            "description": "Satış zamanı en kıt kaynağımız. Boşta geçen dakikayı müşteriyle konuşmaya çevir.",
            "krs": [
                _kr("Doluluk %38,5'ten %80'e", "doluluk", 38.5, 80, "yuzde"),
                _kr("Ulaşma oranı %66'dan %70'e", "ulasma_orani", 66, 70, "yuzde"),
                _kr("Ortalama görüşme süresi 236 sn'den 300 sn'ye", "sure_ort", 236, 300, "sn"),
            ],
        },
    ]


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
        # --- 1. çeyrek ---
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
        # --- 2. çeyrek ---
        {
            "quarter": 2, "kind": "taahhut", "status": draft, "parent": "y_buyume",
            "title": "Büyümeyi hızlandır: 2. çeyrekte 12 M TL",
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
        # --- 3. çeyrek ---
        {
            "quarter": 3, "kind": "taahhut", "status": draft, "parent": "y_buyume",
            "title": "Yaz durgunluğunu kır: 3. çeyrekte 13 M TL",
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
        # --- 4. çeyrek ---
        {
            "quarter": 4, "kind": "taahhut", "status": draft, "parent": "y_buyume",
            "title": "Yılı rekorla kapat: 4. çeyrekte 14 M TL",
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


TEMPLATES = {2026: _q4_2026, 2027: _year_2027}


def template_for(year: int) -> list[dict[str, Any]] | None:
    build = TEMPLATES.get(year)
    return build() if build else None
