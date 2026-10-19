"""Gelen lead akışı: Zoho Leads modülüne günde kaç lead (ve kaç yeni kişi) düştü.

Sayım Created_Time'a göredir ve contact'a dönüştürülmüş lead'leri de içerir
(Zoho dönüştürülenleri varsayılan sorguda gizler; 2026 Q1'de lead'ler
contact'a çevriliyordu). CRM taşımasında 1-2 Nisan 2026'da toplu içe
aktarılan ~16,6 bin eski kayıt akış değildir; o günler is_import ile
işaretlenir, toplamlara ve kişi eşleştirmesine girmez.

Tekil kişi (yönetici, 19 Ekim): aynı e-posta ya da telefon yıl içinde daha
önce geldiyse kayıt ikilidir. Q1'de form çift gönderimi ~%9,5 ikili
üretiyordu. Anahtarlar lead_inflow_keys'te en erken (zaman, lead id) ile
tutulur; aynı günler yeniden sayılınca sonuç değişmez.

Ekip Panosu'ndaki "gelen lead" anahtar sonucu n_unique'ten okunur. Tablolar
ilk yazmada oluşur; yoksa okuma None döner.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from pusula.config import get_org_id
from pusula.ingest.crm_actions import parse_zoho_time
from pusula.panel_data import connect

IMPORT_DAYS = frozenset({date(2026, 4, 1), date(2026, 4, 2)})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lead_inflow_daily (
    org_id      text NOT NULL DEFAULT 'rexven',
    day         date NOT NULL,
    n           int NOT NULL,
    converted   int NOT NULL DEFAULT 0,
    is_import   boolean NOT NULL DEFAULT false,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, day)
);
ALTER TABLE lead_inflow_daily ADD COLUMN IF NOT EXISTS n_unique int;
ALTER TABLE lead_inflow_daily ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS lead_inflow_keys (
    org_id    text NOT NULL DEFAULT 'rexven',
    key       text NOT NULL,
    lead_id   text NOT NULL,
    first_at  timestamptz NOT NULL,
    PRIMARY KEY (org_id, key)
);
ALTER TABLE lead_inflow_keys ENABLE ROW LEVEL SECURITY;
"""


@dataclass(frozen=True)
class LeadRec:
    lead_id: str
    at: datetime
    keys: tuple[str, ...]
    converted: bool

    @property
    def day(self) -> date:
        return self.at.date()


def norm_phone(value: Any) -> str | None:
    """Son 10 hane (90 / 0 öneki atılır); kısa numara kimlik sayılmaz."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if len(digits) >= 10 else None


def person_keys(record: dict[str, Any]) -> tuple[str, ...]:
    keys = []
    email = str(record.get("Email") or "").strip().lower()
    if "@" in email:
        keys.append(f"e:{email}")
    for field in ("Mobile", "Phone"):
        phone = norm_phone(record.get(field))
        if phone and f"p:{phone}" not in keys:
            keys.append(f"p:{phone}")
    return tuple(keys)


def to_rec(record: dict[str, Any], converted: bool) -> LeadRec | None:
    at = parse_zoho_time(record.get("Created_Time"))
    if at is None or not record.get("id"):
        return None
    return LeadRec(str(record["id"]), at, person_keys(record), converted)


def bucket(created: Iterable[Any]) -> Counter[date]:
    """Zoho Created_Time değerlerini İstanbul gününe sayar."""
    out: Counter[date] = Counter()
    for value in created:
        moment = parse_zoho_time(value)
        if moment is not None:
            out[moment.date()] += 1
    return out


def mark_unique(
    records: list[LeadRec], stored: dict[str, tuple[datetime, str]]
) -> tuple[dict[str, bool], dict[str, tuple[datetime, str]]]:
    """Saf: (lead id → yeni kişi mi, güncellenecek anahtarlar).

    Kayıt, anahtarlarından biri kendisinden önce (zaman, id sırası) başka
    bir lead'de görülmüşse ikilidir. stored önceki çalışmaların en erken
    kayıtlarıdır; dönen anahtarlar en erken değere göre birleşir.
    """
    best = dict(stored)
    for rec in records:
        for key in rec.keys:
            mine = (rec.at, rec.lead_id)
            if key not in best or mine < best[key]:
                best[key] = mine
    flags = {}
    for rec in records:
        mine = (rec.at, rec.lead_id)
        flags[rec.lead_id] = not any(best[key] < mine for key in rec.keys)
    changed = {key: value for key, value in best.items() if stored.get(key) != value}
    return flags, changed


def day_rows(
    first: date, last: date, records: list[LeadRec], unique: dict[str, bool]
) -> list[tuple[date, int, int, int, bool]]:
    """Aralığın her günü (sıfırlar dahil): (gün, toplam, tekil, dönüştürülen, içe aktarım mı)."""
    total: Counter[date] = Counter()
    uniq: Counter[date] = Counter()
    conv: Counter[date] = Counter()
    for rec in records:
        total[rec.day] += 1
        uniq[rec.day] += unique.get(rec.lead_id, True)
        conv[rec.day] += rec.converted
    rows = []
    day = first
    while day <= last:
        is_import = day in IMPORT_DAYS
        rows.append((day, total[day], 0 if is_import else uniq[day], conv[day], is_import))
        day += timedelta(days=1)
    return rows


def _month_chunks(first: date, last: date) -> list[tuple[date, date]]:
    out = []
    start = first
    while start <= last:
        nxt = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
        out.append((start, min(last, nxt - timedelta(days=1))))
        start = nxt
    return out


def fetch(first: date, last: date) -> list[LeadRec]:
    """Aralıktaki tüm lead'ler (dönüştürülenler dahil). Yalnız okuma."""
    from pusula.zoho import coql

    out: list[LeadRec] = []
    for start, end in _month_chunks(first, last):
        a = f"{start.isoformat()}T00:00:00+03:00"
        b = f"{(end + timedelta(days=1)).isoformat()}T00:00:00+03:00"
        where = f"Created_Time between '{a}' and '{b}'"
        fields = "id, Created_Time, Email, Phone, Mobile"
        for converted, cond in ((False, ""), (True, "Converted__s = true and ")):
            for record in coql(f"select {fields} from Leads where {cond}{where}"):
                rec = to_rec(record, converted)
                # between iki ucu da kapsar; ertesi günün 00:00 kaydı sonraki parçaya aittir.
                if rec is not None and start <= rec.day <= end:
                    out.append(rec)
    return out


