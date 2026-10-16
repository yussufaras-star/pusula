"""Finans tablolarına okuma/yazma (düz psycopg3 + SQL).

Hareketler ON CONFLICT DO NOTHING ile eklenir: mevcut satır, kullanıcının
kategori / şirketler arası düzeltmesi dahil, hiç değişmez.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json

from pusula.config import get_org_id
from pusula.db.models import (
    API_PROVIDERS,
    FinAccount,
    FinCategory,
    FinCompany,
    FinDecision,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
    FinTransaction,
)
from pusula.finance.analytics import auto_intercompany
from pusula.finance.statement import Parsed

ACCOUNT_COLUMNS = (
    "id, company_id, name, institution, type, currency, provider, provider_account_id,"
    " credential_key, current_balance, balance_at, last_synced_at, sync_error,"
    " sync_warning, active, sort, created_at"
)
SYNC_SOURCE = "finance_sync"
ERROR_MAX = 200


def load_companies(conn: psycopg.Connection) -> list[FinCompany]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, name, country, base_currency, match_patterns, sort"
            " FROM fin_companies ORDER BY sort, id"
        ).fetchall()
    return [FinCompany(**{**r, "match_patterns": list(r["match_patterns"] or [])}) for r in rows]


def load_account(conn: psycopg.Connection, account_id: int) -> FinAccount | None:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            f"SELECT {ACCOUNT_COLUMNS} FROM fin_accounts WHERE id = %s", (account_id,)
        ).fetchone()
    return FinAccount(**row) if row else None


def load_sync_accounts(
    conn: psycopg.Connection, account_id: int | None = None
) -> list[FinAccount]:
    """Aktif API hesapları; en uzun süredir senkronlanmayan önce.

    Süre bütçesi dolup yarım kalan çalıştırmada atlanan hesaplar bir
    sonrakinde başa geçer.
    """
    sql = (
        f"SELECT {ACCOUNT_COLUMNS} FROM fin_accounts"
        " WHERE active AND provider = ANY(%s)"
    )
    params: list[object] = [sorted(API_PROVIDERS)]
    if account_id is not None:
        sql += " AND id = %s"
        params.append(account_id)
    sql += " ORDER BY last_synced_at NULLS FIRST, sort, id"
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(sql, params).fetchall()
    return [FinAccount(**r) for r in rows]


def existing_external_ids(
    conn: psycopg.Connection, account_id: int, external_ids: Sequence[str]
) -> set[str]:
    if not external_ids:
        return set()
    rows = conn.execute(
        "SELECT external_id FROM fin_transactions WHERE account_id = %s AND external_id = ANY(%s)",
        (account_id, list(external_ids)),
    ).fetchall()
    return {r[0] for r in rows}


# --- hareket ekleme ----------------------------------------------------------------


@dataclass(frozen=True)
class NewTx:
    booked_at: date
    amount: Decimal
    description: str
    external_id: str
    counterparty: str | None = None
    counterparty_company_id: str | None = None
    category_id: str | None = None


def classify(
    account: FinAccount, tx: NewTx, companies: Sequence[FinCompany]
) -> NewTx:
    """Şirketler arası kalıbı eşleşirse karşı şirket + ic_transfer."""
    if account.id is None:
        raise ValueError("Kayıtlı olmayan hesap")
    draft = FinTransaction(
        account_id=account.id,
        company_id=account.company_id,
        booked_at=tx.booked_at,
        amount=tx.amount,
        currency=account.currency,
        description=tx.description,
        counterparty=tx.counterparty,
        external_id=tx.external_id,
        source="api",
    )
    match = auto_intercompany(draft, companies)
    if match is None:
        return tx
    return NewTx(
        booked_at=tx.booked_at,
        amount=tx.amount,
        description=tx.description,
        external_id=tx.external_id,
        counterparty=tx.counterparty,
        counterparty_company_id=match[0],
        category_id=match[1],
    )


def insert_transactions(
    conn: psycopg.Connection, account: FinAccount, rows: Sequence[NewTx], source: str
) -> int:
    """Tek sorguda ekler; var olan external_id atlanır. Eklenen satır sayısı."""
    if not rows:
        return 0
    inserted = conn.execute(
        """
        INSERT INTO fin_transactions (
            account_id, company_id, booked_at, amount, currency, description,
            counterparty, counterparty_company_id, category_id, external_id, source
        )
        SELECT %s, %s, u.booked_at, u.amount, %s, u.description, u.counterparty,
               u.cp, u.cat, u.ext, %s
        FROM unnest(
            %s::date[], %s::numeric[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[]
        ) AS u(booked_at, amount, description, counterparty, cp, cat, ext)
        ON CONFLICT (account_id, external_id) DO NOTHING
        RETURNING id
        """,
        (
            account.id,
            account.company_id,
            account.currency,
            source,
            [r.booked_at for r in rows],
            [r.amount for r in rows],
            [r.description for r in rows],
            [r.counterparty for r in rows],
            [r.counterparty_company_id for r in rows],
            [r.category_id for r in rows],
            [r.external_id for r in rows],
        ),
    ).fetchall()
    return len(inserted)


# --- ekstre ------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportResult:
    inserted: int
    duplicates: int
    balance: Decimal | None  # güncellendiyse yeni bakiye


def import_statement(
    conn: psycopg.Connection,
    account: FinAccount,
    parsed: Parsed,
    companies: Sequence[FinCompany],
    update_balance: bool,
    now: datetime,
) -> ImportResult:
    """Ekstre hareketlerini ekler; bakiye istenirse en geç satırdan güncellenir."""
    rows = [
        classify(
            account,
            NewTx(
                booked_at=t.booked_at,
                amount=t.amount,
                description=t.description,
                external_id=t.external_id,
            ),
            companies,
        )
        for t in parsed.txs
    ]
    inserted = insert_transactions(conn, account, rows, "csv")
    balance = parsed.last_balance if update_balance else None
    if balance is not None:
        conn.execute(
            "UPDATE fin_accounts SET current_balance = %s, balance_at = %s,"
            " last_synced_at = %s, sync_error = NULL WHERE id = %s",
            (balance, now, now, account.id),
        )
    else:
        conn.execute(
            "UPDATE fin_accounts SET last_synced_at = %s, sync_error = NULL WHERE id = %s",
            (now, account.id),
        )
    return ImportResult(inserted=inserted, duplicates=len(rows) - inserted, balance=balance)


# --- senkron durumu ------------------------------------------------------------------


def mark_synced(
    conn: psycopg.Connection,
    account_id: int,
    balance: Decimal,
    warning: str | None,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE fin_accounts SET current_balance = %s, balance_at = %s, last_synced_at = %s,"
        " sync_error = NULL, sync_warning = %s WHERE id = %s",
        (balance, now, now, warning, account_id),
    )


def mark_error(conn: psycopg.Connection, account_id: int, message: str) -> None:
    conn.execute(
        "UPDATE fin_accounts SET sync_error = %s WHERE id = %s",
        (message[:ERROR_MAX], account_id),
    )


def last_sync_run(conn: psycopg.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT last_synced_at FROM sync_state WHERE org_id = %s AND source_name = %s",
        (get_org_id(), SYNC_SOURCE),
    ).fetchone()
    return row[0] if row else None


def stamp_sync_run(conn: psycopg.Connection, now: datetime) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (org_id, source_name, last_synced_at, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (org_id, source_name)
        DO UPDATE SET last_synced_at = EXCLUDED.last_synced_at, updated_at = EXCLUDED.updated_at
        """,
        (get_org_id(), SYNC_SOURCE, now, now),
    )


