"""psycopg3 bağlantı havuzu ve temel veritabanı fonksiyonları.

İki grup fonksiyon sunar:
- Temel erişim: insert_event, get_sync_state, set_sync_state.
- Kimlik çözümleme yardımcıları: identity.py'nin kullandığı, tek
  transaction içinde çalışabilmek için Connection parametresi alan
  fonksiyonlar ve transaction() context manager'ı.
Bağlantı bilgisi DATABASE_URL ortam değişkeninden okunur. Tüm
sorgular config.get_org_id() ile aktif org'a kapsamlanır.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from pusula.config import get_org_id
from pusula.db.models import Event, SyncState

# Havuz ilk kullanımda açılır; import anında bağlantı kurulmaz.
_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    # Tembel başlatma: DATABASE_URL sadece gerçekten gerektiğinde okunur.
    global _pool
    if _pool is None:
        _pool = ConnectionPool(conninfo=os.environ["DATABASE_URL"], open=True)
    return _pool


def insert_event(
    event: Event, conn: psycopg.Connection[Any] | None = None
) -> int | None:
    """Olayı events tablosuna yazar.

    (org_id, channel, source_ref) çakışmasında hiçbir şey yapmaz
    (idempotent ingest). Yeni kayıt yazıldıysa id, çakışma olduysa
    None döner. conn verilirse dış transaction kullanılır.
    """
    query = """
        INSERT INTO events (
            org_id, thread_id, channel, direction, rep_id, occurred_at,
            source_ref, body, body_quality, meta
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (org_id, channel, source_ref) DO NOTHING
        RETURNING id
    """
    params = (
        get_org_id(),
        event.thread_id,
        event.channel,
        event.direction,
        event.rep_id,
        event.occurred_at,
        event.source_ref,
        event.body,
        event.body_quality,
        Json(event.meta) if event.meta is not None else None,
    )
    if conn is not None:
        row = conn.execute(query, params).fetchone()
        return row[0] if row is not None else None
    with _get_pool().connection() as owned:
        row = owned.execute(query, params).fetchone()
    return row[0] if row is not None else None


def get_sync_state(source_name: str) -> SyncState | None:
    """Kaynağın senkron durumunu döner; kayıt yoksa None."""
    query = """
        SELECT source_name, last_synced_at, last_cursor, updated_at
        FROM sync_state
        WHERE org_id = %s AND source_name = %s
    """
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(query, (get_org_id(), source_name)).fetchone()
    return SyncState(**row) if row is not None else None


def set_sync_state(state: SyncState) -> None:
    """Kaynağın senkron durumunu yazar (upsert). updated_at DB'de atanır."""
    query = """
        INSERT INTO sync_state (org_id, source_name, last_synced_at, last_cursor, updated_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (org_id, source_name) DO UPDATE SET
            last_synced_at = EXCLUDED.last_synced_at,
            last_cursor = EXCLUDED.last_cursor,
            updated_at = now()
    """
    with _get_pool().connection() as conn:
        conn.execute(
            query, (get_org_id(), state.source_name, state.last_synced_at, state.last_cursor)
        )


def touch_thread(
    thread_id: str,
    channel: str,
    occurred_at: datetime | None,
    owner_rep_id: str | None = None,
    conn: psycopg.Connection[Any] | None = None,
) -> None:
    """threads.last_touch_at, touch_count ve owner_rep_id günceller.

    last_touch_at geriye gitmez (greatest); kanal sayacı bir artar.
    owner_rep_id verilirse en son event'in temsilcisi olarak yazılır.
    occurred_at None ise last_touch_at'e dokunulmaz, sadece sayaç artar.
    """
    query = """
        UPDATE threads SET
            last_touch_at = CASE
                WHEN %(occurred_at)s IS NULL THEN last_touch_at
                ELSE greatest(last_touch_at, %(occurred_at)s)
            END,
            touch_count_by_channel = jsonb_set(
                coalesce(touch_count_by_channel, '{}'::jsonb),
                ARRAY[%(channel)s],
                to_jsonb(coalesce((touch_count_by_channel ->> %(channel)s)::int, 0) + 1)
            ),
            owner_rep_id = coalesce(%(owner_rep_id)s, owner_rep_id)
        WHERE org_id = %(org_id)s AND thread_id = %(thread_id)s
    """
    # owner_rep_id her zaman son event'inki olmalı; coalesce yukarıda
    # None gelirse eskiyi korur — çağıran dolu rep_id geçirmeli.
    params = {
        "org_id": get_org_id(),
        "thread_id": thread_id,
        "channel": channel,
        "occurred_at": occurred_at,
        "owner_rep_id": owner_rep_id,
    }
    if conn is not None:
        conn.execute(query, params)
        return
    with _get_pool().connection() as owned:
        owned.execute(query, params)


def clear_thread_identities(
    conn: psycopg.Connection[Any], thread_id: str
) -> None:
    """Thread'e bağlı identities satırlarını siler (orphan geri alma)."""
    conn.execute(
        "DELETE FROM identities WHERE org_id = %s AND thread_id = %s",
        (get_org_id(), thread_id),
    )


# --- Kimlik çözümleme yardımcıları (identity.py kullanır) ---
# Hepsi Connection parametresi alır; böylece resolve_thread tüm adımları
# tek transaction içinde yürütebilir.


@contextmanager
def transaction() -> Iterator[psycopg.Connection[Any]]:
    """Havuzdan bağlantı verir; blok sonunda commit, hatada rollback."""
    with _get_pool().connection() as conn:
        yield conn


def is_identifier_blocked(conn: psycopg.Connection[Any], id_type: str, id_value: str) -> bool:
    """Tanımlayıcı blocked_identifiers'da mı."""
    query = """
        SELECT 1 FROM blocked_identifiers
        WHERE org_id = %s AND id_type = %s AND id_value = %s
    """
    return conn.execute(query, (get_org_id(), id_type, id_value)).fetchone() is not None


def find_identity_thread_id(
    conn: psycopg.Connection[Any], id_type: str, id_value: str
) -> str | None:
    """Tanımlayıcının bağlı olduğu thread_id'yi döner; kayıt yoksa None."""
    query = """
        SELECT thread_id FROM identities
        WHERE org_id = %s AND id_type = %s AND id_value = %s
    """
    row = conn.execute(query, (get_org_id(), id_type, id_value)).fetchone()
    return row[0] if row is not None else None


def create_thread(conn: psycopg.Connection[Any], thread_id: str) -> None:
    """threads tablosuna yeni bir satır açar; created_at DB'de atanır."""
    conn.execute(
        "INSERT INTO threads (org_id, thread_id) VALUES (%s, %s)",
        (get_org_id(), thread_id),
    )


def upsert_identity(
    conn: psycopg.Connection[Any], thread_id: str, id_type: str, id_value: str
) -> None:
    """Tanımlayıcıyı thread'e bağlar; varsa last_seen_at'i günceller."""
    query = """
        INSERT INTO identities (org_id, thread_id, id_type, id_value)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (org_id, id_type, id_value) DO UPDATE SET
            thread_id = EXCLUDED.thread_id,
            last_seen_at = now()
    """
    conn.execute(query, (get_org_id(), thread_id, id_type, id_value))


def pick_oldest_thread(conn: psycopg.Connection[Any], thread_ids: list[str]) -> str:
    """Verilen thread'lerden en eski created_at'e sahip olanı döner.

    Eşitlikte thread_id sırası belirleyicidir (deterministik merge).
    """
    query = """
        SELECT thread_id FROM threads
        WHERE org_id = %s AND thread_id = ANY(%s)
        ORDER BY created_at ASC, thread_id ASC
        LIMIT 1
    """
    row = conn.execute(query, (get_org_id(), thread_ids)).fetchone()
    if row is None:
        raise ValueError(f"threads tablosunda kayıt yok: {thread_ids}")
    return row[0]


def reassign_thread_rows(
    conn: psycopg.Connection[Any], winner_thread_id: str, loser_thread_id: str
) -> None:
    """Kaybeden thread'in identities, events ve commitments satırlarını taşır."""
    for table in ("identities", "events", "commitments"):
        conn.execute(
            f"UPDATE {table} SET thread_id = %s WHERE org_id = %s AND thread_id = %s",
            (winner_thread_id, get_org_id(), loser_thread_id),
        )


def record_thread_merge(
    conn: psycopg.Connection[Any],
    winner_thread_id: str,
    loser_thread_id: str,
    reason: str | None,
) -> None:
    """thread_merges tablosuna denetim kaydı düşer."""
    query = """
        INSERT INTO thread_merges (org_id, winner_thread_id, loser_thread_id, reason)
        VALUES (%s, %s, %s, %s)
    """
    conn.execute(query, (get_org_id(), winner_thread_id, loser_thread_id, reason))


def delete_thread(conn: psycopg.Connection[Any], thread_id: str) -> None:
    """threads satırını siler (merge sonrası kaybeden hat için)."""
    conn.execute(
        "DELETE FROM threads WHERE org_id = %s AND thread_id = %s",
        (get_org_id(), thread_id),
    )