def _stored_keys(conn: Any, keys: list[str]) -> dict[str, tuple[datetime, str]]:
    out: dict[str, tuple[datetime, str]] = {}
    for i in range(0, len(keys), 5000):
        rows = conn.execute(
            "SELECT key, first_at, lead_id FROM lead_inflow_keys WHERE org_id = %s AND key = ANY(%s)",
            (get_org_id(), keys[i : i + 5000]),
        ).fetchall()
        out.update({key: (at, lead_id) for key, at, lead_id in rows})
    return out


def _keys_empty(conn: Any) -> bool:
    row = conn.execute(
        "SELECT 1 FROM lead_inflow_keys WHERE org_id = %s LIMIT 1", (get_org_id(),)
    ).fetchone()
    return row is None


def sync(today: date, *, since: date | None = None, lookback_days: int = 3) -> dict[str, Any]:
    """Kişi anahtarları boşsa yıl başından, değilse son birkaç günü yeniden sayar."""
    org = get_org_id()
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        if since is None:
            since = date(today.year, 1, 1) if _keys_empty(conn) else today - timedelta(days=lookback_days)
    records = sorted(fetch(since, today), key=lambda r: (r.at, r.lead_id))
    flow = [r for r in records if r.day not in IMPORT_DAYS]
    with connect() as conn:
        stored = _stored_keys(conn, sorted({k for r in flow for k in r.keys}))
        unique, changed = mark_unique(flow, stored)
        rows = day_rows(since, today, records, unique)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO lead_inflow_keys (org_id, key, lead_id, first_at) VALUES (%s, %s, %s, %s)
                ON CONFLICT (org_id, key) DO UPDATE SET lead_id = EXCLUDED.lead_id, first_at = EXCLUDED.first_at
                WHERE (EXCLUDED.first_at, EXCLUDED.lead_id) < (lead_inflow_keys.first_at, lead_inflow_keys.lead_id)
                """,
                [(org, key, lead_id, at) for key, (at, lead_id) in changed.items()],
            )
            cur.executemany(
                """
                INSERT INTO lead_inflow_daily (org_id, day, n, n_unique, converted, is_import, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (org_id, day) DO UPDATE
                SET n = EXCLUDED.n, n_unique = EXCLUDED.n_unique, converted = EXCLUDED.converted,
                    is_import = EXCLUDED.is_import, updated_at = now()
                """,
                [(org, day, n, n_unique, conv, is_import) for day, n, n_unique, conv, is_import in rows],
            )
    return {
        "since": since.isoformat(),
        "days": len(rows),
        "leads": sum(r[1] for r in rows if not r[4]),
        "unique": sum(r[2] for r in rows),
        "import_excluded": sum(r[1] for r in rows if r[4]),
    }


def load_inflow(first: date, last: date) -> int | None:
    """Aralıkta gelen tekil kişi (içe aktarım günleri hariç). Tablo aralığın
    başını kapsamıyorsa ya da tekil sayım henüz yoksa None."""
    with connect() as conn:
        # n_unique sütunu ilk tekil sayımda eklenir; o zamana kadar veri yok.
        exists = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'lead_inflow_daily' AND column_name = 'n_unique'"
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(
            """
            SELECT min(day), count(*) FILTER (WHERE n_unique IS NULL),
                   coalesce(sum(n_unique) FILTER (WHERE NOT is_import), 0)
            FROM lead_inflow_daily WHERE org_id = %s AND day BETWEEN %s AND %s
            """,
            (get_org_id(), first, last),
        ).fetchone()
    if not row or row[0] is None or row[0] > first or row[1]:
        return None
    return int(row[2])


def now_istanbul() -> datetime:
    from pusula.metrics import TZ

    return datetime.now(TZ)
