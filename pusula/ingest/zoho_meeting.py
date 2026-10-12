"""Zoho Meeting → meeting_sessions: temsilcinin gerçek toplantı katılımı.

Bookings randevunun planını verir (ör. 30 dk). Zoho Meeting katılımcı
raporu gerçek giriş-çıkışı verir: sunucu (temsilci) ne zaman girdi, ne
zaman çıktı, müşteri katıldı mı. Doluluk bu süreyi kullanır.

Yetki ayrı bir refresh token'dadır (ZOHO_MEETING_REFRESH_TOKEN; kapsam
ZohoMeeting.meeting.READ + ZohoMeeting.manageOrg.READ). CRM token'ına
dokunulmaz. Değişken yoksa senkron atlanır.

Uçlar (meeting.zoho.com/api/v2):
- user.json → zsoid (kuruluş no)
- {zsoid}/sessions.json?listtype=past → toplantılar, yeniden eskiye
- {zsoid}/participant/{meetingKey}.json → joinTime / leaveTime (epoch ms)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from pusula.config import get_org_id
from pusula.panel_data import connect

logger = logging.getLogger(__name__)

ENV_TOKEN = "ZOHO_MEETING_REFRESH_TOKEN"
BASE = "https://meeting.zoho.com/api/v2"
PAGE = 100
# Müşteri en az bu kadar kaldıysa toplantı yapılmış sayılır.
CUSTOMER_MIN_SEC = 60
# Bitişinden bu kadar sonra çekilmiş rapor kesindir; yeniden çekilmez.
FINAL_AFTER = timedelta(minutes=30)
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_TOKEN_TTL = 55 * 60

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meeting_sessions (
    org_id           text NOT NULL DEFAULT 'rexven',
    meeting_key      text NOT NULL,
    presenter_email  text,
    rep_id           text,
    topic            text,
    planned_start    timestamptz NOT NULL,
    planned_min      numeric,
    presenter_join   timestamptz,
    presenter_leave  timestamptz,
    customer_sec     numeric NOT NULL DEFAULT 0,
    participants     int NOT NULL DEFAULT 0,
    fetched_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, meeting_key)
);
CREATE INDEX IF NOT EXISTS idx_meeting_sessions_rep_time
    ON meeting_sessions (org_id, rep_id, planned_start);
"""

_lock = threading.Lock()
_token: tuple[str, float] | None = None


def enabled() -> bool:
    return bool(os.environ.get(ENV_TOKEN))


