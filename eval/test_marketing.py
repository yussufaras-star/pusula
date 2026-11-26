"""Pazarlama önerileri: kanal tablosu, tespit eşikleri ve fiyat artışı penceresi (saf)."""

from datetime import date

from pusula.lead_channels import Lead
from pusula.marketing import build_marketing, channel_rows, side_stats

A = (date(2026, 11, 1), date(2026, 11, 25))
B = (date(2026, 10, 1), date(2026, 10, 25))


def leads(day: date, utm: str | None, n: int, *, sold: int = 0, source: str | None = None,
          wrong: int = 0) -> list[Lead]:
    out = []
    for i in range(n):
        status = "İletişim Kurulamadı/Numara Hatalı" if i < wrong else "Düşünmek İstiyor"
        out.append(Lead(day, utm, None, source, status, i < sold))
    return out


def kasim_ekim() -> tuple[dict, dict]:
    nov, oct_ = date(2026, 11, 10), date(2026, 10, 10)
    data = (
        leads(nov, "meta", 500, sold=2) + leads(oct_, "meta", 500, sold=1)
        + leads(nov, "youtube", 80, sold=3) + leads(oct_, "youtube", 110, sold=4)
        + leads(nov, "google", 270, sold=5) + leads(oct_, "google", 180, sold=7)
        + leads(nov, None, 450, sold=7, wrong=60) + leads(oct_, None, 640, sold=25)
        + leads(oct_, "direct", 25, source="Contact Form")
        + leads(nov, "tiktok", 3)
    )
    return side_stats(data, *A), side_stats(data, *B)


def test_channel_rows_merge_small_and_put_unknown_last() -> None:
    sa, sb = kasim_ekim()
    rows = channel_rows(sa, sb)
    assert [r["kanal"] for r in rows] == ["Meta", "Google", "YouTube", "Direkt", "UTM yok", "Diğer"]
    meta = rows[0]
    assert meta["satis"] == 3 and round(meta["oran"], 4) == 0.003


def test_findings_name_weak_and_best_channel_and_lost_form() -> None:
    sa, sb = kasim_ekim()
    out = build_marketing(sa, sb, today=date(2026, 3, 1))
    texts = [t["metin"] for t in out["tespitler"]]
    assert "Kaynağı belirsiz (UTM'siz) lead azaldı: 640 → 450 (−%30)." in texts
    assert "YouTube kanalından gelen lead azaldı: 110 → 80 (−%27)." in texts
    assert any(t.startswith("Meta: iki dönemde 1.000 lead'den 3 satış (%0,3)") for t in texts)
    assert any(t.startswith("En iyi dönen kanal YouTube") for t in texts)
    assert "“İletişim formu” kaynağıyla gelen lead kesildi: 25 → 0." in texts
    tips = [t["metin"] for t in out["oneriler"]]
    assert any(t.startswith("Meta çok lead getiriyor") and "YouTube kanalına" in t for t in tips)
    assert any(t.startswith("YouTube en iyi dönen kanal ama lead'i %27 azaldı") for t in tips)
    # Fiyat artışı penceresi dışında fiyat bölümü yok.
    assert not any(t["grup"] == "fiyat" for t in out["tespitler"] + out["oneriler"])


def test_price_section_in_window_with_pool_and_season() -> None:
    sa, sb = kasim_ekim()
    pool = {"Düşünmek İstiyor": 845, "Almadı-Şimdi Değil/Erteleme": 225, "Satış Fırsatı": 79,
            "Almadı-Fiyat Şoku/Bütçe Yetersizliği": 446}
    out = build_marketing(sa, sb, today=date(2026, 11, 26), pool=pool, season=(4329186.0, 2648282.0))
    fiyat = [t["metin"] for t in out["tespitler"] if t["grup"] == "fiyat"]
    assert fiyat[0] == "Yeni yıl fiyat artışına 36 gün var; mevcut fiyat 31 Aralık'a kadar geçerli."
    assert any("bekleyen 1.149 lead" in t for t in fiyat)
    assert "Geçen yıl Aralık → Ocak ciro: 4,33 Mn TL → 2,65 Mn TL (−%39)." in fiyat
    assert out["oneriler"][0]["grup"] == "fiyat"
    assert any("446 lead'e" in t["metin"] for t in out["oneriler"])


def test_data_quality_shares() -> None:
    sa, sb = kasim_ekim()
    texts = [t["metin"] for t in build_marketing(sa, sb, today=date(2026, 3, 1))["tespitler"] if t["grup"] == "veri"]
    # 450 / 1.303 UTM'siz, 60 / 1.303 hatalı.
    assert "Kaynağı (UTM) olmayan lead payı %35: bu lead'lerin hangi kanaldan geldiği bilinmiyor." in texts
    assert not any(t.startswith("Hatalı") for t in texts)
