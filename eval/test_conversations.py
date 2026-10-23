"""Görüşme Analizleri: kayıt seçimi, transkript biçimi, haftalık sayılar (veritabanısız)."""

from collections import Counter
from datetime import date, datetime

from pusula import conversations as cv
from pusula import sonar41
from pusula.metrics import TZ
from pusula.recordings import call_uuid
from pusula.transcribe import _elevenlabs_turns, speaker_lines

UUID = "ba6693bf-e3ad-4682-a6d5-72aae688785d"
T0 = datetime(2026, 10, 14, 15, 0, tzinfo=TZ)


def event(**meta):
    base = {
        "voice_recording_url": f"https://phonebridge.zoho.com/phonebridge/recording/{UUID}?serviceID=892038563",
        "duration_sec": 2053,
        "what_id": {"id": "L1", "name": "İrfan Esen"},
        "call_result": "Sunum Yapıldı",
    }
    base.update(meta)
    return {"id": 7, "rep_id": "r1", "thread_id": "t1", "direction": "outbound", "occurred_at": T0, "meta": base}


def test_verimor_uuid_from_crm_recording_url() -> None:
    assert call_uuid(event()["meta"]["voice_recording_url"]) == UUID
    assert call_uuid(None) is None
    assert call_uuid("https://example.com/kayit.mp3") is None


def test_call_row_links_rep_lead_and_outcome() -> None:
    row = cv.call_row(event())
    assert row is not None
    assert row["source_ref"] == UUID and row["kind"] == "call"
    assert row["rep_id"] == "r1" and row["lead_id"] == "L1" and row["customer"] == "İrfan Esen"
    assert row["crm_outcome"] == "Sunum Yapıldı" and row["duration_sec"] == 2053


def test_short_or_unrecorded_calls_are_skipped() -> None:
    assert cv.call_row(event(duration_sec=cv.MIN_CALL_SEC - 1)) is None
    assert cv.call_row(event(voice_recording_url=None)) is None


def test_week_bounds_are_istanbul_monday_to_monday() -> None:
    assert cv.week_start_of(date(2026, 10, 18)) == date(2026, 10, 12)  # pazar → o haftanın pazartesisi
    assert cv.week_start_of(date(2026, 10, 19)) == date(2026, 10, 19)
    start, end = cv.week_bounds(date(2026, 10, 12))
    assert start == datetime(2026, 10, 12, 0, 0, tzinfo=TZ)
    assert (end - start).days == 7


def test_speaker_lines_merges_runs_and_renumbers() -> None:
    text = speaker_lines([("spk_7", "Merhaba"), ("spk_7", "nasılsınız?"), (0, "İyiyim"), ("spk_7", " ")])
    assert text == "Konuşmacı 1: Merhaba nasılsınız?\nKonuşmacı 2: İyiyim"


def test_elevenlabs_words_become_turns() -> None:
    body = {
        "words": [
            {"text": "Merhaba", "type": "word", "speaker_id": "a"},
            {"text": " ", "type": "spacing", "speaker_id": "a"},
            {"text": "efendim", "type": "word", "speaker_id": "a"},
            {"text": "(gülme)", "type": "audio_event", "speaker_id": "b"},
            {"text": "Buyurun", "type": "word", "speaker_id": "b"},
        ]
    }
    assert _elevenlabs_turns(body) == [("a", "Merhaba efendim"), ("b", "Buyurun")]


def test_word_count_ignores_speaker_labels() -> None:
    assert cv.word_count("Konuşmacı 1: bir iki üç\nKonuşmacı 2: dört") == 4


def test_normalize_clamps_sonar_drops_unknown_and_zero_crm_without_note() -> None:
    out = cv.normalize_analysis({
        "sonar": [
            {"kod": "B29", "puan": 9, "alinti": "a"},
            {"kod": "B29", "puan": 1, "alinti": "tekrar"},  # ilk kayıt geçerli
            {"kod": "B99", "puan": 3, "alinti": "yok"},
            {"kod": "B01", "puan": 0, "alinti": "uygulanmaz"},
            {"kod": "B03", "puan": 2, "alinti": "c", "dogrusu": "Formu neden doldurdunuz?"},
        ],
        "crm": {"not_var": False, "puanlar": {"C1": 4}},
        "musteri": {"ilgi": "4"},
        "tur": "x",
    })
    assert out["sonar"] == [
        {"kod": "B03", "puan": 2, "alinti": "c", "dogrusu": "Formu neden doldurdunuz?"},
        {"kod": "B29", "puan": 5, "alinti": "a", "dogrusu": ""},
    ]
    assert out["crm"]["puanlar"] == {k: 0 for k in sonar41.CRM_KEYS}
    assert out["musteri"]["ilgi"] == 4 and out["tur"] == "diger"