def _access_token() -> str:
    global _token
    with _lock:
        if _token and time.monotonic() < _token[1]:
            return _token[0]
        resp = httpx.post(
            os.environ["ZOHO_ACCOUNTS_DOMAIN"].rstrip("/") + "/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": os.environ["ZOHO_CLIENT_ID"],
                "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
                "refresh_token": os.environ[ENV_TOKEN],
            },
            timeout=_TIMEOUT,
        )
        body = resp.json() if resp.status_code == 200 else {}
        if "access_token" not in body:
            raise RuntimeError(f"Zoho Meeting token yenilenemedi (HTTP {resp.status_code})")
        _token = (body["access_token"], time.monotonic() + _TOKEN_TTL)
        return _token[0]


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(4):
        resp = httpx.get(
            f"{BASE}/{path}",
            headers={"Authorization": f"Zoho-oauthtoken {_access_token()}"},
            params=params,
            timeout=_TIMEOUT,
        )
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(2**attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def _ms(value: Any) -> datetime | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms > 0 else None


def summarize(session: dict[str, Any], participants: list[dict[str, Any]]) -> dict[str, Any]:
    """Saf: oturum + katılımcı raporu → meeting_sessions satırı."""
    presenter = (session.get("presenterEmail") or "").strip().lower()
    joins: list[datetime] = []
    leaves: list[datetime] = []
    open_presence = False
    customer_sec = 0.0
    for part in participants:
        email = str(part.get("email") or "").strip().lower()
        is_presenter = part.get("role") == "presenter" or (presenter and email == presenter)
        if is_presenter:
            join, leave = _ms(part.get("joinTime")), _ms(part.get("leaveTime"))
            if join:
                joins.append(join)
                if leave and leave > join:
                    leaves.append(leave)
                else:
                    open_presence = True
        else:
            customer_sec += max(float(part.get("duration") or 0) / 1000.0, 0.0)
    return {
        "meeting_key": str(session.get("meetingKey")),
        "presenter_email": presenter or None,
        "topic": session.get("topic"),
        "planned_start": _ms(session.get("startTimeMillisec") or session.get("startTimeMillis")),
        "planned_min": round(float(session.get("duration") or 0) / 60000.0, 1),
        "presenter_join": min(joins) if joins else None,
        # Süren katılımda çıkış yok: okuyucu şimdiye kadar sayar.
        "presenter_leave": None if open_presence or not leaves else max(leaves),
        "customer_sec": round(customer_sec, 1),
        "participants": len(participants),
    }


def _zsoid() -> str:
    body = _get("user.json")
    zsoid = (body.get("userDetails") or {}).get("zsoid")
    if not zsoid:
        raise RuntimeError("Zoho Meeting kuruluş numarası (zsoid) alınamadı")
    return str(zsoid)


def _sessions_since(zsoid: str, since: datetime) -> list[dict[str, Any]]:
    """Geçmiş toplantılar, yeniden eskiye; since'ten eskiye inince durur."""
    out: list[dict[str, Any]] = []
    index = 1
    while True:
        page = _get(f"{zsoid}/sessions.json", {"listtype": "past", "index": index, "count": PAGE})
        rows = page.get("session") or []
        for row in rows:
            start = _ms(row.get("startTimeMillisec") or row.get("startTimeMillis"))
            if start is not None and start >= since:
                out.append(row)
        oldest = min(
            (s for s in (_ms(r.get("startTimeMillisec") or r.get("startTimeMillis")) for r in rows) if s),
            default=None,
        )
        if len(rows) < PAGE or oldest is None or oldest < since:
            return out
        index += PAGE


def _final_keys(conn: Any, keys: list[str]) -> set[str]:
    """Raporu kesinleşmiş (bitişten 30 dk sonra çekilmiş) oturumlar."""
    if not keys:
        return set()
    rows = conn.execute(
        """
        SELECT meeting_key FROM meeting_sessions
        WHERE org_id = %s AND meeting_key = ANY(%s)
          AND presenter_leave IS NOT NULL
          AND fetched_at > greatest(presenter_leave, planned_start
                + make_interval(mins => coalesce(planned_min, 0)::int)) + %s
        """,
        (get_org_id(), keys, FINAL_AFTER),
    ).fetchall()
    return {str(r[0]) for r in rows}


def sync_meetings(
    now: datetime, *, lookback: timedelta = timedelta(days=2), budget_sec: float | None = None
) -> dict[str, Any]:
    """Pencere içindeki toplantıların gerçek katılımını yazar."""
    if not enabled():
        return {"skipped": "token_yok"}
    started = time.monotonic()
    zsoid = _zsoid()
    sessions = _sessions_since(zsoid, now - lookback)
    report = {"sessions": len(sessions), "fetched": 0, "written": 0, "final_skipped": 0}
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        emails = {
            str(email).strip().lower(): str(rep_id)
            for rep_id, email in conn.execute(
                "SELECT rep_id, email FROM reps WHERE org_id = %s AND email IS NOT NULL",
                (get_org_id(),),
            ).fetchall()
        }
        final = _final_keys(conn, [str(s.get("meetingKey")) for s in sessions])
        for session in sessions:
            key = str(session.get("meetingKey"))
            if key in final:
                report["final_skipped"] += 1
                continue
            if budget_sec is not None and time.monotonic() - started > budget_sec:
                report["skipped"] = "sure_doldu"
                break
            body = _get(f"{zsoid}/participant/{key}.json", {"index": 1, "count": 100})
            report["fetched"] += 1
            row = summarize(session, body.get("participants") or [])
            if row["planned_start"] is None:
                continue
            conn.execute(
                """
                INSERT INTO meeting_sessions (
                    org_id, meeting_key, presenter_email, rep_id, topic, planned_start,
                    planned_min, presenter_join, presenter_leave, customer_sec,
                    participants, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (org_id, meeting_key) DO UPDATE SET
                    presenter_email = EXCLUDED.presenter_email,
                    rep_id = EXCLUDED.rep_id,
                    topic = EXCLUDED.topic,
                    planned_start = EXCLUDED.planned_start,
                    planned_min = EXCLUDED.planned_min,
                    presenter_join = EXCLUDED.presenter_join,
                    presenter_leave = EXCLUDED.presenter_leave,
                    customer_sec = EXCLUDED.customer_sec,
                    participants = EXCLUDED.participants,
                    fetched_at = now()
                """,
                (
                    get_org_id(),
                    row["meeting_key"],
                    row["presenter_email"],
                    emails.get(row["presenter_email"] or ""),
                    row["topic"],
                    row["planned_start"],
                    row["planned_min"],
                    row["presenter_join"],
                    row["presenter_leave"],
                    row["customer_sec"],
                    row["participants"],
                ),
            )
            conn.commit()
            report["written"] += 1
    report["seconds"] = round(time.monotonic() - started, 1)
    return report