def last_fx_date(conn: psycopg.Connection) -> date | None:
    row = conn.execute("SELECT max(date) FROM fin_fx_rates").fetchone()
    return row[0] if row else None


# --- ekran verisi -------------------------------------------------------------------

TX_COLUMNS = (
    "id, account_id, company_id, booked_at, amount, currency, description, counterparty,"
    " category_id, counterparty_company_id, external_id, source, user_edited_at, created_at"
)
PLANNED_COLUMNS = (
    "id, company_id, direction, title, counterparty, amount, currency, due_date, recurrence,"
    " recur_until, probability, status, category_id, notes, created_at"
)
DECISION_COLUMNS = (
    "id, company_id, title, description, owner, status, priority, due_date, impact_usd,"
    " meeting_date, created_at, updated_at"
)
SNAPSHOT_COLUMNS = "id, company_id, week_date, locked, locked_at, fx, notes, created_at"
LINE_COLUMNS = "id, snapshot_id, side, section, group_name, label, amount, currency, origin, sort"


def _rows(conn: psycopg.Connection, query: str, params: Sequence[object] = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, params).fetchall()


def load_categories(conn: psycopg.Connection) -> list[FinCategory]:
    rows = _rows(conn, "SELECT id, name, kind, sort FROM fin_categories ORDER BY sort")
    return [FinCategory(**r) for r in rows]


