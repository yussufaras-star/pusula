"""Lead akışı kanal kırılımı: kanal eşleme, haftalık seri, kalite (saf)."""

from datetime import date, timedelta

from pusula.lead_channels import MIN_LEADS, NO_CAMPAIGN, NO_UTM, OTHER, Lead, build, channel, form, quality

TODAY = date(2026, 11, 5)  # perşembe
LAST = date(2026, 10, 26)


def lead(day: date, utm: str | None = "meta", status: str | None = "Randevu", sold: bool = False, **kw: str) -> Lead:
    return Lead(day, utm, kw.get("campaign"), kw.get("source"), status, sold)


def test_channel_and_form_mapping() -> None:
    assert channel("Meta") == "Meta"
    assert channel("l.facebook.com") == "Meta"
    assert channel("www.youtube.com") == "YouTube"
    assert channel("ig") == "Instagram"
    assert channel("") == NO_UTM and channel(None) == NO_UTM
    assert channel("yandex") == OTHER
    assert form("WhatsApp - Rexven-+90538") == "WhatsApp"
    assert form("Contact Form") == "İletişim formu"
    assert form(None) == "Belirtilmemiş"


def test_quality_excludes_wrong_numbers_from_reached() -> None:
    leads = [
        lead(LAST, status="Randevu"),
        lead(LAST, status="Nitelikli Değil-İlgisiz"),
        lead(LAST, status="İletişim Kurulamadı/Numara Hatalı"),
        lead(LAST, status="Aging"),
        lead(LAST, status="Yeni Müşteri Adayı", sold=True),
    ]
    q = quality(leads)
    assert q["lead"] == 5
    assert q["ulasildi"] == 50.0  # 4 işlenen; hatalı no ulaşılmış sayılmaz
    assert (q["randevu"], q["nitelikli_degil"], q["hatali"]) == (20.0, 20.0, 20.0)
    assert (q["satis"], q["satis_orani"]) == (1, 20.0)


def test_weekly_change_share_and_small_rows_fold() -> None:
    prev = LAST - timedelta(weeks=1)
    leads = [lead(prev) for _ in range(10)] + [lead(LAST) for _ in range(12)]
    leads += [lead(LAST, utm="google") for _ in range(8)] + [lead(LAST, utm="twitter")]  # tek lead → Diğer
    leads += [lead(TODAY)]
    out = build(leads, "kanal", 2, TODAY)
    rows = {r["ad"]: r for r in out["satirlar"]}
    assert set(rows) == {"Meta", "Google", OTHER}
    meta = rows["Meta"]
    assert meta["seri"] == [10, 12, 1] and meta["degisim"] == 20.0
    assert rows["Google"]["degisim"] is None  # önceki hafta < MIN_LEADS
    assert meta["pay"] == 57.1  # 12 / 21
    assert out["toplam"]["son"] == 21
    assert out["satirlar"][-1]["ad"] == OTHER
    assert out["haftalar"][-1]["suruyor"]


def test_campaign_keeps_top_and_no_utm_last() -> None:
    leads = [lead(LAST, campaign="a") for _ in range(MIN_LEADS)] + [lead(LAST, utm=None) for _ in range(9)]
    out = build(leads, "kampanya", 2, TODAY)
    assert [r["ad"] for r in out["satirlar"]] == ["a", NO_CAMPAIGN]
    assert out["utm_payi"] == round(100 * 5 / 14, 1)
