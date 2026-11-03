from datetime import datetime, timezone

from pusula.ingest.zoho_meeting import internal_rows

REPS = {"beytullah aras": "r-bey", "ayşe kar": "r-ayse"}
NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


def ms(h: int, m: int = 0, day: int = 3) -> int:
    return int(datetime(2026, 11 if day <= 30 else 10, day, h, m, tzinfo=timezone.utc).timestamp() * 1000)


def session(topic: str, h: int, day: int = 3) -> dict:
    return {"topic": topic, "presenterEmail": "yusuf.aras@rexven.com", "startTimeMillisec": ms(h, day=day),
            "duration": 60 * 60000}


def test_katilim_varsa_gercek_giris_cikis_sayilir():
    parts = [
        {"email": "yusuf.aras@rexven.com", "role": "presenter", "joinTime": ms(13, 16), "leaveTime": ms(13, 58)},
        {"email": "Ayşe Kar", "role": "participant", "joinTime": ms(13, 17), "leaveTime": ms(13, 58)},
    ]
    rows = internal_rows(session("Mentörlük Görüşmesi-Ayşe Kar", 13), parts, REPS, NOW)
    assert rows[0]["rep_id"] == "r-ayse" and rows[0]["source"] == "katilim"
    assert (rows[0]["end_at"] - rows[0]["start_at"]).total_seconds() == 41 * 60


def test_rapor_yoksa_planlanan_sure_sayilir():
    rows = internal_rows(session("Mentorluk Görüşmesi - Beytullah Aras", 11), [], REPS, NOW)
    assert rows[0]["rep_id"] == "r-bey" and rows[0]["source"] == "plan"
    assert (rows[0]["end_at"] - rows[0]["start_at"]).total_seconds() == 3600


def test_baska_gunun_raporu_eski_tekrara_yazilmaz():
    parts = [{"email": "Ayşe Kar", "role": "participant", "joinTime": ms(13, 17), "leaveTime": ms(13, 58)}]
    old = session("Mentörlük Görüşmesi-Ayşe Kar", 13, day=6)
    old["startTimeMillisec"] = int(datetime(2026, 10, 6, 13, tzinfo=timezone.utc).timestamp() * 1000)
    rows = internal_rows(old, parts, REPS, NOW)
    assert rows[0]["source"] == "plan" and rows[0]["day"].isoformat() == "2026-10-06"


def test_temsilci_degilse_ya_da_baska_konuysa_yok():
    assert internal_rows(session("Mentörlük Görüşmesi-Mehmet Para", 10), [], REPS, NOW) == []
    assert internal_rows(session("Rexven Bilgilendirme Toplantısı-Ayşe Kar", 10), [], REPS, NOW) == []


def test_gelecekteki_toplanti_sayilmaz():
    assert internal_rows(session("Mentörlük Görüşmesi-Ayşe Kar", 16), [], REPS, NOW) == []
