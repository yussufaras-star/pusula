"""Zoho Meeting oturum süresi → katılınan Bookings kaydı.

Liste API'sindeki duration planlanan penceredir (ör. 3600000 ms = 1 saat).
Gerçek süre meetings.zoho.com katılımcı raporundaki giriş-çıkıştır
(leaveTime - joinTime). duration alanı bu pencereyle aynıysa yazılmaz.
Aynı e-postanın yeniden girişleri toplanır, milisaniye saniyeye yuvarlanır.

Yalnız satış ekibinin sunduğu ve bir katildi randevuya (±90 dk)
eşleşen oturum yazılır. Eşleşmeyen iç toplantı süreye girmez.
Yeni event açılmaz: süre, randevunun meta.actual_duration_sec
alanına yazılır. Bookings yeniden çekerken bu alanı silmez.

Scope: ZohoMeeting.meeting.READ. zsoid: ZOHO_MEETING_ZSOID.
Yoksa user.json denenir (ZohoMeeting.manageOrg.READ). İkisi de
yoksa kayıt atlanır, watermark ilerlemez, saatlik iş düşmez.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_email
from pusula.db.models import Event
from pusula.ingest.base import Ingester, IngestError, RawRecord, to_istanbul
from pusula.ingest.registry import register
from pusula.zoho.auth import ZohoAuthError, get_access_token

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 7
MATCH_SLACK = timedelta(minutes=90)
_PAGE_SIZE = 50
_MAX_PAGES = 20
_PARTICIPANT_PAGE = 100
_HTTP_TIMEOUT = httpx.Timeout(90.0, connect=15.0)
_DEFAULT_BASE = "https://meeting.zoho.com"
_ZSOURCE = "Rexven"

_START_RE = re.compile(
    r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)",
    re.IGNORECASE,
)

_DIRECTIONS = frozenset({"inbound", "outbound", "internal"})
_QUALITIES = frozenset({"low", "medium", "high"})


class MeetingRequestError(IngestError):
    """Meeting API okunamadı. Mesaj token içermez."""


@dataclass(frozen=True)
class SessionSpan:
    """Satış temsilcisinin oturumda kaldığı süre."""

    meeting_key: str
    rep_id: str
    started_at: datetime
    duration_sec: int


@dataclass(frozen=True)
class BookingSlot:
    """Katıldı işaretli randevu."""

    source_ref: str
    rep_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class DurationMatch:
    """Randevuya yazılacak gerçekleşen süre."""

    source_ref: str
    occurred_at: datetime
    duration_sec: int
    meeting_key: str


@register
class MeetingDurationIngester(Ingester):
    """Katıldı randevularına Zoho Meeting oturum süresini yazar."""

    source_name = "zoho_meeting"
    channel = "meeting"

    def __init__(self) -> None:
        self.last_skip_reason: str | None = None
        self.last_skip_sample: dict[str, Any] | None = None
        self.fetch_limit: int | None = None
        self.fetch_truncated = False
        self.lookback: timedelta | None = None

    def fetch(self, since: datetime | None) -> Iterator[RawRecord]:
        """Penceredeki eşleşen süreleri occurred_at artan sırada verir.

        since watermark olarak kullanılmaz. 7 günden eski --since
        pencereyi geriye uzatır. API yoksa boş döner, watermark ilerlemez.
        """
        self.fetch_truncated = False
        try:
            matches = _collect_matches(since, self.lookback)
        except MeetingRequestError as exc:
            logger.warning("zoho_meeting atlandi: %s", exc)
            self.fetch_truncated = True
            return

        matches.sort(key=lambda item: (item.occurred_at, item.source_ref))
        yielded = 0
        for match in matches:
            yield RawRecord(
                source_ref=match.source_ref,
                occurred_at=match.occurred_at,
                payload={
                    "actual_duration_sec": match.duration_sec,
                    "meeting_key": match.meeting_key,
                },
            )
            yielded += 1
            if self.fetch_limit is not None and yielded >= self.fetch_limit:
                self.fetch_truncated = True
                break

    def to_event(self, raw: RawRecord) -> Event | None:
        existing = _load_booking(raw.source_ref)
        if existing is None:
            return self._skip("booking_yok", raw.source_ref)
        if existing["randevu_durumu"] != "katildi":
            return self._skip("katilmadi", raw.source_ref)

        meta = dict(existing["meta"])
        duration = raw.payload.get("actual_duration_sec")
        meeting_key = raw.payload.get("meeting_key")
        if not isinstance(duration, int) or duration <= 0:
            return self._skip("sure_yok", raw.source_ref)
        if not isinstance(meeting_key, str) or not meeting_key:
            return self._skip("anahtar_yok", raw.source_ref)
        meta["actual_duration_sec"] = duration
        meta["meeting_key"] = meeting_key

        direction = existing["direction"]
        if direction not in _DIRECTIONS:
            direction = "outbound"
        quality = existing["body_quality"]
        if quality not in _QUALITIES:
            quality = "low"
        occurred = existing["occurred_at"] or raw.occurred_at
        return Event(
            channel="meeting",
            direction=direction,
            rep_id=existing["rep_id"],
            occurred_at=occurred,
            source_ref=raw.source_ref,
            body=existing["body"],
            body_quality=quality,
            meta=meta,
        )

    def _skip(self, reason: str, source_ref: str) -> None:
        self.last_skip_reason = reason
        self.last_skip_sample = {"source_ref": source_ref, "reason": reason}
        return None


def duration_sec_from_ms(raw: Any) -> int | None:
    """Milisaniye → saniye. 82790 → 83. 3600000 → 3600.

    Sıfır ve negatif yok sayılır. Saniye sanılıp bölünmez.
    """
    ms = _as_ms(raw)
    if ms is None or ms <= 0:
        return None
    return int(round(ms / 1000.0))


def attendance_ms(row: dict[str, Any], scheduled_ms: int | None = None) -> int | None:
    """Katılımcının oturumda kaldığı milisaniye.

    meetings.zoho.com raporu joinTime ve leaveTime gösterir.
    duration alanı planlanan pencereyle aynıysa gerçekleşen süre değildir.
    """
    join_ms = _as_ms(row.get("joinTime"))
    leave_ms = _as_ms(row.get("leaveTime"))
    if join_ms is not None and leave_ms is not None and leave_ms > join_ms:
        return leave_ms - join_ms
    raw = _as_ms(row.get("duration"))
    if raw is None or raw <= 0:
        return None
    if scheduled_ms is not None and abs(raw - scheduled_ms) <= 1000:
        return None
    return raw


def rep_duration_sec(
    participants: list[dict[str, Any]],
    email: str,
    scheduled_ms: int | None = None,
) -> int | None:
    """Temsilcinin oturumda kaldığı saniye. Yeniden girişler toplanır.

    Önce e-posta. E-posta boşsa ve oturumun sunucusu bu temsilciyse
    role=presenter satırları. Başka katılımcının süresi eklenmez.
    scheduled_ms, liste API'sindeki planlanan penceredir.
    """
    target = normalize_email(email)
    if target is None:
        return None
    matched, by_email = _sum_ms(
        participants, email=target, role=None, scheduled_ms=scheduled_ms
    )
    if matched:
        return duration_sec_from_ms(by_email)
    matched_role, by_role = _sum_ms(
        participants, email=None, role="presenter", scheduled_ms=scheduled_ms
    )
    if not matched_role:
        return None
    return duration_sec_from_ms(by_role)


def session_start(payload: dict[str, Any]) -> datetime | None:
    """startTimeMillisec, yoksa startTime + timezone. Istanbul'a çevrilir."""
    for key in ("startTimeMillisec", "startTimeMillis"):
        ms = _as_ms(payload.get(key))
        if ms is not None and ms > 0:
            return datetime.fromtimestamp(ms / 1000.0, tz=ZoneInfo("Europe/Istanbul"))
    raw = payload.get("startTime")
    if not isinstance(raw, str):
        return None
    match = _START_RE.search(raw.strip())
    if match is None:
        return None
    try:
        naive = datetime.strptime(match.group(1), "%b %d, %Y %I:%M %p")
    except ValueError:
        return None
    tz_name = payload.get("timezone")
    tz = ZoneInfo("Europe/Istanbul")
    if isinstance(tz_name, str) and tz_name.strip():
        try:
            tz = ZoneInfo(tz_name.strip())
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("Europe/Istanbul")
    return to_istanbul(naive.replace(tzinfo=tz))


