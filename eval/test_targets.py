"""Günlük hedef ve tempo dürtmesi: veritabanı gerektirmeyen saf kurallar."""

from datetime import date, datetime, timedelta

from pusula.metrics import TZ
from pusula.pace_nudges import (
    compose,
    cooldown_ok,
    format_body,
    gaps_for,
    in_window,
)
from pusula.targets import (
    STANDARD_CALLS,
    attach_targets,
    day_factor,
    elapsed_share,
    freed_windows,
    rep_targets,
)

MONDAY = date(2026, 10, 12)
SATURDAY = date(2026, 10, 10)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def meeting(start: datetime, state: str, planned: float = 30, rep_id: str = "r1") -> dict:
    return {"rep_id": rep_id, "start": start, "planned_min": planned, "state": state}


def test_standard_call_target_is_blocks_minus_break_over_five_minutes() -> None:
    # (09-11 + 14-17 = 300 dk) - 75 dk mola = 225 dk; 5 dk bant → 45.
    assert STANDARD_CALLS == 45


def test_saturday_is_all_meetings() -> None:
    assert day_factor(MONDAY, "arama") == 1.0
    assert day_factor(SATURDAY, "arama") == 0.0
    out = rep_targets(today={}, hist_hours={}, meetings=[], day=SATURDAY, cutoff=at(SATURDAY, 12))
    assert "arama" not in out and "ulasma_orani" not in out


def test_calls_accrue_only_in_call_blocks() -> None:
    assert elapsed_share(at(MONDAY, 10, 0), "arama", MONDAY) == 60 / 300
    # 11-14 toplantı bloğu: arama payı 09-11'de sabit kalır.
    assert elapsed_share(at(MONDAY, 11, 0), "arama", MONDAY) == 120 / 300
    assert elapsed_share(at(MONDAY, 13, 45), "arama", MONDAY) == 120 / 300
    assert elapsed_share(at(MONDAY, 15, 30), "arama", MONDAY) == 210 / 300
    assert elapsed_share(at(MONDAY, 17, 30), "arama", MONDAY) == 1.0


def test_meetings_accrue_only_in_meeting_blocks() -> None:
    assert elapsed_share(at(MONDAY, 10, 30), "randevu", MONDAY) == 0.0
    assert elapsed_share(at(MONDAY, 12, 30), "randevu", MONDAY) == 90 / 240
    assert elapsed_share(at(MONDAY, 16, 0), "randevu", MONDAY) == 180 / 240
    assert elapsed_share(at(SATURDAY, 12, 0), "randevu", SATURDAY) == 0.5
    assert elapsed_share(at(MONDAY + timedelta(days=1), 0), "arama", MONDAY) == 1.0
    assert elapsed_share(at(MONDAY, 10), "doluluk", MONDAY) == 1.0


def test_no_show_frees_slot_after_ten_minute_wait() -> None:
    items = [
        meeting(at(MONDAY, 12), "gelmedi", 30),   # 12:10-12:30 → 20 dk
        meeting(at(MONDAY, 13), "geldi", 30),
        meeting(at(MONDAY, 17), "bilinmiyor", 30),
        meeting(at(MONDAY, 17, 30), "gelmedi", 8),  # beklemeden kısa → yok
    ]
    assert freed_windows(items) == [(at(MONDAY, 12, 10), at(MONDAY, 12, 30))]
    out = rep_targets(today={"arama": 10}, hist_hours={}, meetings=items, day=MONDAY, cutoff=at(MONDAY, 12, 20))
    # 45 + 20/5 = 49; şimdiye: 09-11 payı 18 + 12:10-12:20 → 2.
    assert out["arama"]["daily"] == 49.0
    assert out["arama"]["bonus"] == 4.0
    assert out["arama"]["by_now"] == 20.0
    assert out["ulasilan"]["daily"] == 34.3
    assert out["ulasma_orani"]["daily"] == 70.0


