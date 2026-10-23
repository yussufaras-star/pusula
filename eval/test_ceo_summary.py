"""CEO özeti: raporlanan gün, tempo, istisnalar, metin (veritabanısız)."""

from datetime import date

from pusula import ceo_summary as cs


def test_report_day_skips_sunday() -> None:
    assert cs.report_day(date(2026, 10, 23)) == date(2026, 10, 22)  # cuma → perşembe
    assert cs.report_day(date(2026, 10, 26)) == date(2026, 10, 24)  # pazartesi → cumartesi


def test_pace_and_plan() -> None:
    assert cs.pace(57, 19, 27) == 57 / 19 * 27
    assert cs.pace(5, 0, 27) is None
    assert cs.month_plan(date(2026, 10, 1)) == {"satis": 67, "ciro": 3_148_330}
    assert cs.month_plan(date(2027, 1, 1)) is None


def test_exceptions_only_deviations() -> None:
    reps = [
        {"rep_id": "a", "name": "Ayşe", "arama": 40, "doluluk": 85.0},
        {"rep_id": "b", "name": "Beytullah", "arama": 0, "doluluk": None},
        {"rep_id": "c", "name": "Can", "arama": 30, "doluluk": 48.0},
        {"rep_id": "d", "name": "Deniz", "arama": 0, "doluluk": None},
    ]
    assert cs.exceptions(reps, {"d"}) == ["Beytullah hiç arama yapmadı", "Can doluluk %48", "Deniz izinli"]


def test_render_has_result_month_funnel_and_team() -> None:
    s = {
        "today": date(2026, 10, 26),
        "day": date(2026, 10, 24),
        "son": {"satis": 5, "ciro": 232_000},
        "ay": {"ad": "Ekim", "satis": 57, "ciro": 3_170_000, "hedef": {"satis": 67, "ciro": 3_148_330},
               "tempo_satis": 81.0, "tempo_ciro": 4_500_000, "is_gunu": 19, "toplam_is_gunu": 27},
        "huni": {"gelen_lead": 62, "arama": 212, "ulasilan": 93, "randevu": 8, "katildi": 7, "satis": 3},
        "huni_kiyas": {"gelen_lead": 67, "arama": 157, "ulasilan": 82, "randevu": 12, "katildi": 5, "satis": 2},
        "bugun_toplanti": 10,
        "istisnalar": [],
        "kalite": None,
    }
    text = cs.render(s)
    assert "Satış Operasyonu Yönetici Özeti" in text and "24 Ekim Cumartesi" in text and "(pazar dahil)" in text
    assert "57 / 67 yeni müşteri (%85)" in text and "3,17 M TL / 3,15 M TL" in text
    assert "arama: 212 (↑ ort. 157)" in text and "toplantı planlanan: 8 (↓ ort. 12)" in text
    assert "*Ekip:* normal" in text


def test_cliq_card_sections() -> None:
    s = {
        "today": date(2026, 10, 23), "day": date(2026, 10, 22),
        "son": {"satis": 3, "ciro": 176_000},
        "ay": {"ad": "Ekim", "satis": 57, "ciro": 3_170_000, "hedef": {"satis": 67, "ciro": 3_148_330},
               "tempo_satis": 81.0, "tempo_ciro": 4_500_000, "is_gunu": 19, "toplam_is_gunu": 27},
        "huni": {"gelen_lead": 62, "arama": 212, "ulasilan": None, "randevu": 8, "katildi": 7, "satis": 3},
        "huni_kiyas": {"gelen_lead": 67, "arama": 157, "ulasilan": 82, "randevu": 12, "katildi": 5, "satis": 2},
        "bugun_toplanti": 10, "istisnalar": ["Ayşe Kar izinli"], "kalite": None,
    }
    note, card, slides = cs.cliq_card(s)
    assert note == "3 yeni müşteri · 176 B TL" and card["title"] == "Satış Operasyonu Yönetici Özeti · 22 Ekim Perşembe"
    funnel = next(x for x in slides if x["type"] == "table")
    assert len(funnel["data"]["rows"]) == 5  # ölçülmeyen adım yok
    assert funnel["data"]["rows"][1] == {"Adım": "Arama", "Gün": "212", "4 hafta ort.": "157 ↑"}
    assert {"Ekip": "Ayşe Kar izinli"} in slides[3]["data"]


def test_card_html_colours_and_sections() -> None:
    from pusula import ceo_card

    s = {
        "today": "2026-10-23", "day": "2026-10-22",
        "son": {"satis": 3, "ciro": 176_000},
        "ay": {"ad": "Ekim", "satis": 57, "ciro": 3_170_000, "hedef": {"satis": 67, "ciro": 3_148_330},
               "tempo_satis": 60.0, "tempo_ciro": 4_500_000, "is_gunu": 19, "toplam_is_gunu": 27},
        "huni": {"gelen_lead": 62, "arama": 212, "ulasilan": 93, "randevu": 8, "katildi": 7, "satis": 3},
        "huni_kiyas": {"gelen_lead": 67, "arama": 157, "ulasilan": 82, "randevu": 12, "katildi": 5, "satis": 2},
        "bugun_toplanti": 10, "istisnalar": ["Ayşe <Kar> izinli"], "kalite": None,
    }
    html = ceo_card.render_html(s)
    assert "22 Ekim Perşembe" in html and "176 B TL" in html
    assert "hedefin altında" in html and "hedefin üstünde" in html  # müşteri tempo altında, ciro üstünde
    assert "▼ %33" in html and "▲ %35" in html
    assert "Ayşe &lt;Kar&gt; izinli" in html  # kaçış


def test_workdays_skip_sundays_and_public_holidays() -> None:
    from pusula.panel_ciro import is_workday, workdays_inclusive

    assert not is_workday(date(2026, 10, 29))  # Cumhuriyet Bayramı
    assert is_workday(date(2026, 10, 28))  # arife yarım gün, iş günü
    assert workdays_inclusive(date(2026, 10, 1), date(2026, 10, 31)) == 26  # 31 gün − 4 pazar − 29 Ekim
    assert cs.report_day(date(2026, 10, 30)) == date(2026, 10, 28)  # cuma → (29 tatil) çarşamba