def load_accounts(conn: psycopg.Connection) -> list[FinAccount]:
    rows = _rows(conn, f"SELECT {ACCOUNT_COLUMNS} FROM fin_accounts ORDER BY sort, id")
    return [FinAccount(**r) for r in rows]


def load_transactions(
    conn: psycopg.Connection, start: date | None = None, end: date | None = None
) -> list[FinTransaction]:
    query = f"SELECT {TX_COLUMNS} FROM fin_transactions WHERE true"
    params: list[object] = []
    if start is not None:
        query += " AND booked_at >= %s"
        params.append(start)
    if end is not None:
        query += " AND booked_at <= %s"
        params.append(end)
    return [FinTransaction(**r) for r in _rows(conn, query + " ORDER BY booked_at, id", params)]


def load_transaction(conn: psycopg.Connection, tx_id: int) -> FinTransaction | None:
    rows = _rows(conn, f"SELECT {TX_COLUMNS} FROM fin_transactions WHERE id = %s", (tx_id,))
    return FinTransaction(**rows[0]) if rows else None


def load_planned(conn: psycopg.Connection) -> list[FinPlannedItem]:
    rows = _rows(conn, f"SELECT {PLANNED_COLUMNS} FROM fin_planned_items ORDER BY due_date, id")
    return [FinPlannedItem(**r) for r in rows]


def load_decisions(conn: psycopg.Connection) -> list[FinDecision]:
    rows = _rows(conn, f"SELECT {DECISION_COLUMNS} FROM fin_decisions ORDER BY id")
    return [FinDecision(**r) for r in rows]


def load_snapshots(conn: psycopg.Connection) -> list[FinSnapshot]:
    rows = _rows(conn, f"SELECT {SNAPSHOT_COLUMNS} FROM fin_snapshots ORDER BY week_date, company_id")
    return [FinSnapshot(**r) for r in rows]


def load_snapshot(conn: psycopg.Connection, snapshot_id: int) -> FinSnapshot | None:
    rows = _rows(conn, f"SELECT {SNAPSHOT_COLUMNS} FROM fin_snapshots WHERE id = %s", (snapshot_id,))
    return FinSnapshot(**rows[0]) if rows else None


def load_line(conn: psycopg.Connection, line_id: int) -> FinSnapshotLine | None:
    rows = _rows(conn, f"SELECT {LINE_COLUMNS} FROM fin_snapshot_lines WHERE id = %s", (line_id,))
    return FinSnapshotLine(**rows[0]) if rows else None


def load_row(conn: psycopg.Connection, table: str, row_id: int) -> dict | None:
    """Planlı kalem / karar / hesap: tek satır (birleştirip doğrulamak için)."""
    columns = {
        "fin_planned_items": PLANNED_COLUMNS,
        "fin_decisions": DECISION_COLUMNS,
        "fin_accounts": ACCOUNT_COLUMNS,
    }[table]
    rows = _rows(conn, f"SELECT {columns} FROM {table} WHERE id = %s", (row_id,))
    return rows[0] if rows else None


