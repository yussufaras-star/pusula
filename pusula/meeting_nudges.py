"""Randevu dürtmeleri: Bookings randevusu yaklaşırken ve başladığında Cliq.

Her çalıştırmada (5 dk'da bir) bugünün Bookings randevuları ve Zoho
Meeting'in canlı oda durumu doğrudan çekilir; veritabanındaki senkron
beklenmez. Yalnız satış ekibi, yalnız temsilciye gider.

- ara:       başlangıçtan ~1 saat önce, müşteriyi araması hatırlatılır.
- hatirlat:  ~20 dk önce toplantı hatırlatması (GitHub zamanlaması 5-15 dk
             gecikebildiği için yönetici kararıyla 20 dk).
- oda:       saat geçti, Zoho Meeting odası açılmadı ("delay").

Her randevu × tür bir kez (nudge_queue.facts->>'booking_id'). İptal edilen,
sonucu girilmiş randevuya ve izinli temsilciye gitmez. Pencere kaçarsa
(ör. randevu son dakika alındı) o tür atlanır.
"""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from psycopg.types.json import Json

from pusula.block_share import load_sales_reps
from pusula.config import get_org_id
from pusula.ingest import zoho_meeting
from pusula.ingest.bookings import (
    _fetch_all_appointments,
    _format_bookings_time,
    _name_key,
    _parse_start,
)
from pusula.leaves import load_leaves
from pusula.metrics import TZ, Span
from pusula.pace_nudges import _ensure, _send
from pusula.panel_data import connect

logger = logging.getLogger(__name__)

_LOCK_KEY = 4_204_211
# Bu durumdaki randevuya dürtme gitmez (iptal veya sonuç girilmiş).
CLOSED = {"cancel", "cancelled", "completed", "no_show"}
# Gönderim pencereleri, başlangıca göre dakika: [başı, sonu).
WINDOWS: dict[str, tuple[int, int]] = {
    "ara": (-65, -20),
    "hatirlat": (-25, 0),
    "oda": (3, 30),
}
KINDS = {"ara": "randevu_ara", "hatirlat": "randevu_hatirlat", "oda": "randevu_oda"}


@dataclass(frozen=True)
class Appointment:
    booking_id: str
    rep_id: str
    start: datetime
    customer: str
    phone: str | None
    status: str
    meeting_key: str | None
    join_link: str | None


def _meeting_info(payload: dict[str, Any]) -> dict[str, Any]:
    """meeting_info Bookings'te Python sözlüğü metni olarak gelir."""
    raw = payload.get("meeting_info") or payload.get("zoho_meeting_info")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        for parse in (json.loads, ast.literal_eval):
            try:
                value = parse(raw)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, dict):
                return value
    return {}


def to_appointments(rows: list[dict[str, Any]], reps_by_name: dict[str, str]) -> list[Appointment]:
    out: list[Appointment] = []
    for row in rows:
        rep_id = reps_by_name.get(_name_key(str(row.get("staff_name") or "")))
        start = _parse_start(row)
        booking_id = str(row.get("booking_id") or "").strip()
        if rep_id is None or start is None or not booking_id:
            continue
        info = _meeting_info(row)
        out.append(
            Appointment(
                booking_id=booking_id,
                rep_id=rep_id,
                start=start.astimezone(TZ),
                customer=" ".join(str(row.get("customer_name") or "müşteri").split()),
                phone=(str(row.get("customer_contact_no") or "").strip() or None),
                status=str(row.get("status") or "").strip().casefold(),
                meeting_key=(str(info.get("meeting_key")) if info.get("meeting_key") else None),
                join_link=info.get("join_link") or None,
            )
        )
    return out


def due(
    appt: Appointment, now: datetime, room_status: dict[str, str]
) -> str | None:
    """Şu an gönderilmesi gereken tür (ara | hatirlat | oda) ya da None."""
    if appt.status in CLOSED:
        return None
    offset = (now - appt.start).total_seconds() / 60.0
    for kind, (lo, hi) in WINDOWS.items():
        if not lo <= offset < hi:
            continue
        if kind == "oda":
            # Yalnız Zoho'nun "gecikti" dediği oda: başlamış / bitmiş / bilinmeyen değil.
            if appt.meeting_key is None or room_status.get(appt.meeting_key) != "delay":
                continue
        return kind
    return None


def _clock(value: datetime) -> str:
    return value.astimezone(TZ).strftime("%H:%M")


