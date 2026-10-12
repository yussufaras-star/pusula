"""İzinli günler: yöneticinin panelden işaretlediği temsilci-günleri.

İzinli gün doluluğa, kayıp zamana, ekip toplamına ve uyarılara girmez.
Tablo ilk yazmada oluşur; yoksa okuma boş döner.
"""

from __future__ import annotations

from datetime import date

from pusula.config import get_org_id
from pusula.metrics import Span
from pusula.panel_data import connect

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rep_leaves (
    org_id      text NOT NULL DEFAULT 'rexven',
    rep_id      text NOT NULL,
    day         date NOT NULL,
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, rep_id, day)
);
"""


def load_leaves(span: Span) -> set[tuple[str, date]]:
    """Aralıktaki (rep_id, gün) izinleri."""
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('rep_leaves')").fetchone()
        if not exists or exists[0] is None:
            return set()
        rows = conn.execute(
            "SELECT rep_id, day FROM rep_leaves WHERE org_id = %s AND day >= %s AND day < %s",
            (get_org_id(), span.start.date(), span.end.date()),
        ).fetchall()
    return {(str(rep_id), day) for rep_id, day in rows}


def set_leave(rep_id: str, day: date, on: bool, *, by: str) -> None:
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        if on:
            conn.execute(
                """
                INSERT INTO rep_leaves (org_id, rep_id, day, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (org_id, rep_id, day) DO NOTHING
                """,
                (get_org_id(), rep_id, day, by),
            )
        else:
            conn.execute(
                "DELETE FROM rep_leaves WHERE org_id = %s AND rep_id = %s AND day = %s",
                (get_org_id(), rep_id, day),
            )
