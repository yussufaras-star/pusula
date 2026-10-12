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
    STANDARD,
    attach_targets,
    day_factor,
    elapsed_share,
    resolve,
    rep_targets,
)

MONDAY = date(2026, 10, 12)
SATURDAY = date(2026, 10, 10)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def test_standard_call_target_is_blocks_minus_break_over_five_minutes() -> None:
    # (09-11 + 14-17 = 300 dk) - 75 dk mola = 225 dk; 5 dk bant → 45.
    assert STANDARD["arama"] == 45
    assert resolve("r1", "arama", {}, {"arama": 30.0}, MONDAY) == (45, "standart")


def test_saturday_is_all_meetings() -> None:
    assert day_factor(MONDAY, "arama") == 1.0
    assert day_factor(SATURDAY, "arama") == 0.0
    assert day_factor(SATURDAY, "randevu") == 1.5  # 6 sa / 4 sa
    stored = {("r1", "arama"): 90.0, ("r1", "randevu"): 4.0, ("r1", "doluluk"): 70.0}
    assert resolve("r1", "arama", stored, {}, SATURDAY) == (None, None)
    assert resolve("r1", "ulasilan", stored, {"ulasilan": 9.0}, SATURDAY) == (None, None)
    assert resolve("r1", "randevu", stored, {}, SATURDAY) == (6.0, "kisi")
    assert resolve("r1", "doluluk", stored, {}, SATURDAY) == (70.0, "kisi")


def test_resolve_order_rep_team_standard_average() -> None:
    stored = {("*", "arama"): 80.0, ("r1", "arama"): 60.0}
    assert resolve("r1", "arama", stored, {}, MONDAY) == (60.0, "kisi")
    assert resolve("r2", "arama", stored, {}, MONDAY) == (80.0, "ekip")
    assert resolve("r1", "ulasilan", stored, {"ulasilan": 12.0}, MONDAY) == (12.0, "ortalama")
    assert resolve("r1", "randevu", stored, {"randevu": None}, MONDAY) == (None, None)


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


def test_rep_targets_paces_by_blocks() -> None:
    out = rep_targets(
        rep_id="r1",
        today={"arama": 3},
        hist_hours={},
        stored={("r1", "arama"): 90.0},
        day=MONDAY,
        cutoff=at(MONDAY, 12, 0),
    )
    assert out["arama"] == {"daily": 90.0, "by_now": 36.0, "source": "kisi", "actual": 3}
    # Geçmiş yok, standart yok → ulaşılan hedefi uydurulmaz.
    assert "ulasilan" not in out


def test_attach_targets_sums_team_and_skips_leave() -> None:
    payload = {
        "reps": [
            {"rep_id": "a", "today": {"arama": 10}, "on_leave": False},
            {"rep_id": "b", "today": {"arama": 4}, "on_leave": False},
            {"rep_id": "c", "today": {"arama": 0}, "on_leave": True},
        ],
        "team": {"today": {"arama": 14}},
    }
    stored = {("*", "arama"): 90.0, ("*", "doluluk"): 70.0}
    attach_targets(payload, history={}, stored=stored, day=MONDAY, cutoff=at(MONDAY, 13, 30))
    assert payload["reps"][2]["target"] == {}
    team = payload["team"]["target"]
    # 13:30: arama payı 09-11 (120/300).
    assert team["arama"] == {"daily": 180.0, "by_now": 72.0, "source": "hedef", "actual": 14}
    assert team["doluluk"]["daily"] == 70.0


def card(**target) -> dict:
    return {
        "rep_id": "r1",
        "name": "Ayşe Kar",
        "status": "aktif",
        "today": {"arama": 10, "ulasilan": 4, "doluluk": 50.0, "mesai_dk": 120},
        "target": target,
    }


def test_gaps_apply_80_percent_rule_and_minimum_expectation() -> None:
    behind = card(arama={"daily": 90, "by_now": 20, "source": "kisi"})
    assert [g["metric"] for g in gaps_for(behind)] == ["arama"]
    on_pace = card(arama={"daily": 90, "by_now": 12, "source": "kisi"})
    assert gaps_for(on_pace) == []
    # Bu saate kadar 5'ten az arama beklenirken uyarı yok.
    early = card(arama={"daily": 90, "by_now": 4, "source": "kisi"})
    early["today"]["arama"] = 0
    assert gaps_for(early) == []


def test_occupancy_gap_needs_ten_points_and_ninety_minutes() -> None:
    low = card(doluluk={"daily": 70.0, "by_now": 70.0, "source": "kisi"})
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
