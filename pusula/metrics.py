"""Tek tip KPI vektörü: herhangi bir zaman aralığı, temsilci ve kırılım.

Canlı kokpit, karşılaştırma ekranı, ekip panosu ve dürtme sistemi aynı sorguyu
okur. Temas, çevirme, dönüş ve ulaşma tanımları pusula.temas'tan gelir;
doluluk dakikaları (dolu / mola / kayıp) pusula.occupancy zaman
çizelgesinden. Burada yeni tanım yoktur, sadece toplanır.

Aralıklar yarım açıktır: [start, end). Tüm zamanlar Europe/Istanbul.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pusula.config import get_org_id
from pusula.panel_data import _meeting_duration_min_sql, connect
from pusula.temas import (
    duration_sec,
    is_cevirme_sql,
    is_donus_sql,
    is_gelen_sql,
    is_giden_arama_sql,
    is_lead_reached_sql,
    is_not_future_sql,
    is_not_planned_sql,
    is_temas_sql,
)

TZ = ZoneInfo("Europe/Istanbul")
RATE_MIN_N = 5

Group = Literal["team", "rep", "hour", "day", "rep_hour", "rep_day"]

# Mesai: hafta içi 09-18, cumartesi 09-15, pazar yok. Mola pusula.occupancy'de.
SHIFT_START_HOUR = 9
_SHIFT_END_HOUR = {0: 18, 1: 18, 2: 18, 3: 18, 4: 18, 5: 15}

# Sayılabilir anahtarlar: toplanır. Oranlar toplamdan yeniden hesaplanır.
COUNT_KEYS = (
    "arama",
    "giden_temas",
    "donus",
    "gelen",
    "ulasilan",
    "lead_payda",
    "lead_pay",
    "randevu",
    "katildi",
    "katilmadi",
    "sonuc_girilmedi",
    "whatsapp",
    "sure_toplam",
    "call_sec",
    "meet_dk",
    # pusula.occupancy zaman çizelgesi
    "dolu_dk",
    "mola_dk",
    "kayip_dk",
    "girilmemis_dk",
)


@dataclass(frozen=True)
class Span:
    """[start, end) Istanbul. Gelecek kısmı ölçümde yok sayılır."""

    start: datetime
    end: datetime

    @classmethod
    def days(cls, first: date, last: date) -> Span:
        return cls(
            datetime.combine(first, time.min, tzinfo=TZ),
            datetime.combine(last + timedelta(days=1), time.min, tzinfo=TZ),
        )

    @classmethod
    def day(cls, day: date) -> Span:
        return cls.days(day, day)

    def clipped(self, now: datetime) -> Span:
        return Span(self.start, min(self.end, max(now, self.start)))


def shift_bounds(day: date) -> tuple[datetime, datetime] | None:
    end_hour = _SHIFT_END_HOUR.get(day.weekday())
    if end_hour is None:
        return None
    return (
        datetime.combine(day, time(SHIFT_START_HOUR), tzinfo=TZ),
        datetime.combine(day, time(end_hour), tzinfo=TZ),
    )


def in_shift(moment: datetime) -> bool:
    local = moment.astimezone(TZ)
    bounds = shift_bounds(local.date())
    return bounds is not None and bounds[0] <= local < bounds[1]


def ratio(num: float | None, den: float | None) -> float | None:
    if not den:
        return None
    return round(100.0 * float(num or 0) / float(den), 1)


def finish(row: dict[str, Any], *, n_reps: int = 1) -> dict[str, Any]:
    """Ham sayımlardan oranları ve doluluğu türetir. Yeni dict döner.

    Doluluk = dolu / (dolu + kayıp); mola paydada yoktur. mesai_dk geçen
    brüt mesai (dolu + mola + kayıp).
    """
    out = dict(row)
    out["ulasma_orani"] = ratio(out.get("lead_pay"), out.get("lead_payda"))
    katilim_payda = int(out.get("katildi") or 0) + int(out.get("katilmadi") or 0)
    out["katilim_payda"] = katilim_payda
    out["katilim_orani"] = ratio(out.get("katildi"), katilim_payda)
    temas = int(out.get("giden_temas") or 0)
    if out.get("sure_ort") is None and temas and out.get("sure_toplam") is not None:
        out["sure_ort"] = round(float(out["sure_toplam"]) / temas, 1)
    dolu = float(out.get("dolu_dk") or 0)
    mola = float(out.get("mola_dk") or 0)
    kayip = float(out.get("kayip_dk") or 0)
    out["dolu_dk"] = round(dolu, 1)
    out["mola_dk"] = round(mola, 1)
    out["kayip_dk"] = round(kayip, 1)
    out["girilmemis_dk"] = round(float(out.get("girilmemis_dk") or 0), 1)
    out["mesai_dk"] = round(dolu + mola + kayip, 1)
    out["doluluk"] = ratio(dolu, dolu + kayip)
    out["n_reps"] = n_reps
    return out


def combine(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Sayımları toplar; medyan toplanamaz, düşer. finish() ile tamamlanır."""
    out: dict[str, Any] = {key: 0 for key in COUNT_KEYS}
    out["sure_toplam"] = 0.0
    for row in rows:
        for key in COUNT_KEYS:
            out[key] = (out[key] or 0) + (row.get(key) or 0)
    out["sure_ort"] = None
    out["sure_tipik"] = None
    return out


