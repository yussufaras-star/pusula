"""Günlük hedefler: yöneticinin girdiği değer, yoksa standart, yoksa geçmiş ortalama.

Hedef temsilciye özel veya ekip varsayılanı ('*') olabilir. Sayım hedefleri
hafta içi gün içindir ve blok takvimine bağlıdır (pusula.blocks): arama ve
ulaşılan arama bloklarında (09-11, 14-17), toplantı toplantı bloklarında
(11-14, 17-18; cumartesi 09-15 tamamı) birikir. Cumartesi arama bloğu
olmadığından arama/ulaşılan hedefi yoktur; toplantı hedefi blok süresi
oranında (6/4) büyür.

Standart arama hedefi: arama blokları (300 dk) eksi günlük mola (75 dk),
görüşme başına 5 dk bant → 45. Tablo ilk yazmada oluşur; yoksa okuma boş döner.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from pusula.blocks import PLANNED_BLOCKS, blocks_for
from pusula.config import get_org_id
from pusula.metrics import TZ, combine, finish
from pusula.panel_data import MOLA_SAAT, connect

METRICS: dict[str, str] = {
    "arama": "Arama",
    "ulasilan": "Ulaşılan",
    "randevu": "Toplantı",
    "doluluk": "Doluluk",
}
COUNT_METRICS = ("arama", "ulasilan", "randevu")
METRIC_KIND = {"arama": "call", "ulasilan": "call", "randevu": "meeting"}
TEAM_DEFAULT = "*"
CALL_BAND_MIN = 5.0


def _weekday_minutes(kind: str) -> float:
    return sum((b.end_hour - b.start_hour) * 60.0 for b in PLANNED_BLOCKS if b.kind == kind)


# (300 - 75) / 5 = 45 arama.
STANDARD: dict[str, float] = {
    "arama": round((_weekday_minutes("call") - MOLA_SAAT * 60) / CALL_BAND_MIN),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_targets (
    org_id      text NOT NULL DEFAULT 'rexven',
    rep_id      text NOT NULL,
    metric      text NOT NULL,
    value       double precision NOT NULL,
    updated_by  text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, rep_id, metric)
);
"""

Stored = dict[tuple[str, str], float]


def load_targets() -> Stored:
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('daily_targets')").fetchone()
        if not exists or exists[0] is None:
            return {}
        rows = conn.execute(
            "SELECT rep_id, metric, value FROM daily_targets WHERE org_id = %s",
            (get_org_id(),),
        ).fetchall()
    return {(str(rep_id), str(metric)): float(value) for rep_id, metric, value in rows}


def save_targets(rows: Iterable[tuple[str, str, float | None]], *, by: str) -> None:
    """(rep_id, metric, değer). Değer None ise hedef silinir (ortalamaya döner)."""
    org_id = get_org_id()
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        for rep_id, metric, value in rows:
            if value is None:
                conn.execute(
                    "DELETE FROM daily_targets WHERE org_id = %s AND rep_id = %s AND metric = %s",
                    (org_id, rep_id, metric),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO daily_targets (org_id, rep_id, metric, value, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (org_id, rep_id, metric)
                    DO UPDATE SET value = EXCLUDED.value,
                                  updated_by = EXCLUDED.updated_by,
                                  updated_at = now()
                    """,
                    (org_id, rep_id, metric, value, by),
                )


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


def resolve(
    rep_id: str,
    metric: str,
    stored: Stored,
    hist_daily: dict[str, float | None],
    day: date,
) -> tuple[float | None, str | None]:
    """(günlük hedef, kaynak). Kaynak: kisi | ekip | standart | ortalama."""
    factor = day_factor(day, metric)
    if factor == 0:
        return None, None
    for key, source in ((rep_id, "kisi"), (TEAM_DEFAULT, "ekip")):
        value = stored.get((key, metric))
        if value is not None:
            return value * factor, source
    if metric in STANDARD:
        return STANDARD[metric] * factor, "standart"
    avg = hist_daily.get(metric)
    if avg:
        return float(avg), "ortalama"
    return None, None


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


def rep_targets(
    *,
    rep_id: str,
    today: dict[str, Any],
    hist_hours: dict[int, dict[str, Any]],
    stored: Stored,
    day: date,
    cutoff: datetime,
) -> dict[str, dict[str, Any]]:
    hist_daily = history_daily(hist_hours)
    out: dict[str, dict[str, Any]] = {}
    for metric in METRICS:
        daily, source = resolve(rep_id, metric, stored, hist_daily, day)
        if daily is None:
            continue
        out[metric] = {
            "daily": round(daily, 1),
            "by_now": round(daily * elapsed_share(cutoff, metric, day), 1),
            "source": source,
            "actual": today.get(metric),
        }
    return out


def attach_targets(
    payload: dict[str, Any],
    *,
    history: dict[str, dict[int, dict[str, Any]]],
    stored: Stored,
    day: date,
    cutoff: datetime,
) -> dict[str, Any]:
    """build_live yüküne kart ve ekip hedeflerini ekler (yerinde)."""
    team: dict[str, dict[str, Any]] = {}
    doluluk: list[float] = []
    for card in payload["reps"]:
        if card.get("on_leave"):
            card["target"] = {}
            continue
        targets = rep_targets(
            rep_id=card["rep_id"],
            today=card["today"],
            hist_hours=history.get(card["rep_id"], {}),
            stored=stored,
            day=day,
            cutoff=cutoff,
        )
        card["target"] = targets
        for metric, item in targets.items():
            if metric == "doluluk":
                doluluk.append(item["daily"])
                continue
            slot = team.setdefault(metric, {"daily": 0.0, "by_now": 0.0, "source": "ortalama"})
            slot["daily"] = round(slot["daily"] + item["daily"], 1)
            slot["by_now"] = round(slot["by_now"] + item["by_now"], 1)
            if item["source"] != "ortalama":
                slot["source"] = "hedef"
    if doluluk:
        team["doluluk"] = {
            "daily": round(sum(doluluk) / len(doluluk), 1),
            "by_now": round(sum(doluluk) / len(doluluk), 1),
            "source": "hedef" if any(m == "doluluk" for _, m in stored) else "ortalama",
        }
    team_today = payload["team"]["today"]
    for metric, item in team.items():
        item["actual"] = team_today.get(metric)
    payload["team"]["target"] = team
    return payload
