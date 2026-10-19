"""Finans API'si (/api/finance/*).

Kapı router seviyesindedir: buraya eklenen her uç finance_user'dan geçer.
Temsilci ve PUSULA_FINANCE_EMAILS dışındaki yönetici 403 alır. Cron ucu
(/api/cron/finance-sync) oturumla değil CRON_SECRET ile korunur; ayrı
router'dadır (cron_router).
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg import errors as pg_errors
from pydantic import BaseModel, ValidationError

from pusula.db.models import (
    API_PROVIDERS,
    CompanyId,
    Currency,
    FinAccount,
    FinCategoryRule,
    FinCompany,
    FinDecision,
    FinPlannedItem,
    FinSnapshot,
    FinSnapshotLine,
    FinTaxSettings,
    FinWallet,
    Money,
    SnapshotSection,
)
from pusula.finance import analytics, store, tax, views
from pusula.finance.fx import load_fx
from pusula.finance.snapshot import (
    SECTION_BY_KEY,
    build_week_lines,
    intercompany_balances,
    mail_summary,
    previous_snapshot,
    side_of,
    week_end,
)
from pusula.finance.sync import run_finance_sync
from pusula.finance.statement import (
    Parsed,
    Sheet,
    StatementError,
    StatementMapping,
    apply_mapping,
    find_header,
    guess_mapping,
    read_rows,
)
from pusula.metrics import TZ
from pusula.panel_data import connect
from pusula.web.auth import finance_user, require_cron

ModelT = TypeVar("ModelT", bound=BaseModel)
PREVIEW_ROWS = 8
STATEMENT_PROVIDERS = ("csv", "manual")
MANUAL_SYNC_MIN_GAP = 60


def require_database() -> None:
    if not (os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")):
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL_POOLED / DATABASE_URL yapılandırılmamış",
        )


# Sıra önemli: yetki kontrolü yapılandırma hatasından önce gelir.
router = APIRouter(
    prefix="/api/finance",
    dependencies=[Depends(finance_user), Depends(require_database)],
)
cron_router = APIRouter(dependencies=[Depends(require_cron), Depends(require_database)])


@router.get("/companies")
def companies() -> Any:
    with connect() as conn:
        return jsonable_encoder(store.load_companies(conn))


# --- ekstre yükleme ----------------------------------------------------------------


class StatementBody(BaseModel):
    filename: str
    content_b64: str
    # Boşsa başlıktan tahmin edilir; kullanıcı önizlemede düzeltip geri yollar.
    mapping: StatementMapping | None = None
    invert: bool = False
    update_balance: bool = True


def _statement_account(conn: Any, account_id: int) -> FinAccount:
    account = store.load_account(conn, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Hesap bulunamadı")
    if account.provider not in STATEMENT_PROVIDERS:
        raise HTTPException(
            status_code=400, detail="Bu hesap API ile senkronize ediliyor; ekstre yüklenmez"
        )
    return account


def parse_statement(body: StatementBody) -> tuple[Sheet, StatementMapping | None, Parsed | None]:
    """Dosya → başlık, kullanılan eşleme, hareketler. Eşleme yoksa hareket yok."""
    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Dosya içeriği okunamadı") from exc
    try:
        sheet = find_header(read_rows(body.filename, data))
    except StatementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mapping = body.mapping or guess_mapping(sheet.header)
    if mapping is None:
        return sheet, None, None
    width = len(sheet.header)
    for index in mapping.model_dump().values():
        if index is not None and not 0 <= index < width:
            raise HTTPException(status_code=400, detail="Sütun eşlemesi dosyayla uyuşmuyor")
    return sheet, mapping, apply_mapping(sheet, mapping, body.invert)


@router.post("/accounts/{account_id}/statement/preview")
def statement_preview(account_id: int, body: StatementBody) -> Any:
    """Yüklemeden önce: başlık, tahmini eşleme, ilk satırlar, kaçı zaten var."""
    sheet, mapping, parsed = parse_statement(body)
    with connect() as conn:
        account = _statement_account(conn, account_id)
        duplicates = 0
        if parsed is not None:
            ids = [t.external_id for t in parsed.txs]
            duplicates = len(store.existing_external_ids(conn, account_id, ids))
    span = parsed.date_range if parsed else None
    return jsonable_encoder(
        {
            "currency": account.currency,
            "header": sheet.header,
            "header_index": sheet.header_index,
            "mapping": mapping,
            "count": len(parsed.txs) if parsed else 0,
            "skipped": parsed.skipped if parsed else len(sheet.rows),
            "duplicates": duplicates,
            "date_from": span[0] if span else None,
            "date_to": span[1] if span else None,
            "last_balance": parsed.last_balance if parsed else None,
            "sample": [
                {
                    "booked_at": t.booked_at,
                    "description": t.description,
                    "amount": t.amount,
                    "balance": t.balance,
                }
                for t in (parsed.txs[:PREVIEW_ROWS] if parsed else [])
            ],
        }
    )


@router.post("/accounts/{account_id}/statement/import")
def statement_import(account_id: int, body: StatementBody) -> Any:
    _, mapping, parsed = parse_statement(body)
    if mapping is None or parsed is None:
        raise HTTPException(status_code=400, detail="Sütun eşlemesi gerekli")
    if not parsed.txs:
        raise HTTPException(status_code=400, detail="Dosyada okunabilen hareket yok")
    with connect() as conn:
        account = _statement_account(conn, account_id)
        result = store.import_statement(
            conn,
            account,
            parsed,
            store.load_companies(conn),
            body.update_balance,
            datetime.now(TZ),
        )
        span = parsed.date_range
        if span:
            since = span[0] - timedelta(days=analytics.OWN_MATCH_DAYS)
            store.apply_own_transfers(conn, since)
            store.apply_wallet_deposits(conn, since)
            store.apply_category_rules(conn, since)
    return jsonable_encoder(
        {
            "inserted": result.inserted,
            "duplicates": result.duplicates,
            "skipped": parsed.skipped,
            "balance": result.balance,
        }
    )


# --- banka senkronu ------------------------------------------------------------------


class SyncBody(BaseModel):
    account_id: int | None = None


@router.post("/sync")
def sync_now(body: SyncBody | None = None) -> Any:
    """Paneldeki "Hesapları güncelle" butonu. Dakikada en fazla bir kez."""
    report = run_finance_sync(
        account_id=body.account_id if body else None,
        min_gap_sec=MANUAL_SYNC_MIN_GAP,
    )
    return jsonable_encoder(report)


@cron_router.get("/api/cron/finance-sync")
def finance_sync_cron() -> Any:
    """Harici tetikleyici. Vercel 60 sn sınırında kalır; kalan hesaplar sonraki çalıştırmada."""
    return jsonable_encoder(run_finance_sync())


# --- ortak yardımcılar ---------------------------------------------------------------

MAX_SPAN_DAYS = 800


def _now() -> datetime:
    return datetime.now(TZ)


def _day(raw: str | None, name: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} YYYY-AA-GG olmalı") from exc


def date_range(start: str | None, end: str | None, today: date) -> tuple[date, date]:
    """Varsayılan son 6 ay; bitiş bugünü geçebilir (planlı), en fazla 800 gün."""
    first = _day(start, "Başlangıç") or views.default_start(today)
    last = _day(end, "Bitiş") or today
    if first > last:
        raise HTTPException(status_code=400, detail="Başlangıç bitişten sonra olamaz")
    if (last - first).days > MAX_SPAN_DAYS:
        raise HTTPException(status_code=400, detail="Aralık en fazla 800 gün")
    return first, last


def _scope(company: str, companies: list[FinCompany]) -> list[str]:
    try:
        return views.scope_ids(company, companies)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bilinmeyen şirket") from exc


def _bundle(conn: Any, since: date | None, until: date | None = None) -> views.Bundle:
    return views.Bundle(
        companies=store.load_companies(conn),
        categories=store.load_categories(conn),
        accounts=store.load_accounts(conn),
        transactions=store.load_transactions(conn, since, until) if since else [],
        planned=store.load_planned(conn),
        decisions=store.load_decisions(conn),
        fx=load_fx(conn, since),
    )


@contextmanager
def db_errors() -> Iterator[None]:
    """DB kısıt / trigger hatası → kısa Türkçe yanıt."""
    try:
        yield
    except pg_errors.RaiseException as exc:
        # Kilitli hafta trigger'ı
        message = (exc.diag.message_primary or "İşlem reddedildi") if exc.diag else "İşlem reddedildi"
        raise HTTPException(status_code=409, detail=message) from exc
    except pg_errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="Kayıt zaten var") from exc
    except pg_errors.ForeignKeyViolation as exc:
        raise HTTPException(status_code=400, detail="Geçersiz şirket, hesap veya kategori") from exc
    except pg_errors.CheckViolation as exc:
        raise HTTPException(status_code=400, detail="Geçersiz değer") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def merged(model: type[ModelT], existing: dict[str, Any] | None, changes: dict[str, Any]) -> ModelT:
    """Mevcut satır + değişiklik → tam model; kurallar (CHECK karşılıkları) burada."""
    try:
        return model(**{**(existing or {}), **changes})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        text = first.get("msg", "Geçersiz değer").removeprefix("Value error, ")
        raise HTTPException(status_code=400, detail=f"{where}: {text}" if where else text) from exc


def _writable(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    unknown = set(payload) - store.WRITABLE[table]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Yazılamaz alan: {', '.join(sorted(unknown))}")
    return payload


def _values(record: BaseModel, keys: Iterable[str]) -> dict[str, Any]:
    data = record.model_dump()
    return {k: data[k] for k in keys}


# --- okuma ---------------------------------------------------------------------------


@router.get("/meta")
def meta() -> Any:
    with connect() as conn:
        bundle = views.Bundle(
            companies=store.load_companies(conn),
            categories=store.load_categories(conn),
            fx=load_fx(conn, _now().date()),
        )
    return jsonable_encoder(views.meta_view(bundle))


@router.get("/accounts")
def accounts(company: str = views.ALL) -> Any:
    with connect() as conn:
        bundle = views.Bundle(
            companies=store.load_companies(conn),
            accounts=store.load_accounts(conn),
            fx=load_fx(conn, _now().date()),
        )
    return jsonable_encoder(views.accounts_view(bundle, _scope(company, bundle.companies)))


@router.get("/overview")
def overview(company: str = views.ALL, start: str | None = None) -> Any:
    now = _now()
    first, _ = date_range(start, None, now.date())
    with connect() as conn:
        # Bakiye seyri başlangıç haftasının pazartesisinden bugüne hareket ister
        bundle = _bundle(conn, analytics.week_start(first))
    return jsonable_encoder(views.overview_view(bundle, _scope(company, bundle.companies), first, now))


@router.get("/flows")
def flows(
    company: str = views.ALL,
    start: str | None = None,
    end: str | None = None,
    kind: str = "all",
    category: str | None = None,
    account_id: int | None = None,
    q: str | None = None,
    limit: int = views.TX_PAGE,
    offset: int = 0,
) -> Any:
    if kind not in views.TX_KINDS:
        raise HTTPException(status_code=400, detail="Geçersiz hareket türü")
    first, last = date_range(start, end, _now().date())
    with connect() as conn:
        bundle = _bundle(conn, first, last)
    return jsonable_encoder(
        views.flows_view(
            bundle,
            _scope(company, bundle.companies),
            first,
            last,
            kind=kind,
            category=category,
            account_id=account_id,
            query=q,
            limit=limit,
            offset=max(0, offset),
        )
    )


@router.get("/projection")
def projection(
    company: str = views.ALL, weeks: int = 13, weighted: bool = True, floor: float = 0
) -> Any:
    if weeks not in (13, 26):
        raise HTTPException(status_code=400, detail="Hafta sayısı 13 veya 26 olmalı")
    today = _now().date()
    with connect() as conn:
        bundle = _bundle(conn, None)
    return jsonable_encoder(
        views.projection_view(
            bundle,
            _scope(company, bundle.companies),
            today,
            weeks=weeks,
            weighted=weighted,
            floor=Decimal(str(floor)),
        )
    )


@router.get("/intercompany")
def intercompany(company: str = views.ALL, start: str | None = None, end: str | None = None) -> Any:
    first, last = date_range(start, end, _now().date())
    # Eşleşme penceresi aralığın iki ucunda da çalışsın diye kenar payı
    margin = timedelta(days=analytics.MATCH_DAYS)
    with connect() as conn:
        bundle = _bundle(conn, first - margin, last + margin)
    return jsonable_encoder(
        views.intercompany_view(bundle, _scope(company, bundle.companies), first, last)
    )


def _snapshot_data(conn: Any) -> tuple[list[FinCompany], list[FinSnapshot], list[FinSnapshotLine]]:
    companies = store.load_companies(conn)
    snapshots = store.load_snapshots(conn)
    lines = store.load_snapshot_lines(conn, [s.id for s in snapshots if s.id is not None])
    return companies, snapshots, lines


@router.get("/snapshots")
def snapshots(company: str = views.ALL, week: str | None = None) -> Any:
    chosen = _day(week, "Hafta")
    with connect() as conn:
        companies, snaps, lines = _snapshot_data(conn)
    return jsonable_encoder(views.snapshots_view(snaps, lines, _scope(company, companies), chosen))


@router.get("/snapshots/summary")
def snapshot_summary(week: str, company: str = views.ALL) -> Any:
    chosen = _day(week, "Hafta")
    with connect() as conn:
        companies, snaps, lines = _snapshot_data(conn)
        decisions = store.load_decisions(conn)
    scope = _scope(company, companies)
    by_snapshot: dict[int, list[FinSnapshotLine]] = {}
    for line in lines:
        by_snapshot.setdefault(line.snapshot_id, []).append(line)
    text = mail_summary(
        chosen,
        [c for c in companies if c.id in scope],
        [s for s in snaps if s.company_id in scope],
        by_snapshot,
        [d for d in decisions if d.company_id is None or d.company_id in scope],
    )
    return {"text": text}


@router.get("/recurring")
def recurring_payments(company: str = views.ALL, start: str | None = None, end: str | None = None) -> Any:
    first, last = date_range(start, end, _now().date())
    with connect() as conn:
        bundle = _bundle(conn, first, last)
    return jsonable_encoder(views.recurring_view(bundle, _scope(company, bundle.companies), first, last))


@router.get("/tax")
def tax_screen(company: str = "llc", start: str | None = None, end: str | None = None) -> Any:
    """Cüzdanlar + kalan net varlık + vergi senaryoları (tek şirket)."""
    now = _now()
    first, last = date_range(start, end, now.date())
    with connect() as conn:
        companies = store.load_companies(conn)
        if company not in {c.id for c in companies}:
            raise HTTPException(status_code=400, detail="Vergi ekranı tek şirket için")
        # Ödenen vergi yıl başından; yüklemeler seçili aralıktan
        bundle = _bundle(conn, min(first, date(now.year, 1, 1)), max(last, now.date()))
        wallets = store.load_wallets(conn)
        settings = store.load_tax_settings(conn, company)
    return jsonable_encoder(views.tax_view(bundle, wallets, settings, company, first, last, now))


@router.get("/decisions")
def decisions(company: str = views.ALL) -> Any:
    with connect() as conn:
        companies = store.load_companies(conn)
        rows = store.load_decisions(conn)
    return jsonable_encoder(views.decisions_view(rows, _scope(company, companies), _now().date()))


# --- yazma: hareket ------------------------------------------------------------------


class TxPatch(BaseModel):
    category_id: str | None = None
    counterparty_company_id: CompanyId | None = None
    # Kategori seçilince aynı karşı taraf için kural oluştur ve benzerlerine uygula
    learn: bool = True


# Kural öğrenilmeyen kategoriler: şirketler arası karşı şirketle işaretlenir.
NO_LEARN = frozenset({analytics.IC_CATEGORY})


def _category_kinds(conn: Any) -> dict[str, str]:
    return analytics.category_kinds(store.load_categories(conn))


@router.patch("/transactions/{tx_id}")
def patch_transaction(tx_id: int, body: TxPatch) -> Any:
    """Kategori / şirketler arası düzeltmesi. Senkron bu satırı bir daha değiştirmez.

    learn: seçilen kategori aynı karşı taraf (ve yön) için kullanıcı kuralı
    olur; o karşı tarafın diğer kategorisiz hareketleri de hemen kategorilenir.
    """
    changes = body.model_dump(include=body.model_fields_set - {"learn"})
    similar = 0
    with connect() as conn, db_errors():
        tx = store.load_transaction(conn, tx_id)
        if tx is None:
            raise HTTPException(status_code=404, detail="Hareket bulunamadı")
        if changes.get("counterparty_company_id") == tx.company_id:
            raise HTTPException(status_code=400, detail="Karşı şirket hareketin kendi şirketi olamaz")
        if "category_id" not in changes and "counterparty_company_id" in changes:
            # Şirketler arası işareti kategoriyi de taşır
            if changes["counterparty_company_id"]:
                changes["category_id"] = analytics.IC_CATEGORY
            elif tx.category_id == analytics.IC_CATEGORY:
                changes["category_id"] = None
        changes["user_edited_at"] = _now()
        changes["category_rule_id"] = None
        store.update_row(conn, "fin_transactions", tx_id, changes)

        category = changes.get("category_id")
        key = analytics.tx_rule_key(tx)
        if body.learn and "category_id" in body.model_fields_set and category and category not in NO_LEARN and len(key) >= 2:
            direction = "in" if tx.amount > 0 else "out"
            if analytics.rule_direction_ok(direction, _category_kinds(conn).get(category, "")):
                store.upsert_rule(
                    conn,
                    FinCategoryRule(
                        company_id=tx.company_id,
                        direction=direction,
                        match_type="exact",
                        pattern=key,
                        category_id=category,
                        source="user",
                        label=(tx.counterparty or tx.description).strip()[:120],
                    ),
                )
                similar = store.apply_category_rules(conn)
        updated = store.load_transaction(conn, tx_id)
    return jsonable_encoder({**(updated.model_dump() if updated else {}), "similar": similar})


# --- kategori kuralları -----------------------------------------------------------------


class RuleBody(BaseModel):
    pattern: str
    match_type: Literal["exact", "contains"] = "contains"
    direction: Literal["in", "out", "any"] = "any"
    company_id: CompanyId | None = None
    category_id: str
    label: str | None = None  # gösterilecek özgün yazım (boşsa kalıp)


@router.get("/rules")
def rules() -> Any:
    with connect() as conn:
        items = store.load_rules(conn)
        usage = dict(
            conn.execute(
                "SELECT category_rule_id, count(*) FROM fin_transactions"
                " WHERE category_rule_id IS NOT NULL GROUP BY 1"
            ).fetchall()
        )
    return jsonable_encoder(
        {"rules": [{**r.model_dump(), "matched": int(usage.get(r.id, 0))} for r in items]}
    )


@router.post("/rules")
def create_rule(body: RuleBody) -> Any:
    """Kullanıcı kuralı; kayıtlı kategorisiz hareketlere hemen uygulanır."""
    pattern = analytics.rule_key(body.pattern)
    if len(pattern) < 2:
        raise HTTPException(status_code=400, detail="Kalıp en az 2 karakter olmalı")
    with connect() as conn, db_errors():
        kind = _category_kinds(conn).get(body.category_id)
        if kind is None:
            raise HTTPException(status_code=400, detail="Bilinmeyen kategori")
        if body.category_id in NO_LEARN:
            raise HTTPException(status_code=400, detail="Şirketler arası, karşı şirket seçilerek işaretlenir")
        if not analytics.rule_direction_ok(body.direction, kind):
            raise HTTPException(status_code=400, detail="Kategori, giriş / çıkış yönüyle uyuşmuyor")
        rule_id = store.upsert_rule(
            conn,
            FinCategoryRule(
                company_id=body.company_id,
                direction=body.direction,
                match_type=body.match_type,
                pattern=pattern + (" " if body.pattern.endswith(" ") and body.match_type == "contains" else ""),
                category_id=body.category_id,
                source="user",
                label=(body.label or body.pattern).strip()[:120],
            ),
        )
        applied = store.apply_category_rules(conn)
    return {"id": rule_id, "applied": applied}


@router.delete("/rules/{rule_id}")
def remove_rule(rule_id: int) -> Any:
    """Kuralı siler, verdiği kategorileri geri alır; kalanlara diğer kurallar uygulanır."""
    with connect() as conn, db_errors():
        reverted = store.delete_rule(conn, rule_id)
        if reverted is None:
            raise HTTPException(status_code=404, detail="Kural bulunamadı")
        reapplied = store.apply_category_rules(conn)
    return {"id": rule_id, "reverted": reverted, "reapplied": reapplied}


@router.get("/uncategorized")
def uncategorized(company: str = views.ALL, limit: int = 20) -> Any:
    """Kategorisiz karşı taraflar, USD hacmine göre."""
    with connect() as conn:
        companies = store.load_companies(conn)
        txs = store.load_uncategorized(conn)
        table = load_fx(conn, min((t.booked_at for t in txs), default=_now().date()))
    scope = set(_scope(company, companies))
    rows = analytics.top_uncategorized((t for t in txs if t.company_id in scope), table, max(1, min(limit, 100)))
    total = sum(1 for t in txs if t.company_id in scope)
    return jsonable_encoder({"items": rows, "total": total, "missing_fx": sorted(table.missing)})


# --- yazma: hesap --------------------------------------------------------------------


@router.post("/accounts")
def create_account(payload: dict[str, Any] = Body(...)) -> Any:
    _writable("fin_accounts", payload)
    account = merged(FinAccount, None, payload)
    values = _values(account, payload)
    if "current_balance" in values:
        values["balance_at"] = _now()
    with connect() as conn, db_errors():
        new_id = store.insert_row(conn, "fin_accounts", values)
    return {"id": new_id}


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, payload: dict[str, Any] = Body(...)) -> Any:
    _writable("fin_accounts", payload)
    with connect() as conn, db_errors():
        existing = store.load_row(conn, "fin_accounts", account_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Hesap bulunamadı")
        account = merged(FinAccount, existing, payload)
        values = _values(account, payload)
        if "current_balance" in values:
            if account.provider in API_PROVIDERS:
                raise HTTPException(status_code=400, detail="API hesabının bakiyesi senkronla güncellenir")
            values["balance_at"] = _now()
        store.update_row(conn, "fin_accounts", account_id, values)
    return {"id": account_id}


# --- yazma: planlı kalem ve karar ----------------------------------------------------


def _create(table: str, model: type[BaseModel], payload: dict[str, Any]) -> dict[str, int]:
    _writable(table, payload)
    record = merged(model, None, payload)
    with connect() as conn, db_errors():
        return {"id": store.insert_row(conn, table, _values(record, payload))}


def _patch(table: str, model: type[BaseModel], row_id: int, payload: dict[str, Any]) -> dict[str, int]:
    _writable(table, payload)
    with connect() as conn, db_errors():
        existing = store.load_row(conn, table, row_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
        record = merged(model, existing, payload)
        store.update_row(conn, table, row_id, _values(record, payload))
    return {"id": row_id}


def _delete(table: str, row_id: int) -> dict[str, int]:
    with connect() as conn, db_errors():
        if not store.delete_row(conn, table, row_id):
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return {"id": row_id}


@router.post("/planned")
def create_planned(payload: dict[str, Any] = Body(...)) -> Any:
    return _create("fin_planned_items", FinPlannedItem, payload)


@router.patch("/planned/{item_id}")
def patch_planned(item_id: int, payload: dict[str, Any] = Body(...)) -> Any:
    return _patch("fin_planned_items", FinPlannedItem, item_id, payload)


@router.delete("/planned/{item_id}")
def delete_planned(item_id: int) -> Any:
    return _delete("fin_planned_items", item_id)


@router.post("/decisions")
def create_decision(payload: dict[str, Any] = Body(...)) -> Any:
    return _create("fin_decisions", FinDecision, payload)


@router.patch("/decisions/{decision_id}")
def patch_decision(decision_id: int, payload: dict[str, Any] = Body(...)) -> Any:
    return _patch("fin_decisions", FinDecision, decision_id, payload)


@router.delete("/decisions/{decision_id}")
def delete_decision(decision_id: int) -> Any:
    return _delete("fin_decisions", decision_id)


# --- yazma: cüzdan ve vergi senaryosu -------------------------------------------------


def _wallet_stamp(payload: dict[str, Any]) -> dict[str, Any]:
    """Bakiye girilip tarih verilmediyse bakiye bugün itibarıyladır."""
    if "balance" in payload and not payload.get("as_of"):
        return {**payload, "as_of": _now().date().isoformat()}
    return payload


@router.post("/wallets")
def create_wallet(payload: dict[str, Any] = Body(...)) -> Any:
    return _create("fin_wallets", FinWallet, {"as_of": _now().date().isoformat(), **_wallet_stamp(payload)})


@router.patch("/wallets/{wallet_id}")
def patch_wallet(wallet_id: int, payload: dict[str, Any] = Body(...)) -> Any:
    return _patch("fin_wallets", FinWallet, wallet_id, _wallet_stamp(payload))


@router.delete("/wallets/{wallet_id}")
def delete_wallet(wallet_id: int) -> Any:
    return _delete("fin_wallets", wallet_id)


@router.put("/tax-settings/{company_id}")
def put_tax_settings(company_id: CompanyId, payload: dict[str, Any] = Body(...)) -> Any:
    """Vergi ayarının tamamı (yıl başı net varlık, yapı, ortaklar)."""
    if "company_id" in payload or "updated_at" in payload:
        raise HTTPException(status_code=400, detail="Yazılamaz alan")
    settings = merged(FinTaxSettings, None, {**payload, "company_id": company_id})
    with connect() as conn, db_errors():
        store.save_tax_settings(conn, settings)
    return {"company_id": company_id}


# --- yazma: haftalık bilanço ---------------------------------------------------------


class NewWeekBody(BaseModel):
    week_date: date
    company_ids: list[CompanyId] | None = None  # boşsa tüm şirketler
    copy_previous: bool = True
    # Hareketlerdeki şirketler arası net transferi alacak / borç olarak ekle
    include_intercompany: bool = False


@router.post("/snapshots")
def create_week(body: NewWeekBody) -> Any:
    """Yeni hafta: hesap bakiyeleri + açık planlı kalemler + cüzdanlar + tahmini
    vergi + önceki elle girilenler.

    Kurlar o anki son ECB kuruyla haftaya kilitlenir. include_intercompany:
    şirketin kendi kayıtlarındaki net transferler (bilanço tarihine kadar)
    şirketler arası alacak / borç satırı olur.
    """
    if body.week_date.isoweekday() != 1:
        raise HTTPException(status_code=400, detail="Hafta pazartesi olmalı")
    with connect() as conn, db_errors():
        companies = store.load_companies(conn)
        ids = body.company_ids or [c.id for c in sorted(companies, key=lambda c: c.sort)]
        existing = store.load_snapshots(conn)
        taken = [s.company_id for s in existing if s.week_date == body.week_date and s.company_id in ids]
        if taken:
            raise HTTPException(status_code=409, detail=f"Bu hafta zaten var: {', '.join(taken)}")
        accounts_ = store.load_accounts(conn)
        planned = store.load_planned(conn)
        fx_table = load_fx(conn, None if body.include_intercompany else body.week_date - timedelta(days=7))
        fx_rates = fx_table.latest_rates()
        ic_txs = store.load_transactions(conn, None, week_end(body.week_date)) if body.include_intercompany else []
        names = {c.id: c.name for c in companies}
        wallets = store.load_wallets(conn)
        tax_settings = {c: store.load_tax_settings(conn, c) for c in ids}
        created: list[int] = []
        for company_id in ids:
            previous: list[FinSnapshotLine] = []
            if body.copy_previous:
                prev = previous_snapshot(existing, company_id, body.week_date)
                if prev and prev.id is not None:
                    previous = store.load_snapshot_lines(conn, [prev.id])
            ic = intercompany_balances(company_id, ic_txs, fx_table, week_end(body.week_date), names)
            snapshot_id = store.insert_snapshot(conn, company_id, body.week_date, fx_rates)
            lines = build_week_lines(
                company_id,
                accounts_,
                planned,
                previous,
                body.week_date,
                ic,
                wallets=wallets,
                tax_due=_tax_due(tax_settings[company_id], accounts_, wallets, fx_table, body.week_date),
            )
            for line in lines:
                store.insert_line(conn, snapshot_id, line.__dict__)
            created.append(snapshot_id)
    return {"ids": created, "week_date": body.week_date}


def _tax_due(
    settings: FinTaxSettings,
    accounts_: list[FinAccount],
    wallets: list[FinWallet],
    fx_table: Any,
    week: date,
) -> list[tuple[str, Decimal]]:
    """Bugünkü yapıyla yıl başından bu haftaya kârın tahmini vergisi (USD).

    Yıl başı net varlık girilmemiş şirkette boş. FDII payı girilmediyse 0.
    """
    net = tax.net_assets(settings.company_id, accounts_, wallets, fx_table).net
    prof = tax.profit(net, settings, week_end(week))
    if prof is None:
        return []
    due = tax.structure_tax(prof.ytd, settings, settings.fdii_share or Decimal("0"))
    return [(f"Tahmini ABD vergisi ({tax.STRUCTURE_LABEL[settings.structure]})", due)]


class SnapshotPatch(BaseModel):
    notes: str


@router.patch("/snapshots/{snapshot_id}")
def patch_snapshot(snapshot_id: int, body: SnapshotPatch) -> Any:
    with connect() as conn, db_errors():
        if not store.update_row(conn, "fin_snapshots", snapshot_id, {"notes": body.notes}):
            raise HTTPException(status_code=404, detail="Bilanço bulunamadı")
    return {"id": snapshot_id}


@router.post("/snapshots/{snapshot_id}/lock")
def lock_snapshot(snapshot_id: int) -> Any:
    """Kesinleştir. Sonrası trigger ile değiştirilemez, kilidi açılamaz."""
    with connect() as conn, db_errors():
        if not store.update_row(conn, "fin_snapshots", snapshot_id, {"locked": True}):
            raise HTTPException(status_code=404, detail="Bilanço bulunamadı")
    return {"id": snapshot_id, "locked": True}


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: int) -> Any:
    return _delete("fin_snapshots", snapshot_id)


def _line_fx_check(snapshot: FinSnapshot, currency: str) -> None:
    if currency != "USD" and currency not in snapshot.fx:
        raise HTTPException(status_code=400, detail=f"{currency} kuru bu haftaya kilitlenmemiş")


class LineBody(BaseModel):
    section: SnapshotSection
    group_name: str
    label: str
    amount: Money
    currency: Currency
    sort: int = 0


@router.post("/snapshots/{snapshot_id}/lines")
def add_line(snapshot_id: int, body: LineBody) -> Any:
    """Elle satır; taraf bölümden gelir (özkaynak pasif tarafındadır)."""
    if not body.group_name.strip() or not body.label.strip():
        raise HTTPException(status_code=400, detail="Grup ve kalem adı gerekli")
    with connect() as conn, db_errors():
        snapshot = store.load_snapshot(conn, snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Bilanço bulunamadı")
        _line_fx_check(snapshot, body.currency)
        values = {**body.model_dump(), "side": side_of(body.section), "origin": "manual"}
        line_id = store.insert_line(conn, snapshot_id, values)
    return {"id": line_id}


@router.patch("/snapshot-lines/{line_id}")
def patch_line(line_id: int, payload: dict[str, Any] = Body(...)) -> Any:
    _writable("fin_snapshot_lines", payload)
    with connect() as conn, db_errors():
        line = store.load_line(conn, line_id)
        if line is None:
            raise HTTPException(status_code=404, detail="Satır bulunamadı")
        # Bölüm değişirse taraf da ona uyar (özkaynak pasiftir)
        if payload.get("section") in SECTION_BY_KEY and "side" not in payload:
            payload = {**payload, "side": side_of(payload["section"])}
        record = merged(FinSnapshotLine, line.model_dump(), payload)
        snapshot = store.load_snapshot(conn, line.snapshot_id)
        if snapshot is not None:
            _line_fx_check(snapshot, record.currency)
        store.update_row(conn, "fin_snapshot_lines", line_id, _values(record, payload))
    return {"id": line_id}


@router.delete("/snapshot-lines/{line_id}")
def delete_line(line_id: int) -> Any:
    return _delete("fin_snapshot_lines", line_id)