def _bucket_sql(group: Group) -> tuple[str, str]:
    local = "(e.occurred_at AT TIME ZONE 'Europe/Istanbul')"
    hour = f"extract(hour FROM {local})::int"
    day = f"{local}::date"
    rep = "e.rep_id"
    return {
        "team": ("NULL::text", "NULL::text"),
        "rep": (rep, "NULL::text"),
        "hour": ("NULL::text", f"{hour}::text"),
        "day": ("NULL::text", f"{day}::text"),
        "rep_hour": (rep, f"{hour}::text"),
        "rep_day": (rep, f"{day}::text"),
    }[group]


def query_kpis(
    span: Span,
    *,
    group: Group = "rep",
    rep_ids: Sequence[str] | None = None,
    isodow: Sequence[int] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Ham KPI satırları. Her satır: rep_id, bucket ve COUNT_KEYS + süreler.

    Ulaşma oranı benzersiz lead (thread) üzerinden ve satırın kendi
    kırılımı içinde sayılır; saatlik satırda saat içi, aralıkta aralık içi.
    isodow verilirse yalnız o gün tipleri (1=pazartesi … 6=cumartesi).
    """
    now = now or datetime.now(TZ)
    clipped = span.clipped(now)
    rep_col, bucket_col = _bucket_sql(group)
    dur = duration_sec("e")
    outbound = "e.channel = 'call' AND e.direction = 'outbound'"
    meet = "e.channel = 'meeting'"
    temas = f"{outbound} AND {is_temas_sql('e')}"
    connected = (
        f"e.channel = 'call' AND e.meta->>'call_status' = 'connected' "
        f"AND {is_not_planned_sql('e')} AND {is_not_future_sql('e')}"
    )
    rep_filter = ""
    params: list[Any] = [get_org_id(), clipped.start, clipped.end]
    if rep_ids:
        rep_filter = "AND e.rep_id = ANY(%s)"
        params.append(list(rep_ids))
    if isodow:
        rep_filter += (
            " AND extract(isodow FROM e.occurred_at AT TIME ZONE "
            "'Europe/Istanbul')::int = ANY(%s)"
        )
        params.append([int(d) for d in isodow])
    sql = f"""
        SELECT
          {rep_col} AS rep_id,
          {bucket_col} AS bucket,
          count(*) FILTER (WHERE {outbound} AND {is_cevirme_sql('e')})::int,
          count(*) FILTER (WHERE {temas})::int,
          count(*) FILTER (WHERE {is_donus_sql('e')})::int,
          count(*) FILTER (WHERE {is_gelen_sql('e')})::int,
          count(DISTINCT e.thread_id) FILTER (
            WHERE e.thread_id IS NOT NULL AND {is_giden_arama_sql('e')}
          )::int,
          count(DISTINCT e.thread_id) FILTER (
            WHERE e.thread_id IS NOT NULL AND {is_lead_reached_sql('e')}
          )::int,
          count(*) FILTER (WHERE {meet})::int,
          count(*) FILTER (
            WHERE {meet} AND e.meta->>'randevu_durumu' = 'katildi'
          )::int,
          count(*) FILTER (
            WHERE {meet} AND e.meta->>'randevu_durumu' = 'katilmadi'
          )::int,
          count(*) FILTER (
            WHERE {meet} AND e.meta->>'randevu_durumu' = 'sonuc_girilmedi'
          )::int,
          count(*) FILTER (
            WHERE e.channel = 'whatsapp' AND e.direction = 'outbound'
          )::int,
          sum({dur}) FILTER (WHERE {temas})::float,
          avg({dur}) FILTER (WHERE {temas})::float,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY {dur})
            FILTER (WHERE {temas})::float,
          coalesce(sum({dur}) FILTER (WHERE {connected}), 0)::float,
          coalesce(sum({_meeting_duration_min_sql('e')}) FILTER (
            WHERE {meet} AND e.meta->>'randevu_durumu' = 'katildi'
          ), 0)::float
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND r.category = 'sales' AND r.active
          AND e.occurred_at >= %s AND e.occurred_at < %s
          AND e.occurred_at <= now()
          AND extract(isodow FROM e.occurred_at AT TIME ZONE 'Europe/Istanbul') <> 7
          {rep_filter}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    with connect() as conn:
        fetched = conn.execute(sql, params).fetchall()
    rows: list[dict[str, Any]] = []
    for rec in fetched:
        (
            rep_id,
            bucket,
            arama,
            giden_temas,
            donus,
            gelen,
            lead_payda,
            lead_pay,
            randevu,
            katildi,
            katilmadi,
            sonuc,
            whatsapp,
            sure_toplam,
            sure_ort,
            sure_tipik,
            call_sec,
            meet_dk,
        ) = rec
        rows.append(
            {
                "rep_id": rep_id,
                "bucket": bucket,
                "arama": arama,
                "giden_temas": giden_temas,
                "donus": donus,
                "gelen": gelen,
                "ulasilan": giden_temas + donus,
                "lead_payda": lead_payda,
                "lead_pay": lead_pay,
                "randevu": randevu,
                "katildi": katildi,
                "katilmadi": katilmadi,
                "sonuc_girilmedi": sonuc,
                "whatsapp": whatsapp,
                "sure_toplam": sure_toplam or 0.0,
                "sure_ort": round(sure_ort, 1) if sure_ort is not None else None,
                "sure_tipik": round(sure_tipik, 1) if sure_tipik is not None else None,
                "call_sec": call_sec,
                "meet_dk": meet_dk,
            }
        )
    return rows


