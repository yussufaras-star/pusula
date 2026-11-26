"""Finans uyarıları: ilk kez çekilen abonelik, olağan dışı transfer ve
Rexven'e işlenmemiş üretici ödemesi.

Her senkrondan sonra çalışır; bir hareket en fazla bir uyarı üretir
(üretici > abonelik > olağan dışı). Uyarı (tür, hareket) başına tekildir:
kullanıcının verdiği karar sonraki taramada ezilmez.

Karar:
- güven   → uyarı kapanır (pasif).
- riskli  → seçilen aralık sonunda yeniden açılır ve yeniden bildirilir.
Üretici uyarısı, ödeme sonradan Rexven'e işlenirse kendiliğinden kapanır.

Üretici ödemesi: 'uretici' / 'tedarikci' kategorisindeki çıkış, aynı para
biriminde aynı tutarlı bir supplierPayout kaydıyla (banka gününden PAYOUT_BEFORE
gün önce ile PAYOUT_AFTER gün sonrası arası) eşleşmeli. Rexven yalnız NetBird'e
bağlı bilgisayardan okunur; ödemeler fin_producer_payouts'a oradan yazılır.
Tarama, banka gününden PRODUCER_GRACE gün sonrasını kapsamadıkça uyarı açılmaz.
"""

from __future__ import annotations

import logging
import os
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from pusula.db.models import FinAccount, FinTransaction
from pusula.finance.analytics import is_transfer, tx_rule_key
from pusula.finance.fx import FxTable
from pusula.finance.providers.rexven import Payout, SupplierWallet

logger = logging.getLogger(__name__)

KINDS = ("uretici", "abonelik", "olagandisi")
LOOKBACK_DAYS = 14  # abonelik / olağan dışı: bu kadar günlük hareketler taranır
HISTORY_DAYS = 365  # alıcının "normali" bu pencereden
NEW_PAYEE_USD = Decimal("1000")
MULTIPLE = Decimal("3")
BIG_USD = Decimal("25000")
SUBSCRIPTION_CATEGORIES = frozenset({"yazilim"})
PRODUCER_CATEGORIES = frozenset({"uretici", "tedarikci"})
PRODUCER_LOOKBACK = 45
PRODUCER_GRACE = 3
PAYOUT_BEFORE = 7
PAYOUT_AFTER = 10
PAYOUT_TOLERANCE = Decimal("0.005")  # tutar farkı (oran); en az 1 birim
REMIND_DAYS = (1, 3, 7, 14, 30)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_alerts (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('uretici', 'abonelik', 'olagandisi')),
    tx_id       bigint NOT NULL REFERENCES fin_transactions (id) ON DELETE CASCADE,
    reason      text NOT NULL,
    status      text NOT NULL DEFAULT 'acik' CHECK (status IN ('acik', 'guven', 'riskli', 'kapandi')),
    remind_at   timestamptz,
    decided_at  timestamptz,
    notified_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, tx_id)
);
CREATE INDEX IF NOT EXISTS fin_alerts_status ON fin_alerts (status);