def format_message(kind: str, appt: Appointment, now: datetime) -> str:
    at = _clock(appt.start)
    if kind == "ara":
        text = f"Saat {at}'te toplantıya gelecek {appt.customer} isimli müşterini aramayı unutma."
        if appt.phone:
            text += f"\nTelefon: {appt.phone}"
        return text
    if kind == "hatirlat":
        minutes = max(round((appt.start - now).total_seconds() / 60.0), 1)
        text = f"{minutes} dk sonra ({at}) {appt.customer} ile toplantın var."
        if appt.join_link:
            text += f"\nOda: {appt.join_link}"
        return text
    late = max(round((now - appt.start).total_seconds() / 60.0), 1)
    text = (
        f"{at} {appt.customer} toplantın {late} dk önce başladı ama Zoho Meeting odası "
        "henüz açılmadı. Müşteri bekliyor olabilir."
    )
    if appt.join_link:
        text += f"\nOda: {appt.join_link}"
    return text


def _room_status() -> dict[str, str]:
    """meeting_key → liveConferenceStatus (started | delay | ended). Token yoksa boş."""
    if not zoho_meeting.enabled():
        return {}
    zsoid = zoho_meeting._zsoid()
    body = zoho_meeting._get(f"{zsoid}/sessions.json", {"listtype": "upcoming", "index": 1, "count": 100})
    return {
        str(row.get("meetingKey")): str(row.get("liveConferenceStatus"))
        for row in body.get("session") or []
        if row.get("meetingKey") and row.get("liveConferenceStatus")
    }


def _sent_keys(conn: Any, day: date) -> set[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT kind, facts->>'booking_id' FROM nudge_queue
        WHERE org_id = %s AND day = %s AND kind = ANY(%s) AND status <> 'hata'
        """,
        (get_org_id(), day, list(KINDS.values())),
    ).fetchall()
    return {(str(kind), str(booking)) for kind, booking in rows}


def in_window(local: datetime) -> bool:
    """Pazar hariç 08:00-19:00 (09:00 randevusunun 1 saat öncesi dahil)."""
    return local.weekday() != 6 and 8 <= local.hour < 19


def run_meeting_nudges(now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if not in_window(now):
        return {"skipped": "zaman_disi", "at": now.isoformat()}
    reps = load_sales_reps()
    emails = {rep.rep_id: rep.email for rep in reps}
    names = {rep.rep_id: rep.full_name for rep in reps}
    by_name = {_name_key(rep.full_name): rep.rep_id for rep in reps}
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = _fetch_all_appointments(
        _format_bookings_time(day_start), _format_bookings_time(day_start.replace(hour=23, minute=59, second=59))
    )
    appts = to_appointments(rows, by_name)
    on_leave = {rep_id for rep_id, _ in load_leaves(Span.day(now.date()))}
    report: dict[str, Any] = {"at": now.isoformat(), "appointments": len(appts), "sent": 0, "failed": 0}
    candidates = [a for a in appts if a.rep_id not in on_leave and a.rep_id in emails]
    room: dict[str, str] = {}
    if any(WINDOWS["oda"][0] <= (now - a.start).total_seconds() / 60.0 < WINDOWS["oda"][1] for a in candidates):
        try:
            room = _room_status()
        except Exception as exc:  # noqa: BLE001 — oda durumu yoksa diğer dürtmeler yine gider
            logger.warning("Zoho Meeting oda durumu alınamadı: %s", exc)
            report["room_error"] = str(exc)[:200]

    with connect() as lock_conn, connect() as conn:
        locked = lock_conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (_LOCK_KEY,)).fetchone()
        if not locked or not locked[0]:
            return {"skipped": "baska_calisma_suruyor", "at": now.isoformat()}
        _ensure(conn)
        sent = _sent_keys(conn, now.date())
        conn.commit()
        for appt in candidates:
            kind = due(appt, now, room)
            if kind is None or (KINDS[kind], appt.booking_id) in sent:
                continue
            body = format_message(kind, appt, now)
            facts = {"booking_id": appt.booking_id, "start": appt.start.isoformat(), "customer": appt.customer}
            row = conn.execute(
                """
                INSERT INTO nudge_queue (org_id, rep_id, rep_name, day, created_at, kind,
                                         facts, body, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'gonderiliyor')
                RETURNING id
                """,
                (get_org_id(), appt.rep_id, names[appt.rep_id], now.date(), now, KINDS[kind],
                 Json(facts), body),
            ).fetchone()
            conn.commit()
            ok = _send(conn, row[0], body, name=names[appt.rep_id], email=emails[appt.rep_id], now=now)
            conn.commit()
            report["sent" if ok else "failed"] += 1
    return report