def test_dim_scores_average_behaviors_per_dimension() -> None:
    a = {"sonar": [{"kod": "B01", "puan": 4}, {"kod": "B03", "puan": 2}, {"kod": "B29", "puan": 1}]}
    assert cv.dim_scores(a) == {"K1": 3.0, "K4": 1.0}


def test_sonar_framework_is_complete() -> None:
    assert len(sonar41.CODES) == 41 and sonar41.DIM_KEYS == ("K1", "K2", "K3", "K4", "K5")
    assert {b["dim"] for b in sonar41.BEHAVIORS.values()} == set(sonar41.DIM_KEYS)
    assert sonar41.CRM_KEYS == ("C1", "C2", "C3", "C4", "C5", "C6")
    assert "B29 Net sonraki adım önerme" in sonar41.rubric_text()


def test_note_row_cleans_html_and_links_lead_and_author() -> None:
    row = cv.note_row({
        "id": "n1",
        "Note_Title": None,
        "Note_Content": "trendyol<br>50800 dedim.&nbsp;indirim yok<br><span><br></span>",
        "Parent_Id": {"module": {"api_name": "Leads", "id": "6847"}, "id": "L9"},
        "Created_By": {"name": "Aras", "id": "r7"},
        "Created_Time": "2026-10-19T14:05:00+03:00",
    })
    assert row["parent_module"] == "Leads" and row["parent_id"] == "L9" and row["rep_id"] == "r7"
    assert row["content"] == "trendyol / 50800 dedim.\xa0indirim yok"
    assert row["created_at"] == datetime(2026, 10, 19, 14, 5, tzinfo=TZ)


def test_prompt_includes_this_call_note_and_history() -> None:
    item = {"kind": "call", "direction": "outbound", "occurred_at": T0, "duration_sec": 300,
            "rep_name": "Serkan", "customer": "Ece", "transcript": "Konuşmacı 1: Merhaba"}
    text = cv._prompt(item, {"bu": ["14.10 15:10 Şahin: yarın 14'te ara"], "onceki": []})
    assert "Temsilcinin bu görüşme için CRM notu:\n14.10 15:10 Şahin: yarın 14'te ara" in text
    assert "(önceki not yok)" in text


def analysed(kind="call", seconds=600, sonar=None, crm=None, **a):
    base = {
        "analiz_edilebilir": True,
        "tur": "sunum",
        "sonar": sonar if sonar is not None else [
            {"kod": "B01", "puan": 4}, {"kod": "B05", "puan": 2}, {"kod": "B29", "puan": 3},
        ],
        "crm": crm or {"not_var": False, "puanlar": {}, "yorum": ""},
        "musteri": {"ilgi": 4},
        "itirazlar": [],
        "riskler": [],
        "sonraki_adim": {"var": True, "tarih_net": False},
    }
    base.update(a)
    return {"kind": kind, "duration_sec": seconds, "analysis": base}


