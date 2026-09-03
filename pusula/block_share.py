"""Panelden Cliq'e blok özeti. Yalnız butonla; gölge mod env'den."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from pusula.blocks import PLANNED_BLOCKS
from pusula.config import get_org_id
from pusula.panel_data import (
    Rep,
    connect,
    fmt_day,
    fmt_num,
    fmt_pct,
    today_blocks,
)

_TZ = ZoneInfo("Europe/Istanbul")
NUDGE_TYPE = "blok_ozeti"
TEAM_REP_ID = "team"
_CHANNEL = "cliq"


@dataclass(frozen=True)
class SalesRep:
    rep_id: str
    full_name: str
    email: str


@dataclass(frozen=True)
class ShareResult:
    sent_at: datetime
    body: str
    intended: str
    dest_is_shadow: bool
    repeat: bool


def shadow_email() -> str | None:
    """Doluysa tüm gönderimler bu adrese. Boş = canlı alıcı."""
    raw = os.environ.get("PUSULA_SHADOW_EMAIL")
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _webhook_url() -> str:
    raw = os.environ.get("CLIQ_WEBHOOK_URL")
    if not raw or not str(raw).strip():
        raise RuntimeError("CLIQ_WEBHOOK_URL yok")
    return str(raw).strip()


def _post_cliq(text: str, userids: str) -> None:
    payload = {"type": "message", "text": text, "userids": userids}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _webhook_url(),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _shadow_prefix(intended: str) -> str:
    return f"[golge] normalde {intended} adresine giderdi"


def apply_shadow(body: str, intended: str) -> tuple[str, str, bool]:
    """(metin, userids, gölge mi). Gölge açıksa asla gerçek alıcıya gitmez."""
    shadow = shadow_email()
    if shadow:
        return f"{_shadow_prefix(intended)}\n{body}", shadow, True
    return body, "", False


def load_sales_reps() -> list[SalesRep]:
    org_id = get_org_id()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rep_id, full_name, email
            FROM reps
            WHERE org_id = %s
              AND category = 'sales'
              AND active = true
            ORDER BY full_name
            """,
            (org_id,),
        ).fetchall()
    out: list[SalesRep] = []
    for row in rows:
        email = str(row[2]).strip() if row[2] else ""
        out.append(
            SalesRep(rep_id=str(row[0]), full_name=str(row[1]), email=email)
        )
    return out


def _block_lines(block: dict[str, Any]) -> list[str]:
    today = block.get("today") or {}
    return [
        str(block.get("label") or ""),
        f"giden arama {fmt_num(today.get('arama'))}",
        f"ulaşılan görüşme {fmt_num(today.get('ulasilan'))}",
        f"ulaşma oranı {fmt_pct(today.get('ulasma_orani'))}",
        f"randevu {fmt_num(today.get('randevu'))}",
    ]


def format_block_message(day: date, blocks: list[dict[str, Any]]) -> str:
    """Ekip veya tek kişi. İsim ve kıyas yok."""
    planned_keys = {item.key for item in PLANNED_BLOCKS}
    parts: list[str] = [fmt_day(day)]
    for block in blocks:
        if str(block.get("key")) not in planned_keys:
            continue
        parts.append("")
        parts.extend(_block_lines(block))
    return "\n".join(parts).strip()


def last_sent_at(*, rep_id: str, day: date) -> datetime | None:
    org_id = get_org_id()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT max(sent_at)
            FROM nudges
            WHERE org_id = %s
              AND rep_id = %s
              AND nudge_type = %s
              AND payload->>'day' = %s
            """,
            (org_id, rep_id, NUDGE_TYPE, day.isoformat()),
        ).fetchone()
    if not row or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_TZ)
        return value.astimezone(_TZ)
    return None


def _insert_send(
    *,
    rep_id: str,
    day: date,
    intended: str,
    body: str,
    dest_is_shadow: bool,
) -> datetime:
    org_id = get_org_id()
    payload: dict[str, Any] = {
        "day": day.isoformat(),
        "scope": "team" if rep_id == TEAM_REP_ID else "person",
        "intended": intended,
        "shadow": dest_is_shadow,
        "body": body,
    }
    sent_at = datetime.now(_TZ)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO nudges (
                org_id, rep_id, nudge_type, thread_id, lead_id,
                commitment_id, sent_at, channel, payload
            )
            VALUES (
                %s, %s, %s, %s, NULL,
                NULL, %s, %s, %s
            )
            """,
            (
                org_id,
                rep_id,
                NUDGE_TYPE,
                f"blok_ozeti:{day.isoformat()}:{rep_id}",
                sent_at,
                _CHANNEL,
                Json(payload),
            ),
        )
        conn.commit()
    return sent_at


def _send(
    *,
    rep_id: str,
    day: date,
    body: str,
    intended: str,
    live_userids: str,
) -> ShareResult:
    repeat = last_sent_at(rep_id=rep_id, day=day) is not None
    text, shadow_dest, dest_is_shadow = apply_shadow(body, intended)
    dest = shadow_dest if dest_is_shadow else live_userids
    if not dest:
        raise RuntimeError(f"Cliq alıcı yok: {intended}")
    _post_cliq(text, dest)
    sent_at = _insert_send(
        rep_id=rep_id,
        day=day,
        intended=intended,
        body=text,
        dest_is_shadow=dest_is_shadow,
    )
    return ShareResult(
        sent_at=sent_at,
        body=text,
        intended=intended,
        dest_is_shadow=dest_is_shadow,
        repeat=repeat,
    )


def send_team(day: date) -> ShareResult:
    data = today_blocks(None, day)
    body = format_block_message(day, list(data.get("blocks") or []))
    reps = load_sales_reps()
    emails = [item.email for item in reps if item.email]
    if not emails:
        raise RuntimeError("satış grubu: reps.email yok")
    return _send(
        rep_id=TEAM_REP_ID,
        day=day,
        body=body,
        intended="satış grubu",
        live_userids=",".join(emails),
    )


def send_person(rep: Rep | SalesRep, day: date) -> ShareResult:
    sales = {item.rep_id: item for item in load_sales_reps()}
    found = sales.get(rep.rep_id)
    if found is None:
        raise RuntimeError(f"reps eşlemesi yok: {rep.full_name}")
    if not found.email:
        raise RuntimeError(f"email yok: {found.full_name}")
    data = today_blocks(found.rep_id, day)
    body = format_block_message(day, list(data.get("blocks") or []))
    other_names = [
        item.full_name
        for item in sales.values()
        if item.rep_id != found.rep_id
    ]
    for name in other_names:
        if name and name in body:
            raise RuntimeError("kişi mesajında başka temsilci var")
    return _send(
        rep_id=found.rep_id,
        day=day,
        body=body,
        intended=found.full_name,
        live_userids=found.email,
    )


def fmt_sent_clock(value: datetime) -> str:
    local = value if value.tzinfo is not None else value.replace(tzinfo=_TZ)
    return local.astimezone(_TZ).strftime("%H:%M")


def webhook_host() -> str:
    """Doğrulama çıktısı için host; sır sızdırmaz."""
    try:
        parsed = urlparse(_webhook_url())
    except RuntimeError:
        return "yok"
    return parsed.netloc or "yok"
