"""Toplantı tablosu: planlanan, gelen, sonuç girişi, gerçek süre, takip araması.

Her Bookings randevusu (meeting olayı) aynı temsilcinin ±20 dk içindeki
Zoho Meeting oturumuyla eşleşir. Müşterinin gelip gelmediği önce Zoho
Meeting'den (odada ≥ 1 dk), oturum yoksa CRM sonucundan okunur. Gelmeyen
müşteriye toplantı saatinden sonra giden arama / WhatsApp var mı bakılır.

classify / summarize saf fonksiyonlardır; load_meetings veritabanını okur.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.ingest.zoho_meeting import CUSTOMER_MIN_SEC, busy_interval
from pusula.metrics import Span
from pusula.occupancy import MATCH_MIN
from pusula.panel_data import _meeting_duration_min_sql, connect
from pusula.temas import is_not_planned_sql

RESULT_ENTERED = ("katildi", "katilmadi", "iptal_edildi")


def classify(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Tek toplantının durumu.

    state: bekleniyor | suruyor | geldi | gelmedi | bilinmiyor | iptal
    source: hangi bilgiden karar verildi (zoho_meeting | crm | yok)
    """
    start: datetime = row["start"]
    end = start + timedelta(minutes=float(row.get("planned_min") or 30))
    crm = row.get("crm_status") or "sonuc_girilmedi"
    has_session = row.get("presenter_join") is not None or row.get("customer_join") is not None
    customer_sec = float(row.get("customer_sec") or 0)
    came: bool | None
    source = "yok"
    if has_session:
        came = customer_sec >= CUSTOMER_MIN_SEC
        source = "zoho_meeting"
    elif crm == "katildi":
        came, source = True, "crm"
    elif crm == "katilmadi":
        came, source = False, "crm"
    else:
        came = None

    ongoing = row.get("presenter_join") is not None and row.get("presenter_leave") is None
    if crm == "iptal_edildi":
        state = "iptal"
    elif now < start and not has_session:
        state = "bekleniyor"
    elif ongoing or (start <= now < end and not has_session):
        state = "suruyor"
    elif came is True:
        state = "geldi"
    elif came is False:
        state = "gelmedi"
    else:
        state = "bilinmiyor"

    busy = busy_interval(
        row.get("presenter_join"),
        row.get("presenter_leave"),
        row.get("customer_join"),
        row.get("customer_leave"),
        customer_sec,
        now,
    )
    if busy is not None:
        minutes = (busy[1] - busy[0]).total_seconds() / 60.0
    elif not has_session and crm == "katildi":
        minutes = (min(end, now) - start).total_seconds() / 60.0
    else:
        minutes = 0.0

    followed = state == "gelmedi" and (row.get("call_after") or row.get("wa_after")) is not None
    return {
        "rep_id": row["rep_id"],
        "start": start,
        "planned_min": float(row.get("planned_min") or 0),
        "customer": row.get("customer"),
        "state": state,
        "source": source,
        "crm_status": crm,
        "result_entered": crm in RESULT_ENTERED,
        # CRM "katıldı" ama Zoho Meeting'de müşteri yok (veya tersi).
        "mismatch": source == "zoho_meeting"
        and ((crm == "katildi" and came is False) or (crm == "katilmadi" and came is True)),
        "minutes": round(max(minutes, 0.0), 1),
        "presenter_join": row.get("presenter_join"),
        "customer_join": row.get("customer_join"),
        "call_after": row.get("call_after"),
        "wa_after": row.get("wa_after"),
        "followed_up": followed,
    }