def test_aggregate_dimensions_behaviors_crm_and_objections() -> None:
    items = [
        analysed(),
        analysed(
            kind="meeting",
            seconds=1800,
            sonar=[{"kod": "B01", "puan": 2}, {"kod": "B20", "puan": 3}, {"kod": "B29", "puan": 1}],
            crm={"not_var": True, "puanlar": {"C1": 4, "C2": 2, "C3": 0}, "yorum": "x"},
            itirazlar=[{"tip": "fiyat", "karsilandi": "evet"}, {"tip": "fiyat", "karsilandi": "kismen"},
                       {"tip": "zaman", "karsilandi": "hayir"}],
            sonraki_adim={"var": False, "tarih_net": False},
            riskler=[{"tip": "baski"}],
        ),
        {"kind": "call", "duration_sec": 90, "analysis": {"analiz_edilebilir": False}},
    ]
    s = cv.aggregate(items)
    assert s["gorusme"] == 3 and s["analiz"] == 2
    assert s["telefon"] == 1 and s["toplanti"] == 1 and s["dakika"] == 40.0
    # K1: görüşme 1 = (4+2)/2 = 3, görüşme 2 = 2 → 2,5; K3 yalnız ikinci görüşmede.
    assert s["puanlar"] == {"K1": 2.5, "K2": None, "K3": 3.0, "K4": 2.0, "K5": None}
    assert s["davranislar"]["B01"] == {"ort": 3.0, "adet": 2, "zayif": 1}
    assert s["davranislar"]["B29"] == {"ort": 2.0, "adet": 2, "zayif": 1}
    assert s["crm"]["notlu"] == 1 and s["crm"]["oran"] == 50.0
    assert s["crm"]["puanlar"]["C1"] == 4.0 and s["crm"]["puanlar"]["C3"] is None
    assert s["sonraki_adim_orani"] == 50.0 and s["tarihli_adim_orani"] == 0.0
    assert s["itirazlar"]["fiyat"] == {"adet": 2, "karsilandi": 1, "kismen": 1, "oran": 75.0}
    assert list(s["itirazlar"]) == ["fiyat", "zaman"]
    assert s["riskler"] == {"baski": 1}
    assert [w["kod"] for w in cv.weakest(s)] == ["B29", "B01"]  # tek gözlemli B05/B20 sayılmaz


def test_score_spread_and_bug_rows() -> None:
    items = [
        {"id": 1, "rep_id": "r1", "analysis": {"analiz_edilebilir": True, "sonar": [
            {"kod": "B01", "puan": 5, "alinti": "a"},
            {"kod": "B03", "puan": 1, "alinti": "b", "dogrusu": "c"},
            {"kod": "B29", "puan": 2, "alinti": "d"},
            {"kod": "B39", "puan": 1, "alinti": "İyi günler."},
        ], "crm": {"not_var": False}}},
        {"id": 2, "rep_id": "r2", "analysis": {"analiz_edilebilir": False, "sonar": [{"kod": "B01", "puan": 1}]}},
    ]
    spread = cv.score_spread(items)
    assert spread["K1"] == [1, 0, 0, 0, 1] and spread["K4"] == [0, 1, 0, 0, 0] and spread["K2"] == [0] * 5
    bugs = cv.bug_rows(items)
    assert [(b["kod"], b["puan"], b["dogrusu"], b["item_id"]) for b in bugs][:2] == [("B03", 1, "c", 1), ("B29", 2, "", 1)]
    # B39'da alıntı (son cümle) değil not durumu gösterilir.
    assert bugs[2]["alinti"] == "" and bugs[2]["ihlal"] == "Görüşmeden sonra CRM notu yazılmadı."


def test_example_calls_need_enough_strong_moments_and_sort_by_score() -> None:
    def item(i, scores):
        codes = ["B29", "B01", "B03", "B10", "B11", "B20", "B30", "B34", "B05"]
        return {"id": i, "rep_id": "r", "analysis": {"analiz_edilebilir": True, "sonar": [
            {"kod": k, "puan": p, "alinti": k} for k, p in zip(codes, scores)]}}
    good = item(1, [5, 4, 4, 3, 3, 3, 3, 3])
    better = item(2, [5, 5, 4, 4, 3, 3, 3, 3])
    few = item(3, [5, 5, 5, 5])  # 8'den az davranış
    weak = item(4, [4, 4, 3, 3, 3, 3, 3, 3])  # 3'ten az güçlü an
    out = cv.example_calls([good, better, few, weak])
    assert [e["item_id"] for e in out] == [2, 1]
    # Güçlü anlar görüşme akışı sırasıyla (B01 → B29).
    assert [m["kod"] for m in out[1]["anlar"]] == ["B01", "B03", "B29"]


def _q(i, rep, day, sec, *, kind="call", status="atlandi", kota="aday", outcome=None, talep=False):
    return {"id": i, "rep_id": rep, "kind": kind, "occurred_at": datetime(2026, 10, 19 + day, 12, tzinfo=TZ),
            "duration_sec": sec, "status": status, "kota": kota, "talep": talep, "outcome": outcome,
            "call_status": "connected", "source_ref": f"s{i}"}


