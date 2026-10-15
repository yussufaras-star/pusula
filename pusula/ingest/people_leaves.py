"""Zoho People izinleri → rep_leaves (kaynak 'people').

Yönetici kararı (15 Ekim): her izin türü sayılır, yalnız onaylı
(ApprovalStatus = Approved) ve yalnız tam gün (gün başına LeaveCount ≥ 1).
Yarım gün ve saatlik izin alınmaz; gerekirse panelden muaf süre girilir.

Temsilci eşleşmesi ad soyad ile (Türkçe katlamalı, Bookings'teki gibi).
Pencere her çalışmada yeniden kurulur: People'da iptal / reddedilen izin
Pusula'dan da silinir. Panelden elle işaretlenen gün (kaynak 'panel')
korunur, senkron ona dokunmaz.

Yetki ayrı refresh token'da (ZOHO_PEOPLE_REFRESH_TOKEN; kapsam
ZohoPeople.leave.READ, ZohoPeople.forms.READ). Değişken yoksa atlanır.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from pusula.config import get_org_id
from pusula.ingest.bookings import _name_key
from pusula.leaves import SCHEMA_SQL
from pusula.panel_data import connect

logger = logging.getLogger(__name__)

ENV_TOKEN = "ZOHO_PEOPLE_REFRESH_TOKEN"
URL = "https://people.zoho.com/people/api/v2/leavetracker/leaves/records"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_DATE_FMT = "%d-%b-%Y"
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Tek istekte bu kadar gün (sayfalama belirsiz; küçük parçalar güvenli).
CHUNK_DAYS = 31


def enabled() -> bool:
    return bool(os.environ.get(ENV_TOKEN))


def _token() -> str:
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
        raise RuntimeError(f"Zoho People token yenilenemedi (HTTP {resp.status_code})")
    return str(body["access_token"])


def _fmt(day: date) -> str:
    return f"{day.day:02d}-{_EN_MONTHS[day.month - 1]}-{day.year}"


def _parse_day(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), _DATE_FMT).date()
    except (ValueError, AttributeError):
        return None


def full_days(records: dict[str, Any], reps_by_name: dict[str, str]) -> tuple[set[tuple[str, date]], set[str]]:
    """Saf: People kayıtları → (rep_id, gün) tam gün onaylı izinler + eşleşmeyen adlar."""
    out: set[tuple[str, date]] = set()
    unmatched: set[str] = set()
    for rec in records.values():
        if not isinstance(rec, dict) or str(rec.get("ApprovalStatus")).casefold() != "approved":
            continue
        if str(rec.get("Unit") or "Days").casefold() != "days":
            continue
        name = str(rec.get("Employee") or "")
        rep_id = reps_by_name.get(_name_key(name))
        if rep_id is None:
            unmatched.add(name)
            continue
        for raw_day, info in (rec.get("Days") or {}).items():
            try:
                count = float((info or {}).get("LeaveCount") or 0)
            except (TypeError, ValueError):
                continue
            day = _parse_day(raw_day)
            if day is not None and count >= 1.0:
                out.add((rep_id, day))
    return out, unmatched


def _fetch(token: str, start: date, end: date) -> dict[str, Any]:
    records: dict[str, Any] = {}
    cursor = start
    while cursor <= end:
        upto = min(cursor + timedelta(days=CHUNK_DAYS - 1), end)
        for attempt in range(3):
            resp = httpx.get(
                URL,
                headers={"Authorization": f"Zoho-oauthtoken {token}"},
                params={"from": _fmt(cursor), "to": _fmt(upto), "dateFormat": "dd-MMM-yyyy"},
                timeout=_TIMEOUT,
            )
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            break
        body = resp.json()
        found = body.get("records") if isinstance(body, dict) else None
        if isinstance(found, dict):
            records.update(found)
        cursor = upto + timedelta(days=1)
    return records


def sync_people_leaves(
    today: date, *, back_days: int = 60, ahead_days: int = 60
) -> dict[str, Any]:
    if not enabled():
        return {"skipped": "token_yok"}
    start, end = today - timedelta(days=back_days), today + timedelta(days=ahead_days)
    records = _fetch(_token(), start, end)
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        conn.execute("ALTER TABLE rep_leaves ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'panel'")
        reps = {
            _name_key(str(name)): str(rep_id)
            for rep_id, name in conn.execute(
                "SELECT rep_id, full_name FROM reps WHERE org_id = %s AND full_name IS NOT NULL",
                (get_org_id(),),
            ).fetchall()
        }
        wanted, unmatched = full_days(records, reps)
        wanted = {(rep_id, day) for rep_id, day in wanted if start <= day <= end}
        existing = {
            (str(rep_id), day)
            for rep_id, day in conn.execute(
                """
                SELECT rep_id, day FROM rep_leaves
                WHERE org_id = %s AND source = 'people' AND day BETWEEN %s AND %s
                """,
                (get_org_id(), start, end),
            ).fetchall()
        }
        added = removed = 0
        for rep_id, day in sorted(wanted - existing):
            cur = conn.execute(
                """
                INSERT INTO rep_leaves (org_id, rep_id, day, created_by, source)
                VALUES (%s, %s, %s, 'zoho_people', 'people')
                ON CONFLICT (org_id, rep_id, day) DO NOTHING
                """,
                (get_org_id(), rep_id, day),
            )
            added += cur.rowcount
        for rep_id, day in sorted(existing - wanted):
            conn.execute(
                "DELETE FROM rep_leaves WHERE org_id = %s AND rep_id = %s AND day = %s AND source = 'people'",
                (get_org_id(), rep_id, day),
            )
            removed += 1
    if unmatched:
        logger.info("People: temsilciyle eşleşmeyen çalışanlar: %s", sorted(unmatched))
    return {
        "records": len(records),
        "leave_days": len(wanted),
        "added": added,
        "removed": removed,
        "unmatched": sorted(unmatched),
    }
