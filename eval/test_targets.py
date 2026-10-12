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


# Sabah az, öğleden sonra çok arayan biri: 9-12 arası 10, 12-18 arası 60.
HIST = {h: {"arama": 10 / 3} for h in (9, 10, 11)} | {h: {"arama": 10.0} for h in range(12, 18)}


def test_saturday_count_target_shrinks_by_shift_length() -> None:
    assert day_factor(MONDAY) == 1.0
    assert round(day_factor(SATURDAY), 3) == round(6 / 9, 3)
    stored = {("r1", "arama"): 90.0, ("r1", "doluluk"): 70.0}
    assert resolve("r1", "arama", stored, {}, SATURDAY) == (60.0, "kisi")
    # Oran hedefi gün tipine göre değişmez.
    assert resolve("r1", "doluluk", stored, {}, SATURDAY) == (70.0, "kisi")


def test_resolve_falls_back_to_team_default_then_average() -> None:
    stored = {("*", "arama"): 80.0}
    assert resolve("r1", "arama", stored, {"arama": 50.0}, MONDAY) == (80.0, "ekip")
    assert resolve("r1", "ulasilan", stored, {"ulasilan": 12.0}, MONDAY) == (12.0, "ortalama")
    assert resolve("r1", "randevu", stored, {"randevu": None}, MONDAY) == (None, None)


def test_elapsed_share_follows_rep_hourly_curve() -> None:
    # 12:00'de günlük aramaların 10/70'i beklenir, mesainin 3/9'u değil.
    share = elapsed_share(HIST, at(MONDAY, 12, 0), "arama", MONDAY)
    assert round(share, 3) == round(10 / 70, 3)
    # Yarım saat: 12. saatin yarısı eklenir.
    share = elapsed_share(HIST, at(MONDAY, 12, 30), "arama", MONDAY)
    assert round(share, 3) == round(15 / 70, 3)


def test_elapsed_share_linear_without_history_and_full_for_past_day() -> None:
    assert elapsed_share({}, at(MONDAY, 13, 30), "arama", MONDAY) == 0.5
    assert elapsed_share(HIST, at(MONDAY + timedelta(days=1), 0), "arama", MONDAY) == 1.0
    assert elapsed_share(HIST, at(MONDAY, 10), "doluluk", MONDAY) == 1.0


def test_rep_targets_paces_manager_target() -> None:
    out = rep_targets(
        rep_id="r1",
        today={"arama": 3},
        hist_hours=HIST,
        stored={("r1", "arama"): 140.0},
        day=MONDAY,
        cutoff=at(MONDAY, 12, 0),
    )
    assert out["arama"] == {"daily": 140.0, "by_now": 20.0, "source": "kisi", "actual": 3}
    # Geçmiş saatlerde ulaşılan yok → hedef de yok (uydurulmaz).
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
    assert team["arama"] == {"daily": 180.0, "by_now": 90.0, "source": "hedef", "actual": 14}
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
