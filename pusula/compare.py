"""Karşılaştırma ekranı: iki taraf (dönem × kişi) aynı KPI vektörüyle.

Taraf = bir zaman aralığı + bir özne. Özne "team" (ekip toplamı), "avg"
(temsilci başına ekip ortalaması) veya bir rep_id'dir. Hesap
pusula.metrics.query_kpis'tedir; burada yalnız hizalama, ortalama ve seri
kırılımı yapılır.

Hizalama: süren bir dönem (bu hafta) geçmiş bir dönemle (geçen hafta)
kıyaslanırken geçmiş taraf, süren tarafın geldiği ana kadar kesilir; yani
"bu hafta şu ana kadar" ile "geçen hafta aynı ana kadar" yan yana gelir.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from pusula.metrics import (
    COUNT_KEYS,
    TZ,
    Span,
    available_minutes,
    combine,
    finish,
    query_kpis,
    shift_bounds,
)

TEAM = "team"
AVG = "avg"
MAX_SPAN_DAYS = 366

QueryFn = Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True)
class Side:
    first: date
    last: date
    subject: str

    @property
    def span(self) -> Span:
        return Span.days(self.first, self.last)


def side_cut(side: Side, now: datetime, *, other: Side | None = None) -> datetime:
    """Tarafın ölçüm ucu. other verilirse onun geçen süresine hizalanır."""
    own_end = min(side.span.end, max(now, side.span.start))
    if other is None:
        return own_end
    elapsed = min(other.span.end, max(now, other.span.start)) - other.span.start
    return min(own_end, side.span.start + elapsed)


def work_days(span: Span, cut: datetime) -> float:
    """Kesime kadar geçen mesai günü; süren gün geçen mesai oranıyla sayılır."""
    total = 0.0
    day = span.start.date()
    while datetime.combine(day, datetime.min.time(), tzinfo=TZ) < span.end:
        bounds = shift_bounds(day)
        if bounds is not None and cut > bounds[0]:
            full = (bounds[1] - bounds[0]).total_seconds()
            done = (min(cut, bounds[1]) - bounds[0]).total_seconds()
            total += done / full
        day += timedelta(days=1)
    return round(total, 2)


def per_rep(row: dict[str, Any], n_reps: int) -> dict[str, Any]:
    """Ekip toplamını temsilci başına indirir. Oranlar değişmez."""
    out = dict(row)
    n = max(n_reps, 1)
    for key in (*COUNT_KEYS, "ulasilan"):
        if out.get(key) is not None:
            out[key] = out[key] / n
    return out


def series_group(*sides: Side) -> str:
    """Taraflar aynı kırılımı paylaşır: hepsi tek günse saat, değilse gün."""
    return "hour" if all(side.first == side.last for side in sides) else "day"


def _bucket_span(bucket: str, group: str, side: Side) -> Span:
    if group == "hour":
        start = datetime.combine(side.first, datetime.min.time(), tzinfo=TZ)
        start += timedelta(hours=int(bucket))
        return Span(start, start + timedelta(hours=1))
    return Span.day(date.fromisoformat(bucket))


def _series_keys(side: Side, cut: datetime, group: str) -> list[str]:
    """Kesime kadar olan tüm mesai kovaları; boş kova sıfır olarak görünür."""
    if group == "hour":
        bounds = shift_bounds(side.first)
        if bounds is None:
            return []
        hours = range(bounds[0].hour, bounds[1].hour)
        return [str(h) for h in hours if _bucket_span(str(h), group, side).start < cut]
    keys = []
    day = side.first
    while day <= side.last:
        bounds = shift_bounds(day)
        if bounds is not None and bounds[0] < cut:
            keys.append(day.isoformat())
        day += timedelta(days=1)
    return keys


def build_side(
    side: Side,
    cut: datetime,
    *,
    n_reps: int,
    total_rows: Sequence[dict[str, Any]],
    series_rows: Sequence[dict[str, Any]],
    label: str,
    group: str | None = None,
    aligned: bool = False,
) -> dict[str, Any]:
    """Saf: sorgu çıktısından taraf yükü. Özne filtresi sorguda uygulanmıştır."""
    span = side.span
    measured = Span(span.start, cut)
    is_avg = side.subject == AVG
    scale_n = n_reps if side.subject == TEAM else 1
    total = combine(total_rows)
    if len(total_rows) == 1:
        total["sure_tipik"] = total_rows[0].get("sure_tipik")
    if is_avg:
        total = per_rep(total, n_reps)
    kpis = finish(total, avail_min=available_minutes(measured, cut), n_reps=scale_n)

    group = group or series_group(side)
    by_bucket = {str(row["bucket"]): row for row in series_rows}
    series = []
    for key in _series_keys(side, cut, group):
        row = combine([by_bucket[key]] if key in by_bucket else [])
        if is_avg:
            row = per_rep(row, n_reps)
        bucket_span = _bucket_span(key, group, side)
        done = finish(
            row,
            avail_min=available_minutes(bucket_span, cut),
            n_reps=scale_n,
        )
        series.append(
            {
                "bucket": int(key) if group == "hour" else key,
                "partial": bucket_span.end > cut,
                "kpis": done,
            }
        )
    return {
        "label": label,
        "subject": side.subject,
        "first": side.first,
        "last": side.last,
        "cut": cut,
        "aligned": aligned,
        "work_days": work_days(span, cut),
        "n_reps": scale_n,
        "kpis": kpis,
        "series_group": group,
        "series": series,
    }


def load_side(
    side: Side,
    cut: datetime,
    *,
    rep_ids: Sequence[str],
    label: str,
    group: str | None = None,
    aligned: bool = False,
    query: QueryFn = query_kpis,
) -> dict[str, Any]:
    """Tarafı veritabanından yükler. rep_ids: aktif satış temsilcileri."""
    measured = Span(side.span.start, cut)
    subject_ids = None if side.subject in (TEAM, AVG) else [side.subject]
    group = group or series_group(side)
    total_rows = query(measured, group="team", rep_ids=subject_ids, now=cut)
    series_rows = query(measured, group=group, rep_ids=subject_ids, now=cut)
    return build_side(
        side,
        cut,
        n_reps=len(rep_ids),
        total_rows=total_rows,
        series_rows=series_rows,
        label=label,
        group=group,
        aligned=aligned,
    )
