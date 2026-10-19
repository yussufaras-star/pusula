"""Görüşme Analizleri: kayıt seçimi, transkript biçimi, haftalık sayılar (veritabanısız)."""

from datetime import date, datetime

from pusula import conversations as cv
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


def test_normalize_clamps_scores_and_unknown_type() -> None:
    out = cv.normalize_analysis({"puanlar": {"acilis": 9, "kapanis": -2}, "musteri": {"ilgi": "4"}, "tur": "x"})
    assert out["puanlar"]["acilis"] == 5 and out["puanlar"]["kapanis"] == 0
    assert out["puanlar"]["deger_sunumu"] == 0
    assert out["musteri"]["ilgi"] == 4 and out["tur"] == "diger"


def analysed(kind="call", seconds=600, **a):
    base = {
        "analiz_edilebilir": True,
        "tur": "sunum",
        "puanlar": {"acilis": 4, "ihtiyac_analizi": 2, "deger_sunumu": 4, "itiraz_karsilama": 0, "kapanis": 3},
        "musteri": {"ilgi": 4},
        "itirazlar": [],
        "riskler": [],
        "sonraki_adim": {"var": True, "tarih_net": False},
    }
    base.update(a)
    return {"kind": kind, "duration_sec": seconds, "analysis": base}


def test_aggregate_scores_skip_not_applicable_and_unusable() -> None:
    items = [
        analysed(),
        analysed(
            kind="meeting",
            seconds=1800,
            puanlar={"acilis": 2, "ihtiyac_analizi": 4, "deger_sunumu": 0, "itiraz_karsilama": 3, "kapanis": 1},
            itirazlar=[{"tip": "fiyat", "karsilandi": "evet"}, {"tip": "fiyat", "karsilandi": "kismen"},
                       {"tip": "zaman", "karsilandi": "hayir"}],
            sonraki_adim={"var": False, "tarih_net": False},
            riskler=[{"tip": "baski"}],
        ),
        {"kind": "call", "duration_sec": 90, "analysis": {"analiz_edilebilir": False}},
    ]
    s = cv.aggregate(items)
    assert s["gorusme"] == 3 and s["analiz"] == 2
    assert s["telefon"] == 1 and s["toplanti"] == 1
    assert s["dakika"] == 40.0
    assert s["puanlar"]["acilis"] == 3.0
    assert s["puanlar"]["deger_sunumu"] == 4.0  # 0 = uygulanmaz, ortalamaya girmez
    assert s["puanlar"]["itiraz_karsilama"] == 3.0
    assert s["sonraki_adim_orani"] == 50.0 and s["tarihli_adim_orani"] == 0.0
    assert s["itirazlar"]["fiyat"] == {"adet": 2, "karsilandi": 1, "kismen": 1, "oran": 75.0}
    assert list(s["itirazlar"]) == ["fiyat", "zaman"]
    assert s["riskler"] == {"baski": 1}


def test_aggregate_empty_week() -> None:
    s = cv.aggregate([])
    assert s["analiz"] == 0 and s["puan_ort"] is None and s["sonraki_adim_orani"] is None
