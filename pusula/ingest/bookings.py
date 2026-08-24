"""Zoho Bookings randevuları → events (channel=meeting).

Kaynak fetchappointment. CRM Events / Zoho Flow değil: durum
güncellemesi (katıldı / katılmadı / iptal) Bookings'te kalıyor.

Yazılmayan kayıtlar: @rexven.com (iç hesap), satış ekibi dışındaki
staff (mentor ve diğerleri), e-posta ile lead'e bağlanamayanlar.
Yeni thread açılmaz.

Pencere: son 90 gün (watermark yok sayılır; durum güncellemesi
kaçmasın). --since 90 günden eskiyse pencere o tarihe uzar.
Idempotent: UNIQUE (org_id, channel, source_ref) + meta upsert.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import normalize_email
from pusula.db.models import Event
from pusula.ingest.base import Ingester, IngestError, RawRecord, to_istanbul
from pusula.ingest.registry import register
from pusula.zoho.auth import get_access_token, get_api_domain

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 90
_PER_PAGE = 100
_MAX_PAGES = 200
_HTTP_TIMEOUT = httpx.Timeout(90.0, connect=15.0)

# strftime %b yerel aya bağlı; Bookings İngilizce kısaltma ister.
_EN_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

# Satış ekibi — Bookings staff_name / reps.full_name, Türkçe katlamalı.
_SALES_NAME_KEYS = frozenset(
    {
        "serkan sahin",
        "miray aksel",
        "ayse kar",
        "abdullah benli",
    }
)

_INTERNAL_EMAIL_DOMAIN = "rexven.com"

# API status → meta.randevu_durumu (değerler birebir bu haliyle yazılır).
_STATUS_MAP: dict[str, str] = {
    "completed": "katildi",
    "no_show": "katilmadi",
    "cancel": "iptal_edildi",
    "cancelled": "iptal_edildi",
}

_TR_FOLD = str.maketrans(
    {
        "ç": "c",
        "ğ": "g",
        "ı": "i",
        "ö": "o",
        "ş": "s",
        "ü": "u",
        "Ç": "c",
        "Ğ": "g",
        "İ": "i",
        "I": "i",
        "Ö": "o",
        "Ş": "s",
        "Ü": "u",
    }
)


class BookingsError(IngestError):
    """Bookings okuma isteği kalıcı olarak başarısız."""


@register
class BookingsIngester(Ingester):
    """Bookings randevularını meeting event olarak yazar."""

    source_name = "zoho_bookings"
    channel = "meeting"

    def __init__(self) -> None:
        self.last_skip_reason: str | None = None
        self.last_skip_sample: dict[str, Any] | None = None
        self.fetch_limit: int | None = None
        self.fetch_truncated = False
        # full_name katlaması → rep_id (yalnız satış dörtlüsü).
        self._sales_reps: dict[str, str] | None = None
        # identities.email (normalize) → lead'i olan thread var.
        self._lead_emails: set[str] | None = None

    def fetch(self, since: datetime | None) -> Iterator[RawRecord]:
        """90 günlük randevuları çeker; occurred_at artan sırada verir.

        since watermark olarak kullanılmaz (durum güncellemesi).
        90 günden eski --since pencereyi geriye uzatır.
        """
        self._sales_reps = _load_sales_reps()
        self._lead_emails = _load_lead_emails()

        now = datetime.now(ZoneInfo("Europe/Istanbul"))
        window_start = now - timedelta(days=LOOKBACK_DAYS)
        if since is not None and since < window_start:
            window_start = since
        window_end = now

        from_s = _format_bookings_time(window_start.replace(hour=0, minute=0, second=0))
        to_s = _format_bookings_time(window_end.replace(hour=23, minute=59, second=59))
        logger.info(
            "zoho_bookings pencere %s -> %s",
            from_s,
            to_s,
        )

        rows = _fetch_all_appointments(from_s, to_s)
        records: list[RawRecord] = []
        for payload in rows:
            booking_id = _as_str(payload.get("booking_id"))
            if not booking_id:
                logger.debug("Bookings kaydı booking_id eksik, atlandı")
                continue
            occurred_at = _parse_start(payload)
            if occurred_at is None:
                logger.debug("Bookings %s: baslangic zamani yok, atlandı", booking_id)
                continue
            records.append(
                RawRecord(
                    source_ref=booking_id,
                    occurred_at=occurred_at,
                    payload=payload,
                )
            )

        records.sort(key=lambda item: (item.occurred_at, item.source_ref))
        yielded = 0
        for raw in records:
            yield raw
            yielded += 1
            if self.fetch_limit is not None and yielded >= self.fetch_limit:
                break

    def to_event(self, raw: RawRecord) -> Event | None:
        payload = raw.payload
        email_raw = _as_str(payload.get("customer_email"))
        email = normalize_email(email_raw) if email_raw else None

        if email is not None and email.endswith(f"@{_INTERNAL_EMAIL_DOMAIN}"):
            return self._skip("ic_hesap", payload)

        staff_name = _as_str(payload.get("staff_name")) or ""
        sales_reps = self._sales_reps if self._sales_reps is not None else _load_sales_reps()
        self._sales_reps = sales_reps
        rep_id = sales_reps.get(_name_key(staff_name))
        if rep_id is None:
            return self._skip("mentor", payload)

        lead_emails = (
            self._lead_emails if self._lead_emails is not None else _load_lead_emails()
        )
        self._lead_emails = lead_emails
        if email is None or email not in lead_emails:
            return self._skip("lead_yok", payload)
        if client.is_identifier_blocked("email", email):
            return self._skip("lead_yok", payload)

        return Event(
            channel="meeting",
            direction="outbound",
            rep_id=rep_id,
            occurred_at=raw.occurred_at,
            source_ref=raw.source_ref,
            body=None,
            body_quality="low",
            meta=_build_meta(payload, staff_name),
            email=email,
        )

    def _skip(self, reason: str, payload: dict[str, Any]) -> Event | None:
        self.last_skip_reason = reason
        self.last_skip_sample = {
            "booking_id": payload.get("booking_id"),
            "staff_name": payload.get("staff_name"),
            "status": payload.get("status"),
            "reason": reason,
        }
        return None


def _build_meta(payload: dict[str, Any], staff_name: str) -> dict[str, Any]:
    """Türkçe anahtarlar birebir: randevu_durumu ve kimlik alanları."""
    return {
        "randevu_durumu": _map_status(payload.get("status")),
        "staff_name": staff_name,
        "customer_email": _as_str(payload.get("customer_email")),
        "customer_phone": _as_str(payload.get("customer_contact_no")),
        "duration": payload.get("duration"),
        "booking_id": _as_str(payload.get("booking_id")),
        "service_name": _as_str(payload.get("service_name")),
    }


def _map_status(raw: Any) -> str:
    text = str(raw).strip().casefold() if raw is not None else ""
    return _STATUS_MAP.get(text, "sonuc_girilmedi")


def _name_key(value: str) -> str:
    folded = value.translate(_TR_FOLD)
    return " ".join(folded.split()).casefold()


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _format_bookings_time(value: datetime) -> str:
    local = to_istanbul(value)
    return (
        f"{local.day:02d}-{_EN_MONTHS[local.month - 1]}-{local.year} "
        f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}"
    )


def _parse_start(payload: dict[str, Any]) -> datetime | None:
    for key in ("iso_start_time", "start_time"):
        parsed = _parse_dt(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_dt(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return to_istanbul(dt)
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return to_istanbul(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _load_sales_reps() -> dict[str, str]:
    """Satış dörtlüsünün name_key → rep_id eşlemesi."""
    query = """
        SELECT rep_id, full_name FROM reps
        WHERE org_id = %s AND active = TRUE
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(),)).fetchall()
    result: dict[str, str] = {}
    for rep_id, full_name in rows:
        if not full_name:
            continue
        key = _name_key(str(full_name))
        if key in _SALES_NAME_KEYS:
            result[key] = str(rep_id)
    missing = _SALES_NAME_KEYS - set(result)
    if missing:
        logger.warning("zoho_bookings: reps tablosunda bulunamayan satis isimleri: %s", sorted(missing))
    return result