def summarize(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Temsilci bazında toplam. İptal planlanana girmez."""
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "planlanan": 0,
            "gelen": 0,
            "gelmeyen": 0,
            "bilinmiyor": 0,
            "bekleyen": 0,
            "iptal": 0,
            "sonuc_girilen": 0,
            "sonuc_girilmemis": 0,
            "toplam_dk": 0.0,
            "aranan": 0,
            "aranmayan": 0,
            "uyumsuz": 0,
            "katilim_orani": None,
        }
    )
    for item in items:
        s = out[item["rep_id"]]
        state = item["state"]
        if state == "iptal":
            s["iptal"] += 1
            continue
        s["planlanan"] += 1
        s["toplam_dk"] = round(s["toplam_dk"] + item["minutes"], 1)
        if state == "geldi":
            s["gelen"] += 1
        elif state == "gelmedi":
            s["gelmeyen"] += 1
            s["aranan" if item["followed_up"] else "aranmayan"] += 1
        elif state == "bilinmiyor":
            s["bilinmiyor"] += 1
        else:
            s["bekleyen"] += 1
        if state not in ("bekleniyor", "suruyor"):
            s["sonuc_girilen" if item["result_entered"] else "sonuc_girilmemis"] += 1
        if item["mismatch"]:
            s["uyumsuz"] += 1
    for s in out.values():
        decided = s["gelen"] + s["gelmeyen"]
        s["katilim_orani"] = round(100.0 * s["gelen"] / decided, 1) if decided else None
    return dict(out)


def _customer_from_topic(topic: str | None) -> str | None:
    """'Temsilci with Müşteri' → 'Müşteri'."""
    if not topic or " with " not in topic:
        return None
    return topic.split(" with ", 1)[1].strip() or None


def load_meetings(day: date) -> list[dict[str, Any]]:
    """Günün satış toplantıları; eşleşen Zoho Meeting oturumu ve takip temasları."""
    span = Span.day(day)
    meet_min = _meeting_duration_min_sql("e")
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('meeting_sessions')").fetchone()
        has_sessions = bool(exists and exists[0])
        session_join = (
            f"""LEFT JOIN LATERAL (
                SELECT ms.topic, ms.presenter_join, ms.presenter_leave, ms.customer_sec,
                       (to_jsonb(ms)->>'customer_join')::timestamptz AS customer_join,
                       (to_jsonb(ms)->>'customer_leave')::timestamptz AS customer_leave
                FROM meeting_sessions ms
                WHERE ms.org_id = e.org_id AND ms.rep_id = e.rep_id
                  AND ms.planned_start BETWEEN e.occurred_at - interval '{MATCH_MIN} minutes'
                                           AND e.occurred_at + interval '{MATCH_MIN} minutes'
                ORDER BY abs(extract(epoch FROM ms.planned_start - e.occurred_at))
                LIMIT 1
            ) ms ON true"""
            if has_sessions
            else """LEFT JOIN LATERAL (
                SELECT NULL::text AS topic, NULL::timestamptz AS presenter_join,
                       NULL::timestamptz AS presenter_leave, 0::numeric AS customer_sec,
                       NULL::timestamptz AS customer_join, NULL::timestamptz AS customer_leave
            ) ms ON true"""
        )
        rows = conn.execute(
            f"""
            SELECT
              e.rep_id, e.occurred_at, ({meet_min})::numeric, e.meta->>'randevu_durumu',
              (SELECT l.full_name FROM leads l
                 WHERE l.org_id = e.org_id AND l.thread_id = e.thread_id
                 ORDER BY l.assigned_at DESC NULLS LAST LIMIT 1),
              ms.topic, ms.presenter_join, ms.presenter_leave, ms.customer_sec,
              ms.customer_join, ms.customer_leave,
              (SELECT min(c.occurred_at) FROM events c
                 WHERE c.org_id = e.org_id AND c.thread_id = e.thread_id
                   AND c.channel = 'call' AND c.direction = 'outbound'
                   AND {is_not_planned_sql('c')}
                   AND c.occurred_at >= e.occurred_at AND c.occurred_at <= now()),
              (SELECT min(w.occurred_at) FROM events w
                 WHERE w.org_id = e.org_id AND w.thread_id = e.thread_id
                   AND w.channel = 'whatsapp' AND w.direction = 'outbound'
                   AND w.occurred_at >= e.occurred_at AND w.occurred_at <= now())
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            {session_join}
            WHERE e.org_id = %s AND e.channel = 'meeting'
              AND r.category = 'sales' AND r.active
              AND e.occurred_at >= %s AND e.occurred_at < %s
            ORDER BY e.occurred_at
            """,
            (get_org_id(), span.start, span.end),
        ).fetchall()
    out = []
    for (rep_id, start, planned, crm, lead_name, topic, pj, pl, csec, cj, cl, call_at, wa_at) in rows:
        out.append(
            {
                "rep_id": str(rep_id),
                "start": start,
                "planned_min": float(planned or 0) or 30.0,
                "crm_status": crm,
                "customer": lead_name or _customer_from_topic(topic),
                "presenter_join": pj,
                "presenter_leave": pl,
                "customer_sec": float(csec or 0),
                "customer_join": cj,
                "customer_leave": cl,
                "call_after": call_at,
                "wa_after": wa_at,
            }
        )
    return out


def meetings_payload(day: date, now: datetime) -> dict[str, Any]:
    items = [classify(row, now) for row in load_meetings(day)]
    return {"items": items, "by_rep": summarize(items)}