def test_no_show_on_saturday_creates_call_target() -> None:
    items = [meeting(at(SATURDAY, 10), "gelmedi", 40)]
    out = rep_targets(today={}, hist_hours={}, meetings=items, day=SATURDAY, cutoff=at(SATURDAY, 14))
    assert out["arama"]["daily"] == 6.0  # 30 dk / 5
    assert out["arama"]["by_now"] == 6.0


def test_attach_targets_sums_team_and_skips_leave() -> None:
    payload = {
        "reps": [
            {"rep_id": "a", "today": {"arama": 10}, "on_leave": False},
            {"rep_id": "b", "today": {"arama": 4}, "on_leave": False},
            {"rep_id": "c", "today": {"arama": 0}, "on_leave": True},
        ],
        "team": {"today": {"arama": 14, "ulasma_orani": 52.0}},
    }
    items = [meeting(at(MONDAY, 12), "gelmedi", 30, rep_id="b")]
    attach_targets(payload, history={}, meetings=items, day=MONDAY, cutoff=at(MONDAY, 13, 30))
    assert payload["reps"][2]["target"] == {}
    team = payload["team"]["target"]
    # 13:30: arama payı 09-11 (18 kişi başı) + b'nin 4 ek araması.
    assert team["arama"] == {"daily": 94.0, "by_now": 40.0, "source": "standart", "bonus": 4.0, "actual": 14}
    assert team["ulasma_orani"]["daily"] == 70.0
    assert team["ulasma_orani"]["actual"] == 52.0


def card(**target) -> dict:
    return {
        "rep_id": "r1",
        "name": "Ayşe Kar",
        "status": "aktif",
        "today": {"arama": 10, "ulasilan": 4, "doluluk": 50.0, "mesai_dk": 120},
        "target": target,
    }


def test_gaps_apply_80_percent_rule_and_minimum_expectation() -> None:
    behind = card(arama={"daily": 90, "by_now": 20, "source": "standart"})
    assert [g["metric"] for g in gaps_for(behind)] == ["arama"]
    # Ulaşılan mesaja girmez (arama ve ulaşma oranı satırlarının sonucu).
    both = card(arama={"daily": 90, "by_now": 20, "source": "standart"},
                ulasilan={"daily": 60, "by_now": 14, "source": "standart"})
    assert [g["metric"] for g in gaps_for(both)] == ["arama"]
    on_pace = card(arama={"daily": 90, "by_now": 12, "source": "standart"})
    assert gaps_for(on_pace) == []
    # Bu saate kadar 5'ten az arama beklenirken uyarı yok.
    early = card(arama={"daily": 90, "by_now": 4, "source": "standart"})
    early["today"]["arama"] = 0
    assert gaps_for(early) == []


def test_reach_gap_needs_sample_and_ten_points() -> None:
    low = card(ulasma_orani={"daily": 70.0, "by_now": 70.0, "source": "standart"})
    low["today"].update({"ulasma_orani": 55.0, "lead_payda": 20})
    assert [g["metric"] for g in gaps_for(low)] == ["ulasma_orani"]
    low["today"]["lead_payda"] = 6
    assert gaps_for(low) == []
    low["today"].update({"ulasma_orani": 62.0, "lead_payda": 20})
    assert gaps_for(low) == []
    body = format_body("Ayşe Kar", [{"metric": "ulasma_orani", "actual": 55.0, "daily": 70.0, "by_now": 70.0, "source": "standart"}], at(MONDAY, 12))
    assert "- Ulaşma oranı: %55 (günlük hedef %70)" in body


def test_occupancy_gap_needs_ten_points_and_ninety_minutes() -> None:
    low = card(doluluk={"daily": 70.0, "by_now": 70.0, "source": "standart"})
    assert [g["metric"] for g in gaps_for(low)] == ["doluluk"]
    low["today"]["doluluk"] = 61.0
    assert gaps_for(low) == []
    low["today"]["doluluk"] = 40.0
    low["today"]["mesai_dk"] = 60
    assert gaps_for(low) == []