def test_plan_quota_paces_long_calls_and_takes_one_short() -> None:
    rows = [_q(i, "r1", i % 3, 300) for i in range(1, 31)] + [_q(100, "r1", 0, 45), _q(101, "r1", 1, 50, outcome="callback")]
    plan = cv.plan_quota(rows, elapsed_days=3, closed=False)
    # 3 iş günü geçti: 14 * 3/6 = 7 uzun + 1 kısa (geri arama önce); kalanlar sırada.
    assert len(plan["secilen"]) == 8 and 101 in plan["secilen"] and 100 not in plan["secilen"]
    assert len(plan["sirada"]) == len(rows) - 8 and not plan["disi"]
    # Günlere yayılır: 3 günün her birinden en az 2.
    days = Counter(r["occurred_at"].day for r in rows if r["id"] in plan["secilen"] and r["duration_sec"] >= 120)
    assert min(days.values()) >= 2


def test_plan_quota_counts_done_items_prioritises_meetings_and_closes_week() -> None:
    done = [_q(i, "r1", 0, 300, status="analiz_edildi", kota=None) for i in range(1, 13)]
    pool = [_q(20, "r1", 1, 300), _q(21, "r1", 1, 300), _q(22, "r1", 2, 1800, kind="meeting"),
            _q(23, "r1", 2, 300, talep=True)]
    plan = cv.plan_quota(done + pool, elapsed_days=7, closed=True)
    assert 22 in plan["secilen"] and len(plan["secilen"]) == 2  # 14 - 12 = 2; toplantı önce
    assert plan["disi"] == [r for r in (20, 21) if r not in plan["secilen"]]
    assert 23 not in plan["secilen"] + plan["disi"]  # talep kotaya girmez


def test_cost_usd_batch_halves_and_cache_pricing() -> None:
    from pusula import llm
    tokens = {"input": 1_000_000, "output": 100_000, "cache_read": 1_000_000, "cache_write": 0}
    assert llm.cost_usd("claude-opus-5-5", tokens) == 4.0 + 2.0 + 0.2
    assert llm.cost_usd("claude-opus-5-5", tokens, batch=True) == 3.1
    assert llm.cost_usd("bilinmeyen", tokens) is None


def test_normalize_musaitlik() -> None:
    out = cv.normalize_analysis({"musaitlik": {"musait_degil": False, "tutma": "tuttu"}})
    assert out["musaitlik"]["tutma"] == "uygulanmaz"
    s = cv.aggregate([{"kind": "call", "duration_sec": 50, "analysis": {
        "analiz_edilebilir": True, "musaitlik": {"musait_degil": True, "tutma": "birakti", "geri_arama_net": False}}}])
    assert s["musaitlik"] == {"adet": 1, "tuttu": 0, "kismen": 0, "birakti": 1, "net_geri_arama": 0}


def test_aggregate_empty_week() -> None:
    s = cv.aggregate([])
    assert s["analiz"] == 0 and s["puan_ort"] is None and s["sonraki_adim_orani"] is None
    assert s["davranislar"] == {} and s["crm"]["oran"] is None


def test_share_text_has_focus_code_quote_scores_and_link() -> None:
    stats = cv.aggregate([analysed(), analysed()])
    narrative = {
        "ozet": "İki görüşme.",
        "odak": {"kod": "B29", "baslik": "Tarihli adım", "neden": "Takip yok.", "alinti": "nasıl isterseniz",
                 "nasil": "Saat sor."},
        "guclu": ["Açılış iyi."],
        "dikkat": [],
    }
    text = cv.share_text("Serkan Şahin", date(2026, 10, 19), stats, narrative)
    assert text.startswith("*Görüşme değerlendirmen · 19.10 – 25.10.2026*")
    assert "Merhaba Serkan, bu hafta analiz edilen 2 görüşmenin" in text
    assert "*Bu haftanın odağı: B29 · Tarihli adım*" in text and "> “nasıl isterseniz”" in text
    assert "• Açılış iyi." in text and "*Dikkat*" not in text
    assert "Karşılama ve Teşhis 3,0" in text and "İtiraz ve Müzakere" not in text
    assert "CRM notu yazılan görüşme: %0,0" in text
    assert "\n\n\n" not in text
