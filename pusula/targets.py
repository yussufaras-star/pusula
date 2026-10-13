"""Günlük hedefler: elle girilmez, kurallardan ve geçmiş ortalamadan gelir.

Kurallar (pusula.blocks takvimi; hafta içi arama 09-11 + 14-17, toplantı
11-14 + 17-18; cumartesi tamamı toplantı):

- Arama: arama blokları (300 dk) eksi günlük mola (75 dk), görüşme başına
  5 dk bant → 45. Yalnız arama bloklarında birikir; cumartesi yoktur.
- Gelmeyen toplantı: 10 dk bekleme sonrası planlanan bitişe kadarki süre
  arama süresi sayılır ve hedefe eklenir (5 dk'da bir arama), o pencerede
  birikir. Cumartesi de geçerlidir.
- Ulaşma oranı: %70. Ulaşılan sayısı hedefi arama hedefinin %70'idir.
- Ortalama görüşme süresi: en az 5 dk (ulaşılan giden görüşmeler; arama
  günlerinde).
- Doluluk: %80 (yönetici kararı, 12 Ekim; ölçüm olgunlaşınca %90).
- Toplantı: aynı gün tipinin geçmiş ortalaması; toplantı bloklarında birikir.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from pusula.blocks import PLANNED_BLOCKS, blocks_for
from pusula.metrics import TZ, combine, finish
from pusula.panel_data import MOLA_SAAT

METRICS: dict[str, str] = {
    "arama": "Arama",
    "ulasilan": "Ulaşılan",
    "ulasma_orani": "Ulaşma oranı",
    "randevu": "Toplantı",
    "sure_ort": "Ort. görüşme süresi",
    "doluluk": "Doluluk",
}
COUNT_METRICS = ("arama", "ulasilan", "randevu")
RATE_METRICS = ("ulasma_orani", "doluluk")
# Ekipte toplanmaz, temsilci hedeflerinin ortalaması alınır.
MEAN_METRICS = (*RATE_METRICS, "sure_ort")
METRIC_KIND = {"arama": "call", "ulasilan": "call", "randevu": "meeting"}
CALL_BAND_MIN = 5.0
NO_SHOW_WAIT_MIN = 10.0
REACH_TARGET = 70.0
OCCUPANCY_TARGET = 80.0
TALK_TARGET_SEC = 300.0


def _weekday_minutes(kind: str) -> float:
    return sum((b.end_hour - b.start_hour) * 60.0 for b in PLANNED_BLOCKS if b.kind == kind)


# (300 - 75) / 5 = 45 arama.
STANDARD_CALLS = round((_weekday_minutes("call") - MOLA_SAAT * 60) / CALL_BAND_MIN)


def history_daily(hist_hours: dict[int, dict[str, Any]]) -> dict[str, float | None]:
    """Aynı gün tipinin günlük ortalaması (history_by_rep_hour saatleri toplamı)."""
    if not hist_hours:
        return {metric: None for metric in METRICS}
    row = finish(combine(hist_hours.values()))
    return {metric: row.get(metric) for metric in METRICS}


def block_hours(day: date, kind: str) -> list[int]:
    return [
        hour
        for block in blocks_for(day)
        if block.kind == kind
        for hour in range(block.start_hour, block.end_hour)
    ]


def day_factor(day: date, metric: str) -> float:
    """Hafta içi hedefin bu güne oranı: ilgili blok süresi / hafta içi blok süresi."""
    if metric not in COUNT_METRICS:
        return 1.0
    kind = METRIC_KIND[metric]
    return len(block_hours(day, kind)) * 60.0 / _weekday_minutes(kind)


def elapsed_share(cutoff: datetime, metric: str, day: date) -> float:
    """Günlük hedefin bu ana kadar birikmesi beklenen payı (0..1).

    Sayım hedefi yalnız kendi bloklarında birikir: 12:30'da arama payı
    09-11'in tamamıdır (2/5), toplantı bloğunda artmaz.
    """
    if metric not in COUNT_METRICS:
        return 1.0
    hours = block_hours(day, METRIC_KIND[metric])
    if not hours:
        return 0.0
    local = cutoff.astimezone(TZ)
    if local.date() != day:
        return 1.0 if local.date() > day else 0.0
    done = 0.0
    for hour in hours:
        if hour < local.hour:
            done += 60.0
        elif hour == local.hour:
            done += local.minute
    return done / (len(hours) * 60.0)


def freed_windows(meetings: Iterable[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Gelmeyen toplantılarda 10 dk beklemeden planlanan bitişe kadarki süre."""
    out = []
    for item in meetings:
        if item.get("state") != "gelmedi":
            continue
        start = item["start"]
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        begin = start + timedelta(minutes=NO_SHOW_WAIT_MIN)
        end = start + timedelta(minutes=float(item.get("planned_min") or 0))
        if end > begin:
            out.append((begin, end))
    return out