CREATE TABLE IF NOT EXISTS fin_producer_payouts (
    log_id      bigint PRIMARY KEY,
    wallet_id   bigint NOT NULL,
    user_id     bigint NOT NULL,
    currency    text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    amount      numeric(18, 2) NOT NULL,
    paid_at     timestamptz NOT NULL,
    description text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS fin_producer_payouts_paid ON fin_producer_payouts (paid_at);

CREATE TABLE IF NOT EXISTS fin_payout_scans (
    wallet_id     bigint PRIMARY KEY,
    producer      text NOT NULL DEFAULT '',
    currency      text NOT NULL,
    scanned_until timestamptz NOT NULL
);

ALTER TABLE fin_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin_producer_payouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin_payout_scans ENABLE ROW LEVEL SECURITY;
"""


def ensure_schema(conn: psycopg.Connection) -> None:
    row = conn.execute("SELECT to_regclass('fin_payout_scans')").fetchone()
    if row is None or row[0] is None:
        conn.execute(SCHEMA_SQL)


# --- tespit (saf) ------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    kind: str
    tx_id: int
    reason: str


def _usd(tx: FinTransaction, fx: FxTable) -> Decimal:
    return fx.to_usd(abs(tx.amount), tx.currency, tx.booked_at)


def _money(value: Decimal) -> str:
    return f"{value:,.0f} $".replace(",", ".")


def _outflows(txs: Iterable[FinTransaction], kinds: Mapping[str, str]) -> list[FinTransaction]:
    out = [t for t in txs if t.id is not None and t.amount < 0 and t.category_id != "cuzdan" and not is_transfer(t, kinds)]
    return sorted(out, key=lambda t: (t.booked_at, t.id or 0))


def payout_matches(
    payments: Sequence[FinTransaction], payouts: Sequence[Payout]
) -> dict[int, Payout]:
    """Banka ödemesi → Rexven ödemesi; her Rexven kaydı bir kez kullanılır,
    tarihi en yakın olan seçilir."""
    used: set[int] = set()
    out: dict[int, Payout] = {}
    for tx in sorted(payments, key=lambda t: (t.booked_at, t.id or 0)):
        amount = abs(tx.amount)
        tolerance = max(Decimal("1"), amount * PAYOUT_TOLERANCE)
        best: tuple[int, Payout] | None = None
        for p in payouts:
            if p.log_id in used or p.currency != tx.currency or abs(p.amount - amount) > tolerance:
                continue
            gap = (p.paid_at.date() - tx.booked_at).days
            if -PAYOUT_BEFORE <= gap <= PAYOUT_AFTER and (best is None or abs(gap) < best[0]):
                best = (abs(gap), p)
        if best is not None:
            used.add(best[1].log_id)
            out[tx.id or 0] = best[1]
    return out


def detect(
    txs: Sequence[FinTransaction],
    accounts: Sequence[FinAccount],
    kinds: Mapping[str, str],
    fx: FxTable,
    today: date,
    payouts: Sequence[Payout] = (),
    scanned_until: date | None = None,
) -> tuple[list[Candidate], set[int]]:
    """Uyarı adayları ve Rexven'de karşılığı bulunan üretici ödemeleri (tx id).

    txs tüm geçmişi içermeli: "ilk kez" ve "normal" geçmişten hesaplanır.
    scanned_until None ise (Rexven taraması hiç yapılmadı) üretici denetimi atlanır.
    """
    card = {a.id for a in accounts if a.type == "card"}
    outflows = _outflows(txs, kinds)
    out: list[Candidate] = []
    taken: set[int] = set()

    producer = [t for t in outflows if t.category_id in PRODUCER_CATEGORIES and t.booked_at >= today - timedelta(days=PRODUCER_LOOKBACK)]
    matched = payout_matches(producer, payouts)
    if scanned_until is not None:
        for t in producer:
            tid = t.id or 0
            taken.add(tid)
            if tid in matched or t.booked_at > scanned_until - timedelta(days=PRODUCER_GRACE):
                continue
            out.append(Candidate("uretici", tid, "Bankadan çıktı, Rexven'de üretici cüzdanından düşülmedi"))
    else:
        taken.update(t.id or 0 for t in producer)

    since = today - timedelta(days=LOOKBACK_DAYS)
    history: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for t in outflows:
        key = tx_rule_key(t)
        usd = _usd(t, fx)
        past = [u for d, u in history[key] if d >= t.booked_at - timedelta(days=HISTORY_DAYS)]
        first_ever = not history[key]
        history[key].append((t.booked_at, usd))
        tid = t.id or 0
        if t.booked_at < since or tid in taken or not key:
            continue
        if first_ever and (t.category_id in SUBSCRIPTION_CATEGORIES or (t.account_id in card and t.category_id is None)):
            out.append(Candidate("abonelik", tid, "İlk kez çekilen abonelik / kart ödemesi"))
            continue
        reasons = []
        if first_ever and usd >= NEW_PAYEE_USD:
            reasons.append(f"Yeni alıcıya ilk ödeme ({_money(usd)})")
        elif len(past) >= 2 and usd >= NEW_PAYEE_USD:
            median = Decimal(statistics.median(past))
            if median > 0 and usd >= MULTIPLE * median:
                reasons.append(f"Alıcının normalinin {usd / median:.1f} katı (ortanca {_money(median)})")
        if usd >= BIG_USD:
            reasons.append(f"Tek seferde büyük çıkış ({_money(usd)})")
        if reasons:
            out.append(Candidate("olagandisi", tid, "; ".join(reasons)))
    return out, set(matched)


# --- veritabanı ----------------------------------------------------------------------------


def save_payouts(conn: psycopg.Connection, wallet: SupplierWallet, payouts: Sequence[Payout], until: datetime) -> int:
    added = 0
    for p in payouts:
        cur = conn.execute(
            """
            INSERT INTO fin_producer_payouts (log_id, wallet_id, user_id, currency, amount, paid_at, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (log_id) DO NOTHING
            """,
            (p.log_id, p.wallet_id, p.user_id, p.currency, p.amount, p.paid_at, p.description),
        )
        added += cur.rowcount
    conn.execute(
        """
        INSERT INTO fin_payout_scans (wallet_id, producer, currency, scanned_until) VALUES (%s, %s, %s, %s)
        ON CONFLICT (wallet_id) DO UPDATE SET producer = EXCLUDED.producer, scanned_until = EXCLUDED.scanned_until
        """,
        (wallet.wallet_id, wallet.name, wallet.currency, until),
    )
    return added


def scan_starts(conn: psycopg.Connection) -> dict[int, datetime]:
    return {int(r[0]): r[1] for r in conn.execute("SELECT wallet_id, scanned_until FROM fin_payout_scans")}


def load_payouts(conn: psycopg.Connection, since: date) -> tuple[list[Payout], date | None]:
    rows = conn.execute(
        "SELECT log_id, wallet_id, user_id, currency, amount, paid_at, description FROM fin_producer_payouts WHERE paid_at >= %s",
        (since,),
    ).fetchall()
    scanned = conn.execute("SELECT min(scanned_until) FROM fin_payout_scans").fetchone()
    until = scanned[0].date() if scanned and scanned[0] else None
    return [Payout(*r) for r in rows], until


def refresh(
    conn: psycopg.Connection,
    txs: Sequence[FinTransaction],
    accounts: Sequence[FinAccount],
    kinds: Mapping[str, str],
    fx: FxTable,
    today: date,
) -> dict[str, int]:
    """Yeni uyarıları ekler, Rexven'de karşılığı çıkan üretici uyarılarını kapatır."""
    ensure_schema(conn)
    payouts, scanned = load_payouts(conn, today - timedelta(days=PRODUCER_LOOKBACK + PAYOUT_BEFORE))
    candidates, matched = detect(txs, accounts, kinds, fx, today, payouts, scanned)
    added = 0
    for c in candidates:
        cur = conn.execute(
            "INSERT INTO fin_alerts (kind, tx_id, reason) VALUES (%s, %s, %s) ON CONFLICT (kind, tx_id) DO NOTHING",
            (c.kind, c.tx_id, c.reason),
        )
        added += cur.rowcount
    closed = 0
    if matched:
        cur = conn.execute(
            """
            UPDATE fin_alerts SET status = 'kapandi', decided_at = now(), remind_at = NULL
            WHERE kind = 'uretici' AND status IN ('acik', 'riskli') AND tx_id = ANY(%s)
            """,
            (list(matched),),
        )
        closed = cur.rowcount
    return {"added": added, "closed": closed}


ALERT_SQL = """
SELECT a.id, a.kind, a.reason, a.status, a.remind_at, a.decided_at, a.created_at,
       t.id AS tx_id, t.booked_at, t.amount, t.currency, t.counterparty, t.description,
       t.company_id, t.category_id, acc.name AS account
FROM fin_alerts a
JOIN fin_transactions t ON t.id = a.tx_id
JOIN fin_accounts acc ON acc.id = t.account_id
"""


def due(row: Mapping[str, Any], now: datetime) -> bool:
    """Karar bekleyen: açık ya da hatırlatma zamanı gelmiş riskli."""
    return row["status"] == "acik" or (row["status"] == "riskli" and row["remind_at"] is not None and row["remind_at"] <= now)


def list_alerts(conn: psycopg.Connection, now: datetime, scope: Sequence[str]) -> list[dict[str, Any]]:
    ensure_schema(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            ALERT_SQL + " WHERE t.company_id = ANY(%s) ORDER BY t.booked_at DESC, a.id DESC LIMIT 500", (list(scope),)
        ).fetchall()
    return [r | {"due": due(r, now)} for r in rows]


def decide(conn: psycopg.Connection, alert_id: int, action: str, remind_days: int | None, now: datetime) -> bool:
    ensure_schema(conn)
    if action == "guven":
        cur = conn.execute(
            "UPDATE fin_alerts SET status = 'guven', remind_at = NULL, decided_at = %s WHERE id = %s", (now, alert_id)
        )
    else:
        cur = conn.execute(
            "UPDATE fin_alerts SET status = 'riskli', remind_at = %s, decided_at = %s WHERE id = %s",
            (now + timedelta(days=remind_days or REMIND_DAYS[0]), now, alert_id),
        )
    return cur.rowcount > 0


# --- bildirim ------------------------------------------------------------------------------

KIND_LABEL = {"uretici": "Üretici ödemesi", "abonelik": "Yeni abonelik", "olagandisi": "Olağan dışı transfer"}
NOTIFY_MAX = 8


def recipients() -> str:
    return os.environ.get("FIN_ALERT_CLIQ_USERS", "").strip()


def pending_notices(conn: psycopg.Connection, now: datetime) -> list[dict[str, Any]]:
    """Bildirilmemiş açık uyarılar ve hatırlatması gelip henüz bildirilmemiş riskliler."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            ALERT_SQL
            + """
            WHERE (a.status = 'acik' AND a.notified_at IS NULL)
               OR (a.status = 'riskli' AND a.remind_at <= %s AND (a.notified_at IS NULL OR a.notified_at < a.remind_at))
            ORDER BY t.booked_at, a.id
            """,
            (now,),
        ).fetchall()


def _amount(row: Mapping[str, Any]) -> str:
    value = f"{abs(Decimal(row['amount'])):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{value} {row['currency']}"


def notice_text(rows: Sequence[Mapping[str, Any]], panel_url: str) -> str:
    lines = [f"Finans uyarısı: {len(rows)} hareket karar bekliyor"]
    for r in rows[:NOTIFY_MAX]:
        who = r["counterparty"] or (r["description"] or "")[:40]
        again = " (hatırlatma)" if r["status"] == "riskli" else ""
        lines.append(f"• {KIND_LABEL[r['kind']]}{again}: {r['booked_at']:%d.%m} {r['account']} → {who}, {_amount(r)}. {r['reason']}")
    if len(rows) > NOTIFY_MAX:
        lines.append(f"…ve {len(rows) - NOTIFY_MAX} uyarı daha")
    if panel_url:
        lines.append(f"Güven / Riskli: {panel_url.rstrip('/')}/#/finans/uyarilar")
    return "\n".join(lines)


def notify(conn: psycopg.Connection, now: datetime, send: Any) -> int:
    """send(text, userids): Cliq'e kişisel mesaj. Alıcı tanımlı değilse hiçbir şey yapmaz."""
    users = recipients()
    if not users:
        return 0
    rows = pending_notices(conn, now)
    if not rows:
        return 0
    send(notice_text(rows, os.environ.get("PUSULA_PANEL_URL", "")), users)
    conn.execute("UPDATE fin_alerts SET notified_at = %s WHERE id = ANY(%s)", (now, [r["id"] for r in rows]))
    return len(rows)