def test_window_and_cooldown() -> None:
    assert not in_window(at(MONDAY, 9, 50))
    assert in_window(at(MONDAY, 10, 0))
    assert not in_window(at(MONDAY, 17, 31))
    assert not in_window(at(SATURDAY, 14, 45))
    assert not in_window(at(MONDAY - timedelta(days=1), 12))  # pazar
    now = at(MONDAY, 13)
    assert cooldown_ok([], now)
    assert not cooldown_ok([at(MONDAY, 11, 30)], now)
    assert cooldown_ok([at(MONDAY, 11, 0)], now)
    assert not cooldown_ok([at(MONDAY, 9), at(MONDAY, 10), at(MONDAY, 10, 30)], now)


def test_message_uses_first_name_and_labels_average_honestly() -> None:
    gaps = gaps_for(card(arama={"daily": 90, "by_now": 20, "source": "ortalama"}))
    body = format_body("Ayşe Kar", gaps, at(MONDAY, 12, 0))
    assert "Ayşe, bugün" in body
    assert "- Arama: 10; bu saate kadar 20 bekleniyordu (günlük ortalaman 90)" in body
    assert "Mesai bitimine 6 sa 0 dk var" in body
    assert compose("x", None) == "x"
    assert compose("x", "Arama bloğuna dön.") == "x\n💡 Arama bloğuna dön."


def test_occupancy_target_is_fixed_80() -> None:
    out = rep_targets(today={"doluluk": 72.0}, hist_hours={}, meetings=[], day=SATURDAY, cutoff=at(SATURDAY, 12))
    assert out["doluluk"] == {"daily": 80.0, "by_now": 80.0, "source": "standart", "actual": 72.0}
    # Dürtme 10 puan altında: %72 uyarı değil, %69 uyarı.
    c = card(doluluk=out["doluluk"])
    c["today"]["doluluk"] = 72.0
    assert gaps_for(c) == []
    c["today"]["doluluk"] = 69.0
    assert [g["metric"] for g in gaps_for(c)] == ["doluluk"]


def test_talk_time_target_five_minutes_with_sample() -> None:
    out = rep_targets(today={"sure_ort": 204.0}, hist_hours={}, meetings=[], day=MONDAY, cutoff=at(MONDAY, 12))
    assert out["sure_ort"]["daily"] == 300.0
    c = card(sure_ort=out["sure_ort"])
    c["today"].update({"sure_ort": 204.0, "giden_temas": 12})
    gaps = gaps_for(c)
    assert [g["metric"] for g in gaps] == ["sure_ort"]
    body = format_body("Ayşe Kar", gaps, at(MONDAY, 12))
    assert "- Ort. görüşme süresi: 3 dk 24 sn (hedef en az 5 dk)" in body
    c["today"]["giden_temas"] = 4  # az görüşmede yorum yok
    assert gaps_for(c) == []
    c["today"].update({"sure_ort": 300.0, "giden_temas": 12})
    assert gaps_for(c) == []
    # Cumartesi (arama yok) süre hedefi de yok.
    sat = rep_targets(today={}, hist_hours={}, meetings=[], day=SATURDAY, cutoff=at(SATURDAY, 12))
    assert "sure_ort" not in sat


def test_every_nudge_carries_occupancy() -> None:
    from pusula.pace_nudges import occupancy_line

    c = card(arama={"daily": 90, "by_now": 20, "source": "standart"},
             doluluk={"daily": 80.0, "by_now": 80.0, "source": "standart"})
    c["today"].update({"doluluk": 74.0, "kayip_dk": 72})
    line = occupancy_line(c)
    assert line == "Doluluğun şu an %74 (hedef %80), kayıp zaman 1 sa 12 dk."
    body = format_body("Ayşe Kar", gaps_for(c), at(MONDAY, 12), occupancy=line)
    assert "- Arama:" in body and line in body
    # Doluluk gerideyse tek satır: oran + kayıp zaman.
    c["today"]["doluluk"] = 60.0
    body = format_body("Ayşe Kar", gaps_for(c), at(MONDAY, 12), occupancy=occupancy_line(c))
    assert "- Doluluk: %60 (günlük hedef %80), kayıp zaman 1 sa 12 dk" in body
    assert "Doluluğun şu an" not in body
    c["today"]["doluluk"] = None
    assert occupancy_line(c) is None