def extract_session_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("session")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def extract_participants(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("participants")
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def sales_rep_for_email(email: Any, sales: dict[str, str]) -> str | None:
    if not isinstance(email, str):
        return None
    normalized = normalize_email(email)
    if normalized is None:
        return None
    return sales.get(normalized)


def match_durations(
    sessions: list[SessionSpan],
    bookings: list[BookingSlot],
    slack: timedelta = MATCH_SLACK,
) -> list[DurationMatch]:
    """Aynı temsilci, en yakın başlangıç, ±slack. Bire bir."""
    limit = slack.total_seconds()
    pairs: list[tuple[float, int, int]] = []
    for si, session in enumerate(sessions):
        for bi, booking in enumerate(bookings):
            if session.rep_id != booking.rep_id:
                continue
            delta = abs((session.started_at - booking.occurred_at).total_seconds())
            if delta <= limit:
                pairs.append((delta, si, bi))
    pairs.sort()
    used_sessions: set[int] = set()
    used_bookings: set[int] = set()
    matched: list[DurationMatch] = []
    for _delta, si, bi in pairs:
        if si in used_sessions or bi in used_bookings:
            continue
        used_sessions.add(si)
        used_bookings.add(bi)
        session = sessions[si]
        booking = bookings[bi]
        matched.append(
            DurationMatch(
                source_ref=booking.source_ref,
                occurred_at=booking.occurred_at,
                duration_sec=session.duration_sec,
                meeting_key=session.meeting_key,
            )
        )
    return matched


def _sum_ms(
    participants: list[dict[str, Any]],
    *,
    email: str | None,
    role: str | None,
    scheduled_ms: int | None = None,
) -> tuple[bool, int]:
    """(satır eşleşti mi, pozitif milisaniye toplamı).

    E-posta eşleşip süre 0 ise (True, 0). Başka sunucuya düşülmez.
    """
    total = 0
    seen = False
    for row in participants:
        if email is not None:
            row_email = normalize_email(str(row.get("email") or ""))
            if row_email != email:
                continue
        if role is not None:
            row_role = str(row.get("role") or "").strip().casefold()
            if row_role != role:
                continue
        seen = True
        ms = attendance_ms(row, scheduled_ms)
        if ms is None or ms <= 0:
            continue
        total += ms
    return seen, total


def _as_ms(raw: Any) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _meeting_base() -> str:
    domain = os.environ.get("ZOHO_MEETING_API_DOMAIN", "").strip()
    return (domain or _DEFAULT_BASE).rstrip("/")


def _collect_matches(
    since: datetime | None, lookback: timedelta | None
) -> list[DurationMatch]:
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    window = lookback if lookback is not None else timedelta(days=LOOKBACK_DAYS)
    window_start = now - window
    if since is not None and to_istanbul(since) < window_start:
        window_start = to_istanbul(since)
    window_end = now

    sales = _load_sales_emails()
    if not sales:
        logger.warning("zoho_meeting: satis ekibinde e-posta yok")
        return []

    zsoid = _resolve_zsoid()
    sessions = _sessions_in_window(zsoid, sales, window_start, window_end)
    if not sessions:
        logger.info(
            "zoho_meeting pencere %s -> %s oturum=0",
            window_start.isoformat(timespec="seconds"),
            window_end.isoformat(timespec="seconds"),
        )
        return []

    slack = MATCH_SLACK
    bookings = _load_attended(window_start - slack, window_end + slack)
    matches = match_durations(sessions, bookings, slack)
    logger.info(
        "zoho_meeting pencere %s -> %s oturum=%s eslesen=%s",
        window_start.isoformat(timespec="seconds"),
        window_end.isoformat(timespec="seconds"),
        len(sessions),
        len(matches),
    )
    return matches


def _resolve_zsoid() -> str:
    configured = os.environ.get("ZOHO_MEETING_ZSOID", "").strip()
    if configured:
        return configured
    payload = _get_json(f"{_meeting_base()}/api/v2/user.json", None)
    details = payload.get("userDetails")
    zsoid = details.get("zsoid") if isinstance(details, dict) else None
    if zsoid is None or str(zsoid).strip() == "":
        raise MeetingRequestError(
            "ZOHO_MEETING_ZSOID yok ve user.json zsoid vermedi"
        )
    return str(zsoid).strip()


def _sessions_in_window(
    zsoid: str,
    sales: dict[str, str],
    window_start: datetime,
    window_end: datetime,
) -> list[SessionSpan]:
    found: list[SessionSpan] = []
    seen: set[str] = set()
    index = 0
    for page in range(1, _MAX_PAGES + 1):
        payload = _get_json(
            f"{_meeting_base()}/api/v2/{zsoid}/sessions.json",
            {"listtype": "past", "index": index, "count": _PAGE_SIZE},
        )
        rows = extract_session_rows(payload)
        if not rows:
            break
        fresh = 0
        starts: list[datetime] = []
        for row in rows:
            key = _meeting_key(row)
            if key is None or key in seen:
                continue
            seen.add(key)
            fresh += 1
            started = session_start(row)
            if started is not None:
                starts.append(started)
            span = _span_for_row(zsoid, row, key, started, sales, window_start, window_end)
            if span is not None:
                found.append(span)
        logger.info(
            "zoho_meeting sayfa %s: %s kayit (yeni %s, pencerede %s)",
            page,
            len(rows),
            fresh,
            len(found),
        )
        if fresh == 0 or len(rows) < _PAGE_SIZE:
            break
        if _page_before_window(starts, window_start):
            break
        index += len(rows)
    else:
        logger.warning("zoho_meeting: sayfa limiti asildi (%s)", _MAX_PAGES)
    return found


def _span_for_row(
    zsoid: str,
    row: dict[str, Any],
    key: str,
    started: datetime | None,
    sales: dict[str, str],
    window_start: datetime,
    window_end: datetime,
) -> SessionSpan | None:
    if started is None or started < window_start or started > window_end:
        return None
    rep_id = sales_rep_for_email(row.get("presenterEmail"), sales)
    presenter = row.get("presenterEmail")
    if rep_id is None or not isinstance(presenter, str):
        return None
    try:
        participants = _participant_rows(zsoid, key)
    except MeetingRequestError as exc:
        logger.warning("zoho_meeting %s katilimci okunamadi: %s", key, exc)
        return None
    seconds = rep_duration_sec(
        participants, presenter, _as_ms(row.get("duration"))
    )
    if seconds is None or seconds <= 0:
        return None
    return SessionSpan(
        meeting_key=key,
        rep_id=rep_id,
        started_at=started,
        duration_sec=seconds,
    )


def _page_before_window(starts: list[datetime], window_start: datetime) -> bool:
    """Yeniden eskiye sayfada en yeni kayıt pencerenin öncesindeyse dur.

    Artan sırada (eskiden yeniye) durulmaz; sonraki sayfa pencereye girebilir.
    """
    if len(starts) < 2:
        return False
    if starts[0] < starts[-1]:
        return False
    return max(starts) < window_start


def _participant_rows(zsoid: str, meeting_key: str) -> list[dict[str, Any]]:
    """Belgede sayfa 1. Boş veya reddedilirse 0 bir kez denenir."""
    try:
        rows = _participant_page(zsoid, meeting_key, 1)
    except MeetingRequestError:
        rows = []
    if rows:
        return rows
    try:
        return _participant_page(zsoid, meeting_key, 0)
    except MeetingRequestError as exc:
        logger.warning(
            "zoho_meeting %s katilimci sayfasi yok: %s", meeting_key, exc
        )
        return []


def _participant_page(
    zsoid: str, meeting_key: str, start_index: int
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    index = start_index
    for _page in range(5):
        payload = _get_json(
            f"{_meeting_base()}/api/v2/{zsoid}/participant/{meeting_key}.json",
            {"index": index, "count": _PARTICIPANT_PAGE},
        )
        rows = extract_participants(payload)
        collected.extend(rows)
        if len(rows) < _PARTICIPANT_PAGE:
            break
        index += 1
    return collected


def _meeting_key(row: dict[str, Any]) -> str | None:
    raw = row.get("meetingKey")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _get_json(url: str, params: dict[str, Any] | None) -> dict[str, Any]:
    auth_retried = False
    force_refresh = False
    while True:
        try:
            token = get_access_token(force_refresh=force_refresh)
        except ZohoAuthError as exc:
            raise MeetingRequestError(f"meeting token alinamadi: {exc}") from exc
        force_refresh = False
        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "X-ZSOURCE": _ZSOURCE,
        }
        try:
            response = httpx.get(
                url, headers=headers, params=params, timeout=_HTTP_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise MeetingRequestError(f"meeting istegi ulasamadi: {exc}") from exc
        if response.status_code == 401 and not auth_retried:
            auth_retried = True
            force_refresh = True
            continue
        if response.status_code != 200:
            raise MeetingRequestError(f"meeting HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MeetingRequestError("meeting govdesi json degil") from exc
        if not isinstance(payload, dict):
            raise MeetingRequestError("meeting govdesi sozluk degil")
        if "error" in payload:
            raise MeetingRequestError("meeting istegi reddedildi")
        return payload


def _load_sales_emails() -> dict[str, str]:
    """normalize e-posta → rep_id. Yalnız satış ekibi."""
    from pusula.panel_ciro import SALES_TEAM_IDS

    query = """
        SELECT rep_id, email FROM reps
        WHERE org_id = %s AND active = TRUE AND rep_id = ANY(%s)
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(), list(SALES_TEAM_IDS))).fetchall()
    result: dict[str, str] = {}
    for rep_id, email in rows:
        normalized = normalize_email(str(email)) if email else None
        if normalized is None:
            continue
        result[normalized] = str(rep_id)
    return result


def _load_attended(start: datetime, end: datetime) -> list[BookingSlot]:
    query = """
        SELECT source_ref, rep_id, occurred_at
        FROM events
        WHERE org_id = %s
          AND channel = 'meeting'
          AND meta->>'randevu_durumu' = 'katildi'
          AND occurred_at >= %s
          AND occurred_at <= %s
          AND source_ref IS NOT NULL
          AND rep_id IS NOT NULL
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(), start, end)).fetchall()
    slots: list[BookingSlot] = []
    for source_ref, rep_id, occurred_at in rows:
        if source_ref is None or rep_id is None or occurred_at is None:
            continue
        slots.append(
            BookingSlot(
                source_ref=str(source_ref),
                rep_id=str(rep_id),
                occurred_at=to_istanbul(occurred_at),
            )
        )
    return slots


def _load_booking(source_ref: str) -> dict[str, Any] | None:
    query = """
        SELECT rep_id, occurred_at, direction, body, body_quality, meta
        FROM events
        WHERE org_id = %s AND channel = 'meeting' AND source_ref = %s
    """
    with client.transaction() as conn:
        row = conn.execute(query, (get_org_id(), source_ref)).fetchone()
    if row is None:
        return None
    meta = row[5] if isinstance(row[5], dict) else {}
    occurred = row[1]
    return {
        "rep_id": str(row[0]) if row[0] is not None else None,
        "occurred_at": to_istanbul(occurred) if isinstance(occurred, datetime) else None,
        "direction": row[2] if isinstance(row[2], str) else None,
        "body": row[3] if isinstance(row[3], str) else None,
        "body_quality": row[4] if isinstance(row[4], str) else None,
        "randevu_durumu": meta.get("randevu_durumu"),
        "meta": meta,
    }

