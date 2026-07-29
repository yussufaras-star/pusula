"""psycopg3 bağlantı havuzu ve temel veritabanı fonksiyonları.

Sadece üç fonksiyon sunar: insert_event, get_sync_state, set_sync_state.
Bağlantı bilgisi DATABASE_URL ortam değişkeninden okunur.
"""

import os

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from pusula.db.models import Event, SyncState

# Havuz ilk kullanımda açılır; import anında bağlantı kurulmaz.
_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    # Tembel başlatma: DATABASE_URL sadece gerçekten gerektiğinde okunur.
    global _pool
    if _pool is None:
        _pool = ConnectionPool(conninfo=os.environ["DATABASE_URL"], open=True)
    return _pool


def insert_event(event: Event) -> int | None:
    """Olayı events tablosuna yazar.

    (channel, source_ref) çakışmasında hiçbir şey yapmaz (idempotent
    ingest). Yeni kayıt yazıldıysa id, çakışma olduysa None döner.
    """
    query = """
        INSERT INTO events (
            thread_id, channel, direction, rep_id, occurred_at,
            source_ref, body, body_quality, meta
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (channel, source_ref) DO NOTHING
        RETURNING id
    """
    params = (
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
    with _get_pool().connection() as conn:
        row = conn.execute(query, params).fetchone()
    return row[0] if row is not None else None


def get_sync_state(source_name: str) -> SyncState | None:
    """Kaynağın senkron durumunu döner; kayıt yoksa None."""
    query = "SELECT * FROM sync_state WHERE source_name = %s"
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(query, (source_name,)).fetchone()
    return SyncState(**row) if row is not None else None


def set_sync_state(state: SyncState) -> None:
    """Kaynağın senkron durumunu yazar (upsert). updated_at DB'de atanır."""
    query = """
        INSERT INTO sync_state (source_name, last_synced_at, last_cursor, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (source_name) DO UPDATE SET
            last_synced_at = EXCLUDED.last_synced_at,
            last_cursor = EXCLUDED.last_cursor,
            updated_at = now()
    """
    with _get_pool().connection() as conn:
        conn.execute(query, (state.source_name, state.last_synced_at, state.last_cursor))