def _minutes(windows: list[tuple[datetime, datetime]], cutoff: datetime | None = None) -> float:
    total = 0.0
    for begin, end in windows:
        stop = end if cutoff is None else min(end, cutoff)
        total += max(0.0, (stop - begin).total_seconds() / 60.0)
    return total


def rep_targets(
    *,
    today: dict[str, Any],
    hist_hours: dict[int, dict[str, Any]],
    meetings: Iterable[dict[str, Any]],
    day: date,
    cutoff: datetime,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    windows = freed_windows(meetings)
    bonus = _minutes(windows) / CALL_BAND_MIN
    bonus_now = _minutes(windows, cutoff) / CALL_BAND_MIN
    base = STANDARD_CALLS * day_factor(day, "arama")
    if base or bonus:
        calls = {
            "daily": round(base + bonus, 1),
            "by_now": round(base * elapsed_share(cutoff, "arama", day) + bonus_now, 1),
            "source": "standart",
            "bonus": round(bonus, 1),
            "actual": today.get("arama"),
        }
        out["arama"] = calls
        out["ulasilan"] = {
            "daily": round(calls["daily"] * REACH_TARGET / 100, 1),
            "by_now": round(calls["by_now"] * REACH_TARGET / 100, 1),
            "source": "standart",
            "actual": today.get("ulasilan"),
        }
        out["ulasma_orani"] = {
            "daily": REACH_TARGET,
            "by_now": REACH_TARGET,
            "source": "standart",
            "actual": today.get("ulasma_orani"),
        }
        out["sure_ort"] = {
            "daily": TALK_TARGET_SEC,
            "by_now": TALK_TARGET_SEC,
            "source": "standart",
            "actual": today.get("sure_ort"),
        }
    hist = history_daily(hist_hours)
    if hist.get("randevu"):
        daily = float(hist["randevu"])
        out["randevu"] = {
            "daily": round(daily, 1),
            "by_now": round(daily * elapsed_share(cutoff, "randevu", day), 1),
            "source": "ortalama",
            "actual": today.get("randevu"),
        }
    out["doluluk"] = {
        "daily": OCCUPANCY_TARGET,
        "by_now": OCCUPANCY_TARGET,
        "source": "standart",
        "actual": today.get("doluluk"),
    }
    return out


def attach_targets(
    payload: dict[str, Any],
    *,
    history: dict[str, dict[int, dict[str, Any]]],
    meetings: list[dict[str, Any]],
    day: date,
    cutoff: datetime,
) -> dict[str, Any]:
    """build_live yüküne kart ve ekip hedeflerini ekler (yerinde)."""
    team: dict[str, dict[str, Any]] = {}
    rates: dict[str, list[float]] = {}
    for card in payload["reps"]:
        if card.get("on_leave"):
            card["target"] = {}
            continue
        targets = rep_targets(
            today=card["today"],
            hist_hours=history.get(card["rep_id"], {}),
            meetings=[m for m in meetings if m.get("rep_id") == card["rep_id"]],
            day=day,
            cutoff=cutoff,
        )
        card["target"] = targets
        for metric, item in targets.items():
            if metric in MEAN_METRICS:
                rates.setdefault(metric, []).append(item["daily"])
                continue
            slot = team.setdefault(
                metric, {"daily": 0.0, "by_now": 0.0, "source": item["source"]}
            )
            slot["daily"] = round(slot["daily"] + item["daily"], 1)
            slot["by_now"] = round(slot["by_now"] + item["by_now"], 1)
            if "bonus" in item:
                slot["bonus"] = round(slot.get("bonus", 0.0) + item["bonus"], 1)
    for metric, values in rates.items():
        mean = round(sum(values) / len(values), 1)
        team[metric] = {
            "daily": mean,
            "by_now": mean,
            "source": "standart",
        }
    team_today = payload["team"]["today"]
    for metric, item in team.items():
        item["actual"] = team_today.get(metric)
    payload["team"]["target"] = team
    return payload
