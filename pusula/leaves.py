"""İzinli günler ve muaf süreler: yöneticinin panelden girdiği kayıtlar.

İzinli gün doluluğa, kayıp zamana, ekip toplamına ve uyarılara girmez.
Muaf süre (eğitim vb.) gün içindeki bir saat aralığını aynı şekilde dışarıda
bırakır. Tablolar ilk yazmada oluşur; yoksa okuma boş döner.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time

from pusula.config import get_org_id
from pusula.metrics import TZ, Span
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


# --- muaf süre ------------------------------------------------------------------
# Gün içinde yöneticinin elle girdiği saat aralığı (eğitim, iç toplantı vb.).
# O aralıktaki boşluk kayıp ve mola sayılmaz; doluluk paydasından düşer.
# Aralıkta yapılan faaliyet yine dolu sayılır.

EXCUSED_SQL = """
CREATE TABLE IF NOT EXISTS rep_excused (
    id          bigserial PRIMARY KEY,
    org_id      text NOT NULL DEFAULT 'rexven',
    rep_id      text NOT NULL,
    day         date NOT NULL,
    start_at    timestamptz NOT NULL,
    end_at      timestamptz NOT NULL,
    reason      text,
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (end_at > start_at)
);
CREATE INDEX IF NOT EXISTS rep_excused_day ON rep_excused (org_id, day);
"""


@dataclass(frozen=True)
class Excused:
    id: int
    rep_id: str
    day: date
    start: datetime
    end: datetime
    reason: str | None


def load_excused(span: Span) -> dict[tuple[str, date], list[Excused]]:
    """Aralıktaki muaf süreler, (rep_id, gün) başına başlangıca göre sıralı."""
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('rep_excused')").fetchone()
        if not exists or exists[0] is None:
            return {}
        rows = conn.execute(
            """
            SELECT id, rep_id, day, start_at, end_at, reason FROM rep_excused
            WHERE org_id = %s AND day >= %s AND day < %s
            ORDER BY start_at
            """,
            (get_org_id(), span.start.date(), span.end.date()),
        ).fetchall()
    out: dict[tuple[str, date], list[Excused]] = defaultdict(list)
    for row_id, rep_id, day, start, end, reason in rows:
        item = Excused(int(row_id), str(rep_id), day, start.astimezone(TZ), end.astimezone(TZ), reason)
        out[(item.rep_id, day)].append(item)
    return dict(out)


def add_excused(
    rep_id: str, day: date, start: time, end: time, reason: str | None, *, by: str
) -> int:
    start_at = datetime.combine(day, start, tzinfo=TZ)
    end_at = datetime.combine(day, end, tzinfo=TZ)
    with connect() as conn:
        conn.execute(EXCUSED_SQL)
        row = conn.execute(
            """
            INSERT INTO rep_excused (org_id, rep_id, day, start_at, end_at, reason, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (get_org_id(), rep_id, day, start_at, end_at, reason, by),
        ).fetchone()
    return int(row[0])


def delete_excused(row_id: int) -> bool:
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('rep_excused')").fetchone()
        if not exists or exists[0] is None:
            return False
        cur = conn.execute(
            "DELETE FROM rep_excused WHERE org_id = %s AND id = %s", (get_org_id(), row_id)
        )
        return cur.rowcount > 0