def empty_row() -> dict[str, Any]:
    row: dict[str, Any] = {key: 0 for key in COUNT_KEYS}
    row.update({"ulasilan": 0, "sure_ort": None, "sure_tipik": None})
    return row


def last_activity(day: date, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Temsilci bazında günün son faaliyet anları.

    Arama: başlangıç + süre (bitiş). Toplantı: başlangıç + süre, şimdiyi
    geçmez. WhatsApp: son giden mesaj. CRM: crm_actions tablosu (varsa).
    in_meeting / in_call: şu an süren toplantı veya arama kaydı var mı.
    """
    now = now or datetime.now(TZ)
    span = Span.day(day)
    dur = duration_sec("e")
    meet_min = _meeting_duration_min_sql("e")
    sql = f"""
        SELECT
          e.rep_id,
          min(e.occurred_at) FILTER (
            WHERE e.channel IN ('call', 'whatsapp')
               OR (e.channel = 'meeting' AND e.meta->>'randevu_durumu' = 'katildi')
          ),
          max(e.occurred_at + make_interval(secs => coalesce({dur}, 0)))
            FILTER (
              WHERE e.channel = 'call' AND {is_not_planned_sql('e')}
                AND e.meta->>'call_status' = 'connected'
            ),
          max(least(e.occurred_at + make_interval(mins => ({meet_min})::int), now()))
            FILTER (
              WHERE e.channel = 'meeting'
                AND e.meta->>'randevu_durumu' = 'katildi'
            ),
          max(e.occurred_at) FILTER (
            WHERE e.channel = 'whatsapp' AND e.direction = 'outbound'
          ),
          bool_or(
            e.channel = 'meeting'
            AND coalesce(e.meta->>'randevu_durumu', '') NOT IN ('katilmadi', 'iptal_edildi')
            AND e.occurred_at + make_interval(mins => ({meet_min})::int) > now()
          ),
          bool_or(
            e.channel = 'call' AND {is_not_planned_sql('e')}
            AND e.occurred_at + make_interval(secs => coalesce({dur}, 0)) > now()
          )
        FROM events e
        JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
        WHERE e.org_id = %s
          AND r.category = 'sales' AND r.active
          AND e.occurred_at >= %s AND e.occurred_at < %s
          AND e.occurred_at <= now()
        GROUP BY 1
    """
    org_id = get_org_id()
    with connect() as conn:
        rows = conn.execute(sql, (org_id, span.start, span.end)).fetchall()
        crm = _last_crm_actions(conn, org_id, span)
    out: dict[str, dict[str, Any]] = {}
    for rep_id, first_at, call_at, meet_at, wp_at, in_meeting, in_call in rows:
        out[str(rep_id)] = {
            "first_at": first_at,
            "last_call_at": call_at,
            "last_meeting_at": meet_at,
            "last_whatsapp_at": wp_at,
            "last_crm_at": None,
            "in_meeting": bool(in_meeting),
            "in_call": bool(in_call),
        }
    for rep_id, crm_at in crm.items():
        out.setdefault(
            rep_id,
            {
                "first_at": None,
                "last_call_at": None,
                "last_meeting_at": None,
                "last_whatsapp_at": None,
                "last_crm_at": None,
            },
        )["last_crm_at"] = crm_at
    for item in out.values():
        stamps = [
            item[key]
            for key in ("last_call_at", "last_meeting_at", "last_whatsapp_at", "last_crm_at")
            if item[key] is not None
        ]
        item["last_active_at"] = max(stamps) if stamps else None
    return out


def crm_tracking_enabled() -> bool:
    """CRM işlem akışı kurulmuş ve en az bir kez yazmış mı."""
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('crm_actions')").fetchone()
        if not exists or exists[0] is None:
            return False
        row = conn.execute("SELECT EXISTS (SELECT 1 FROM crm_actions)").fetchone()
    return bool(row and row[0])


def _last_crm_actions(conn: Any, org_id: str, span: Span) -> dict[str, datetime]:
    """crm_actions henüz yoksa boş döner (şema göçü sonrası dolar)."""
    exists = conn.execute("SELECT to_regclass('crm_actions')").fetchone()
    if not exists or exists[0] is None:
        return {}
    rows = conn.execute(
        """
        SELECT actor_rep_id, max(acted_at)
        FROM crm_actions
        WHERE org_id = %s AND acted_at >= %s AND acted_at < %s
          AND acted_at <= now()
        GROUP BY 1
        """,
        (org_id, span.start, span.end),
    ).fetchall()
    return {str(rep_id): acted_at for rep_id, acted_at in rows if rep_id}


def idle_minutes(
    last_active_at: datetime | None,
    *,
    now: datetime,
    data_as_of: datetime | None = None,
) -> float | None:
    """Mesai içinde son faaliyetten bu yana geçen dakika.

    Ölçüm ucu, veri tazeliğini aşmaz: veri 10 dk gerideyse hareketsizlik
    o ana kadar sayılır. Mesai dışında veya gün başlamadıysa None.
    """
    if not in_shift(now):
        return None
    edge = min(now, data_as_of) if data_as_of else now
    start, _ = shift_bounds(now.astimezone(TZ).date()) or (now, now)
    since = last_active_at if last_active_at and last_active_at > start else start
    minutes = (edge - since).total_seconds() / 60.0
    return round(max(minutes, 0.0), 1)
