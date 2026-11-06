"""Panel kullanımı: kim ne zaman girdi, panelde ne kadar aktif zaman geçirdi.

Giriş: başarılı her girişte panel_logins'e satır. Süre: açık ve kullanılan
sekme dakikada bir sinyal gönderir (panel_sessions). Sekme arkadaysa ya da kişi
5 dk'dır fareye / klavyeye dokunmadıysa sinyal gitmez; açık unutulan sekme süre
yazmaz. Sinyaller arası 5 dk'dan uzun boşluk yeni oturum başlatır. Yöneticinin
"temsilci gibi gör" görünümü kaydedilmez.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.panel_data import connect

# Sinyal aralığı 60 sn; gecikmeli sinyal bundan fazla süre yazmaz.
MAX_STEP_SEC = 90
SESSION_GAP = timedelta(minutes=5)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS panel_logins (
    id      bigserial PRIMARY KEY,
    org_id  text NOT NULL DEFAULT 'rexven',
    email   text NOT NULL,
    at      timestamptz NOT NULL DEFAULT now(),
    agent   text
);
CREATE INDEX IF NOT EXISTS panel_logins_email_at ON panel_logins (org_id, email, at);
CREATE TABLE IF NOT EXISTS panel_sessions (
    id          bigserial PRIMARY KEY,
    org_id      text NOT NULL DEFAULT 'rexven',
    email       text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    active_sec  numeric NOT NULL DEFAULT 0,
    pages       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS panel_sessions_email_seen ON panel_sessions (org_id, email, last_seen);
"""

_ready = False


def _ensure(conn: Any) -> None:
    global _ready
    if not _ready:
        conn.execute(SCHEMA_SQL)
        conn.commit()
        _ready = True


def record_login(email: str, agent: str | None) -> None:
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO panel_logins (org_id, email, agent) VALUES (%s, %s, %s)",
            (get_org_id(), email.strip().lower(), (agent or "")[:300]),
        )
        conn.commit()


def heartbeat(email: str, page: str) -> None:
    """Açık oturumu uzatır (aktif süre + sayfa sayacı) ya da yenisini açar."""
    page = (page or "diger")[:40]
    with connect() as conn:
        _ensure(conn)
        row = conn.execute(
            """
            UPDATE panel_sessions SET
                active_sec = active_sec + least(extract(epoch FROM now() - last_seen), %s),
                last_seen = now(),
                pages = jsonb_set(pages, ARRAY[%s], to_jsonb(coalesce((pages->>%s)::int, 0) + 1))
            WHERE id = (
                SELECT id FROM panel_sessions
                WHERE org_id = %s AND email = %s AND last_seen > now() - %s
                ORDER BY last_seen DESC LIMIT 1)
            RETURNING id
            """,
            (MAX_STEP_SEC, page, page, get_org_id(), email, SESSION_GAP),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO panel_sessions (org_id, email, pages) VALUES (%s, %s, %s::jsonb)",
                (get_org_id(), email, json.dumps({page: 1})),
            )
        conn.commit()


def summary(days: int, now: datetime) -> dict[str, Any]:
    """Kişi başına: girişler, aktif süre, aktif gün, sayfalar ve son oturumlar."""
    since = now - timedelta(days=days)
    with connect() as conn:
        _ensure(conn)
        logins = conn.execute(
            """
            SELECT email, count(*) FILTER (WHERE at >= %s), max(at)
            FROM panel_logins WHERE org_id = %s GROUP BY email
            """,
            (since, get_org_id()),
        ).fetchall()
        sessions = conn.execute(
            """
            SELECT email, started_at, last_seen, active_sec, pages
            FROM panel_sessions WHERE org_id = %s AND last_seen >= %s
            ORDER BY started_at DESC
            """,
            (get_org_id(), since),
        ).fetchall()
        first = conn.execute(
            "SELECT least((SELECT min(at) FROM panel_logins), (SELECT min(started_at) FROM panel_sessions))"
        ).fetchone()[0]
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"giris": 0, "son_giris": None, "son_gorulme": None, "aktif_sn": 0.0,
                 "gunler": set(), "sayfalar": defaultdict(int), "oturumlar": []}
    )
    for email, count, last in logins:
        out[email]["giris"] = int(count or 0)
        out[email]["son_giris"] = last
    for email, started, seen, active, pages in sessions:
        u = out[email]
        u["aktif_sn"] += float(active or 0)
        u["gunler"].add(started.date().isoformat())
        u["son_gorulme"] = max(filter(None, [u["son_gorulme"], seen]))
        for page, n in (pages or {}).items():
            u["sayfalar"][page] += int(n)
        if len(u["oturumlar"]) < 15:
            u["oturumlar"].append({"basladi": started, "bitti": seen, "aktif_sn": float(active or 0),
                                   "sayfalar": pages or {}})
    rows = []
    for email, u in out.items():
        top = sorted(u["sayfalar"].items(), key=lambda kv: -kv[1])
        rows.append({
            "email": email, "giris": u["giris"], "son_giris": u["son_giris"], "son_gorulme": u["son_gorulme"],
            "aktif_dk": round(u["aktif_sn"] / 60), "aktif_gun": len(u["gunler"]),
            "sayfalar": [{"sayfa": p, "dk": n} for p, n in top], "oturumlar": u["oturumlar"],
        })
    return {"gun": days, "olcum_basi": first, "kisiler": rows}
