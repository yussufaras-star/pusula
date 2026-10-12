"""Zaman çizelgesi doluluğu: dolu, mola ve kayıp dakika.

Temsilcinin mesaisi (hafta içi 09-18, cumartesi 09-15) gerçek zaman
damgalarından bir şeride dökülür:

- Arama: başlangıçtan 1 dk önce (BCW) → bitişten 1 dk sonra (ACW).
  Giden her deneme ve cevaplanan gelen arama. Planlanan / ileri tarihli yok.
- Toplantı: katılınan (veya şu an süren) randevunun süresi (Bookings
  süresi; Zoho Meeting gerçek süresi geldiğinde o).
- WhatsApp: giden mesaj başına gönderimden önceki 1 dk.

Çakışan aralıklar birleşir (aynı dakika iki kez sayılmaz). Mesai içindeki
boşlukların en uzunlarından toplam 75 dk mola sayılır, kalan boşluk kayıp
zamandır. Doluluk = dolu / (dolu + kayıp).

Hiç faaliyeti olmayan temsilci-günü (izin, rapor) ölçüme girmez; yalnız
süren gün (bugün) sayılır, çünkü başlamamış olmak da kayıptır.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.metrics import TZ, Group, Span, shift_bounds
from pusula.panel_data import MOLA_SAAT, _meeting_duration_min_sql, connect
from pusula.temas import (
    duration_sec,
    is_answered_inbound_sql,
    is_not_future_sql,
    is_not_planned_sql,
)

BCW_SEC = 60
ACW_SEC = 60
WP_SEC_PER_MESAJ = 60
MOLA_DK = MOLA_SAAT * 60

Interval = tuple[datetime, datetime]


@dataclass
class Segment:
    start: datetime
    end: datetime
    kind: str  # "dolu" | "mola" | "kayip"

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclass
class DayTimeline:
    day: date
    segments: list[Segment] = field(default_factory=list)

    def total(self, kind: str) -> float:
        return sum(s.minutes for s in self.segments if s.kind == kind)

    def by_hour(self, kind: str) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for seg in self.segments:
            if seg.kind != kind:
                continue
            cursor = seg.start
            while cursor < seg.end:
                edge = min(seg.end, cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                out[cursor.hour] += (edge - cursor).total_seconds() / 60.0
                cursor = edge
        return dict(out)


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    out: list[list[datetime]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


def day_timeline(
    intervals: Iterable[Interval], day: date, cut: datetime, *, mola_dk: float = MOLA_DK
) -> DayTimeline:
    """Günün şeridi: [09:00, min(mesai sonu, cut)) içinde dolu / mola / kayıp."""
    timeline = DayTimeline(day)
    bounds = shift_bounds(day)
    if bounds is None:
        return timeline
    lo, hi = bounds[0], min(bounds[1], cut)
    if hi <= lo:
        return timeline
    busy = merge((max(a, lo), min(b, hi)) for a, b in intervals if b > lo and a < hi)
    gaps: list[Interval] = []
    cursor = lo
    for a, b in busy:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < hi:
        gaps.append((cursor, hi))

    # Mola en uzun boşluklardan; eşitlikte erken olan. Boşluğun başından kesilir.
    remaining = mola_dk
    mola_of: dict[Interval, float] = {}
    for gap in sorted(gaps, key=lambda g: (-(g[1] - g[0]).total_seconds(), g[0])):
        if remaining <= 0:
            break
        take = min(remaining, (gap[1] - gap[0]).total_seconds() / 60.0)
        mola_of[gap] = take
        remaining -= take

    segments = [Segment(a, b, "dolu") for a, b in busy]
    for gap in gaps:
        take = mola_of.get(gap, 0.0)
        split = gap[0] + timedelta(minutes=take)
        if take > 0:
            segments.append(Segment(gap[0], split, "mola"))
        if split < gap[1]:
            segments.append(Segment(split, gap[1], "kayip"))
    timeline.segments = sorted(segments, key=lambda s: s.start)
    return timeline


def _bucket(group: Group, rep_id: str, day: date, hour: int | None) -> tuple[str | None, str | None]:
    rep = rep_id if group.startswith("rep") else None
    if group in ("hour", "rep_hour"):
        return rep, str(hour)
    if group in ("day", "rep_day"):
        return rep, day.isoformat()
    return rep, None


def aggregate(
    timelines: dict[tuple[str, date], DayTimeline],
    group: Group,
    *,
    running_day: date | None = None,
) -> list[dict[str, Any]]:
    """Şeritleri query_kpis kırılımına toplar: dolu_dk, mola_dk, kayip_dk.

    Faaliyetsiz temsilci-günü (dolu yok) atlanır; running_day hariç.
    """
    sums: dict[tuple[str | None, str | None], dict[str, float]] = defaultdict(
        lambda: {"dolu_dk": 0.0, "mola_dk": 0.0, "kayip_dk": 0.0}
    )
    for (rep_id, day), timeline in timelines.items():
        if timeline.total("dolu") <= 0 and day != running_day:
            continue
        if group in ("hour", "rep_hour"):
            for kind in ("dolu", "mola", "kayip"):
                for hour, minutes in timeline.by_hour(kind).items():
                    sums[_bucket(group, rep_id, day, hour)][f"{kind}_dk"] += minutes
        else:
            key = _bucket(group, rep_id, day, None)
            for kind in ("dolu", "mola", "kayip"):
                sums[key][f"{kind}_dk"] += timeline.total(kind)
    return [
        {"rep_id": rep, "bucket": bucket, **{k: round(v, 2) for k, v in vals.items()}}
        for (rep, bucket), vals in sums.items()
    ]


def fetch_intervals(
    span: Span,
    *,
    rep_ids: Sequence[str] | None = None,
    isodow: Sequence[int] | None = None,
) -> dict[tuple[str, date], list[Interval]]:
    """Temsilci-gün başına dolu aralıklar (birleştirilmemiş)."""
    dur = duration_sec("e")
    meet_min = _meeting_duration_min_sql("e")
    local_day = "(e.occurred_at AT TIME ZONE 'Europe/Istanbul')::date"
    call = (
        f"e.channel = 'call' AND {is_not_planned_sql('e')} AND {is_not_future_sql('e')} "
        f"AND (e.direction = 'outbound' OR ({is_answered_inbound_sql('e')}))"
    )
    meet_end = f"e.occurred_at + make_interval(mins => ({meet_min})::int)"
    # Süren toplantının sonucu henüz girilmemiştir; sürerken dolu sayılır.
    meet = (
        "e.channel = 'meeting' AND (e.meta->>'randevu_durumu' = 'katildi' OR "
        f"(e.meta->>'randevu_durumu' = 'sonuc_girilmedi' AND {meet_end} > now()))"
    )
    wp = "e.channel = 'whatsapp' AND e.direction = 'outbound'"
    extra = ""
    params: list[Any] = [get_org_id(), span.start, span.end]
    if rep_ids:
        extra += " AND e.rep_id = ANY(%s)"
        params.append(list(rep_ids))
    if isodow:
        extra += (
            " AND extract(isodow FROM e.occurred_at AT TIME ZONE 'Europe/Istanbul')::int"
            " = ANY(%s)"
        )
        params.append([int(d) for d in isodow])
    sql = f"""
        SELECT
          e.rep_id,
          {local_day},
          CASE
            WHEN {call} THEN e.occurred_at - make_interval(secs => {BCW_SEC})
            WHEN {wp} THEN e.occurred_at - make_interval(secs => {WP_SEC_PER_MESAJ})
            ELSE e.occurred_at
          END,
          CASE
            WHEN {call} THEN e.occurred_at
              + make_interval(secs => coalesce({dur}, 0) + {ACW_SEC})
            WHEN {meet} THEN least({meet_end}, now())
            ELSE e.occurred_at
          END
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND r.category = 'sales' AND r.active
          AND e.occurred_at >= %s AND e.occurred_at < %s
          AND e.occurred_at <= now()
          AND (({call}) OR ({meet}) OR ({wp}))
          {extra}
    """
    out: dict[tuple[str, date], list[Interval]] = defaultdict(list)
    with connect() as conn:
        for rep_id, day, start, end in conn.execute(sql, params).fetchall():
            out[(str(rep_id), day)].append((start.astimezone(TZ), end.astimezone(TZ)))
    return dict(out)


def timelines_for(
    span: Span,
    cut: datetime,
    *,
    rep_ids: Sequence[str],
    isodow: Sequence[int] | None = None,
    intervals: dict[tuple[str, date], list[Interval]] | None = None,
) -> dict[tuple[str, date], DayTimeline]:
    """Aralıktaki her mesai günü × temsilci için şerit (faaliyetsiz olanlar dahil)."""
    if intervals is None:
        intervals = fetch_intervals(Span(span.start, min(span.end, cut)), rep_ids=rep_ids, isodow=isodow)
    out: dict[tuple[str, date], DayTimeline] = {}
    day = span.start.astimezone(TZ).date()
    last = (min(span.end, cut) - timedelta(microseconds=1)).astimezone(TZ).date()
    wanted = set(isodow) if isodow else None
    while day <= last:
        if shift_bounds(day) is not None and (wanted is None or day.isoweekday() in wanted):
            for rep_id in rep_ids:
                out[(rep_id, day)] = day_timeline(intervals.get((rep_id, day), []), day, cut)
        day += timedelta(days=1)
    return out


def occupancy_rows(
    span: Span,
    *,
    group: Group,
    rep_ids: Sequence[str],
    now: datetime,
    isodow: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """query_kpis ile aynı kırılımda doluluk dakikaları."""
    cut = min(span.end, max(now, span.start))
    timelines = timelines_for(span, cut, rep_ids=rep_ids, isodow=isodow)
    running = now.astimezone(TZ).date() if span.start <= now < span.end else None
    return aggregate(timelines, group, running_day=running)


def attach(rows: list[dict[str, Any]], occ: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Doluluk dakikalarını aynı (rep_id, bucket) KPI satırına ekler.

    KPI satırı olmayan doluluk (yalnız boşluk; arama yok) yeni satır açar.
    """
    index = {(r.get("rep_id"), r.get("bucket")): r for r in rows}
    out = list(rows)
    for item in occ:
        key = (item.get("rep_id"), item.get("bucket"))
        target = index.get(key)
        if target is None:
            target = {"rep_id": key[0], "bucket": key[1]}
            index[key] = target
            out.append(target)
        for name in ("dolu_dk", "mola_dk", "kayip_dk"):
            target[name] = item[name]
    return out


def gaps_report(timeline: DayTimeline, *, min_minutes: float = 5.0) -> list[dict[str, Any]]:
    """Kayıp ve mola parçaları, uzun olandan kısaya (panelde liste)."""
    items = [
        {"start": s.start, "end": s.end, "kind": s.kind, "minutes": round(s.minutes, 1)}
        for s in timeline.segments
        if s.kind != "dolu" and s.minutes >= min_minutes
    ]
    return sorted(items, key=lambda x: -x["minutes"])