def _load_lead_emails() -> set[str]:
    """Lead'e bağlı thread'lerdeki e-posta kimlikleri."""
    query = """
        SELECT i.id_value
        FROM identities i
        INNER JOIN leads l
          ON l.org_id = i.org_id
         AND l.thread_id = i.thread_id
        WHERE i.org_id = %s
          AND i.id_type = 'email'
          AND i.thread_id IS NOT NULL
    """
    with client.transaction() as conn:
        rows = conn.execute(query, (get_org_id(),)).fetchall()
    emails: set[str] = set()
    for (raw,) in rows:
        normalized = normalize_email(str(raw)) if raw else None
        if normalized is not None:
            emails.add(normalized)
    return emails


def _extract_appointment_list(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    resp = payload.get("response") or payload
    rv = resp.get("returnvalue") if isinstance(resp, dict) else {}
    if not isinstance(rv, dict):
        rv = {}
    rows = rv.get("response") or rv.get("data") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        rows = []
    more = bool(rv.get("next_page_available"))
    return [row for row in rows if isinstance(row, dict)], more


def _fetch_all_appointments(from_s: str, to_s: str) -> list[dict[str, Any]]:
    """fetchappointment sayfalarını toplar. Token basılmaz."""
    collected: list[dict[str, Any]] = []
    page = 1
    while page <= _MAX_PAGES:
        body = _fetch_page(from_s, to_s, page)
        rows, more = _extract_appointment_list(body)
        collected.extend(rows)
        logger.info(
            "zoho_bookings sayfa %s: %s kayit (toplam %s) next=%s",
            page,
            len(rows),
            len(collected),
            more,
        )
        if not more or not rows:
            break
        page += 1
    else:
        raise BookingsError(
            f"zoho_bookings: sayfa limiti asildi ({_MAX_PAGES})"
        )
    return collected


def _fetch_page(from_s: str, to_s: str, page: int) -> dict[str, Any]:
    url = get_api_domain() + "/bookings/v1/json/fetchappointment"
    data = {
        "data": json.dumps(
            {
                "from_time": from_s,
                "to_time": to_s,
                "page": page,
                "per_page": _PER_PAGE,
            }
        )
    }
    auth_retried = False
    force_refresh = False
    while True:
        headers = {
            "Authorization": f"Zoho-oauthtoken {get_access_token(force_refresh=force_refresh)}"
        }
        force_refresh = False
        try:
            response = httpx.post(
                url,
                headers=headers,
                data=data,
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise BookingsError(
                f"fetchappointment sayfa {page} gonderilemedi: {exc}"
            ) from exc
        if response.status_code == 401 and not auth_retried:
            auth_retried = True
            force_refresh = True
            continue
        if response.status_code != 200:
            raise BookingsError(
                f"fetchappointment sayfa {page} HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise BookingsError(f"fetchappointment sayfa {page}: govde sozluk degil")
        return payload