def load_snapshot_lines(conn: psycopg.Connection, snapshot_ids: Sequence[int]) -> list[FinSnapshotLine]:
    if not snapshot_ids:
        return []
    rows = _rows(
        conn,
        f"SELECT {LINE_COLUMNS} FROM fin_snapshot_lines WHERE snapshot_id = ANY(%s)"
        " ORDER BY snapshot_id, sort, id",
        (list(snapshot_ids),),
    )
    return [FinSnapshotLine(**r) for r in rows]


# --- genel yazma ------------------------------------------------------------------------

# Tablo → elle yazılabilir kolonlar. Liste dışı alan sorguya giremez.
WRITABLE: dict[str, frozenset[str]] = {
    "fin_accounts": frozenset(
        {
            "company_id", "name", "institution", "type", "currency", "provider",
            "provider_account_id", "credential_key", "current_balance", "balance_at",
            "active", "sort",
        }
    ),
    "fin_planned_items": frozenset(
        {
            "company_id", "direction", "title", "counterparty", "amount", "currency", "due_date",
            "recurrence", "recur_until", "probability", "status", "category_id", "notes",
        }
    ),
    "fin_decisions": frozenset(
        {
            "company_id", "title", "description", "owner", "status", "priority", "due_date",
            "impact_usd", "meeting_date",
        }
    ),
    "fin_snapshots": frozenset({"notes", "locked"}),
    "fin_snapshot_lines": frozenset({"side", "section", "group_name", "label", "amount", "currency", "sort"}),
    "fin_transactions": frozenset({"category_id", "counterparty_company_id", "user_edited_at"}),
}
DELETABLE = frozenset({"fin_planned_items", "fin_decisions", "fin_snapshots", "fin_snapshot_lines"})


def _check(table: str, values: dict[str, object]) -> None:
    unknown = set(values) - WRITABLE[table]
    if unknown:
        raise ValueError(f"Yazılamaz alan: {', '.join(sorted(unknown))}")


def insert_row(conn: psycopg.Connection, table: str, values: dict[str, object]) -> int:
    _check(table, values)
    columns = list(values)
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    row = conn.execute(query, [values[c] for c in columns]).fetchone()
    return int(row[0])


def update_row(conn: psycopg.Connection, table: str, row_id: int, values: dict[str, object]) -> bool:
    """Yalnız verilen alanlar; satır yoksa False."""
    _check(table, values)
    if not values:
        exists = sql.SQL("SELECT 1 FROM {} WHERE id = %s").format(sql.Identifier(table))
        return conn.execute(exists, (row_id,)).fetchone() is not None
    query = sql.SQL("UPDATE {} SET {} WHERE id = %s RETURNING id").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in values),
    )
    return conn.execute(query, [*values.values(), row_id]).fetchone() is not None


def delete_row(conn: psycopg.Connection, table: str, row_id: int) -> bool:
    if table not in DELETABLE:
        raise ValueError("Bu kayıt silinemez")
    query = sql.SQL("DELETE FROM {} WHERE id = %s RETURNING id").format(sql.Identifier(table))
    return conn.execute(query, (row_id,)).fetchone() is not None


def insert_snapshot(
    conn: psycopg.Connection, company_id: str, week: date, fx_rates: dict[str, Decimal]
) -> int:
    row = conn.execute(
        "INSERT INTO fin_snapshots (company_id, week_date, fx) VALUES (%s, %s, %s) RETURNING id",
        (company_id, week, Json({k: str(v) for k, v in fx_rates.items()})),
    ).fetchone()
    return int(row[0])


def insert_line(conn: psycopg.Connection, snapshot_id: int, values: dict[str, object]) -> int:
    """values: side, section, group_name, label, amount, currency, origin, sort."""
    row = conn.execute(
        "INSERT INTO fin_snapshot_lines"
        " (snapshot_id, side, section, group_name, label, amount, currency, origin, sort)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            snapshot_id,
            values["side"],
            values.get("section"),
            values["group_name"],
            values["label"],
            values["amount"],
            values["currency"],
            values.get("origin", "manual"),
            values.get("sort", 0),
        ),
    ).fetchone()
    return int(row[0])
