"""psycopg3 bağlantı havuzu ve temel veritabanı fonksiyonları.

İki grup fonksiyon sunar:
- Temel erişim: insert_event, get_sync_state, set_sync_state.
- Kimlik çözümleme yardımcıları: identity.py'nin kullandığı, tek
  transaction içinde çalışabilmek için Connection parametresi alan
  fonksiyonlar ve transaction() context manager'ı.
Bağlantı bilgisi DATABASE_URL ortam değişkeninden okunur. Tüm
sorgular config.get_org_id() ile aktif org'a kapsamlanır.
"""

import logging
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

logger = logging.getLogger(__name__)

# Havuz ilk kullanımda açılır; import anında bağlantı kurulmaz.
_pool: ConnectionPool | None = None

# Process başı blocklist cache (load_blocklist doldurur).
_blocked_identifiers: set[tuple[str, str]] | None = None
_blocked_domains: set[str] | None = None

# Uzun ingest'te pooler'ın düşürdüğü bağlantılar için.
_POOL_RECONNECT_TIMEOUT = 60.0
_POOL_MAX_IDLE = 300.0
_POOL_MAX_LIFETIME = 1800.0


def _get_pool() -> ConnectionPool:
    # Tembel başlatma: DATABASE_URL sadece gerçekten gerektiğinde okunur.
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=os.environ["DATABASE_URL"],
            open=True,
            reconnect_timeout=_POOL_RECONNECT_TIMEOUT,
            max_idle=_POOL_MAX_IDLE,
            max_lifetime=_POOL_MAX_LIFETIME,
        )
    return _pool


def reset_pool() -> None:
    """Bağlantı havuzunu kapatır; sonraki kullanım yenisini açar.

    Uzun çalıştırmalarda Supabase pooler'ın düşürdüğü stale
    bağlantıları temizlemek için run() periyodik çağırır.
    """
    global _pool
    old = _pool
    _pool = None
    if old is None:
        return
    try:
        old.close()
    except Exception:
        logger.warning("bağlantı havuzu kapatılırken hata", exc_info=True)


def load_blocklist(conn: psycopg.Connection[Any]) -> None:
    """blocked_identifiers ve blocked_domains'i belleğe alır (org filtreli).

    Process başına bir kez (run başı) çağrılır; is_identifier_blocked
    bundan sonra DB'ye gitmez.
    """
    global _blocked_identifiers, _blocked_domains
    org_id = get_org_id()
    id_rows = conn.execute(
        """
        SELECT id_type, id_value FROM blocked_identifiers
        WHERE org_id = %s
        """,
        (org_id,),
    ).fetchall()
    domain_rows = conn.execute(
        """
        SELECT domain FROM blocked_domains
        WHERE org_id = %s
        """,
        (org_id,),
    ).fetchall()
    _blocked_identifiers = {(str(id_type), str(id_value)) for id_type, id_value in id_rows}
    _blocked_domains = {str(domain) for (domain,) in domain_rows}
    logger.info(
        "blocklist yüklendi: %s identifier, %s domain",
        len(_blocked_identifiers),
        len(_blocked_domains),
    )


def is_identifier_blocked(id_type: str, id_value: str) -> bool:
    """Tanımlayıcı blocklist'te mi (bellek cache).

    Cache yoksa RuntimeError. E-postada domain de blocked_domains'te
    aranır.
    """
    if _blocked_identifiers is None or _blocked_domains is None:
        raise RuntimeError(
            "blocklist yüklenmedi; önce load_blocklist(conn) çağır"
        )
    if (id_type, id_value) in _blocked_identifiers:
        return True
    if id_type != "email":
        return False
    domain = id_value.rpartition("@")[2]
    return domain in _blocked_domains


def insert_event(
    event: Event, conn: psycopg.Connection[Any] | None = None
) -> tuple[int, bool]:
    """Olayı events tablosuna yazar veya günceller (upsert).

    (org_id, channel, source_ref) çakışmasında body, meta, occurred_at,
    direction, channel güncellenir; created_at değişmez.
    Dönüş: (event_id, created). created=False → mevcut satır güncellendi.
    call↔meeting kanal geçişinde aynı source_ref tek satırda kalır
    (randevu tamamlanınca Zoho source_ref'i değiştirmez).
    conn verilirse dış transaction kullanılır.
    """
    if conn is not None:
        return _upsert_event_on_conn(conn, event)
    with _get_pool().connection() as owned:
        return _upsert_event_on_conn(owned, event)


def _upsert_event_on_conn(
    conn: psycopg.Connection[Any], event: Event
) -> tuple[int, bool]:
    org_id = get_org_id()
    meta_json = Json(event.meta) if event.meta is not None else None

    # Randevu tamamlanınca channel meeting→call olur; source_ref sabit.
    # UNIQUE (org_id, channel, source_ref) bu geçişi yakalamaz — önce taşı.
    if event.channel in ("call", "meeting"):
        moved = conn.execute(
            """
            UPDATE events SET
                channel = %s,
                body = %s,
                meta = %s,
                occurred_at = %s,
                direction = %s
            WHERE org_id = %s
              AND source_ref = %s
              AND channel IN ('call', 'meeting')
              AND channel IS DISTINCT FROM %s
            RETURNING id
            """,
            (
                event.channel,
                event.body,
                meta_json,
                event.occurred_at,
                event.direction,
                org_id,
                event.source_ref,
                event.channel,
            ),
        ).fetchone()
        if moved is not None:
            return moved[0], False

    row = conn.execute(
        """
        INSERT INTO events (
            org_id, thread_id, channel, direction, rep_id, occurred_at,
            source_ref, body, body_quality, meta
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (org_id, channel, source_ref) DO UPDATE SET
            channel = EXCLUDED.channel,
            body = EXCLUDED.body,
            meta = EXCLUDED.meta,
            occurred_at = EXCLUDED.occurred_at,
            direction = EXCLUDED.direction
        RETURNING id, (xmax = 0) AS inserted
        """,
        (
            org_id,
            event.thread_id,
            event.channel,
            event.direction,
            event.rep_id,
            event.occurred_at,
            event.source_ref,
            event.body,
            event.body_quality,
            meta_json,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"insert_event RETURNING boş: channel={event.channel} "
            f"source_ref={event.source_ref}"
        )
    return int(row[0]), bool(row[1])


def upsert_open_commitment(
    conn: psycopg.Connection[Any],
    *,
    thread_id: str | None,
    source_event_id: int,
    text: str | None,
    due_at: datetime | None,
) -> None:
    """Planlanmış arama için open commitment yazar veya metni/due_at günceller."""
    org_id = get_org_id()
    existing = conn.execute(
        """
        SELECT id FROM commitments
        WHERE org_id = %s AND source_event_id = %s
        """,
        (org_id, source_event_id),
    ).fetchone()
    if existing is not None:
        conn.execute(
            """
            UPDATE commitments SET
                thread_id = COALESCE(%s, thread_id),
                text = %s,
                due_at = %s
            WHERE id = %s
            """,
            (thread_id, text, due_at, existing[0]),
        )
        return
    conn.execute(
        """
        INSERT INTO commitments (
            org_id, thread_id, source_event_id, text, due_at, status
        )
        VALUES (%s, %s, %s, %s, %s, 'open')
        """,
        (org_id, thread_id, source_event_id, text, due_at),
    )


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
    for table in ("identities", "events", "commitments", "leads", "contacts", "deals"):
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
