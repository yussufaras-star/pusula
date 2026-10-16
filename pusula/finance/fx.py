"""Kurlar: 1 birim para = usd_rate USD.

Kaynak ECB referans kurları (Frankfurter API). Hareketler işlem tarihindeki
kurla, güncel bakiyeler son kurla çevrilir. Bir tarih için kur yoksa o
günden önceki en yakın kur kullanılır (hafta sonu, tatil). Hiç kur yoksa
uydurma değer kullanılmaz: tutar 0 sayılır ve para birimi `missing`'e
yazılır; arayüz "kur verisi yetersiz" gösterir.
"""

from __future__ import annotations

import bisect
import logging
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

import httpx
import psycopg

from pusula.db.models import FinFxRate

logger = logging.getLogger(__name__)

FX_CURRENCIES: tuple[str, ...] = ("EUR", "GBP", "TRY")
FRANKFURTER_URL = "https://api.frankfurter.dev/v1"
HTTP_TIMEOUT = 15.0
ONE = Decimal("1")


class FxTable:
    """Para birimi → tarihe göre sıralı (tarih, usd_rate) listesi."""

    def __init__(self, rows: Iterable[FinFxRate]) -> None:
        by_cur: dict[str, dict[date, Decimal]] = {}
        for row in rows:
            by_cur.setdefault(row.currency, {})[row.date] = row.usd_rate
        self._dates: dict[str, list[date]] = {}
        self._rates: dict[str, list[Decimal]] = {}
        for cur, values in by_cur.items():
            days = sorted(values)
            self._dates[cur] = days
            self._rates[cur] = [values[d] for d in days]
        # Kur bulunamayan para birimleri; çağıran uyarı olarak gösterir.
        self.missing: set[str] = set()

    def rate(self, currency: str, day: date | None = None) -> Decimal | None:
        """O günün veya öncesindeki en yakın kur; day None ise son kur.

        Tarih ilk kurdan önceyse ilk kur kullanılır (geçmiş veri eksik).
        """
        if currency == "USD":
            return ONE
        days = self._dates.get(currency)
        if not days:
            return None
        rates = self._rates[currency]
        if day is None:
            return rates[-1]
        index = bisect.bisect_right(days, day) - 1
        return rates[max(index, 0)]

    def to_usd(self, amount: Decimal, currency: str, day: date | None = None) -> Decimal:
        rate = self.rate(currency, day)
        if rate is None:
            self.missing.add(currency)
            return Decimal("0")
        return Decimal(amount) * rate

    def latest_date(self) -> date | None:
        last = [days[-1] for days in self._dates.values() if days]
        return max(last) if last else None

    def latest_rates(self, currencies: Iterable[str] = FX_CURRENCIES) -> dict[str, Decimal]:
        """Bilanço kilidi için: bilinen her para biriminin son kuru."""
        out: dict[str, Decimal] = {}
        for cur in currencies:
            rate = self.rate(cur)
            if rate is not None:
                out[cur] = rate
        return out


# --- ECB (Frankfurter) --------------------------------------------------------


def _usd_rate(units_per_usd: object) -> Decimal | None:
    # Frankfurter base=USD: 1 USD = r birim → 1 birim = 1/r USD
    try:
        value = Decimal(str(units_per_usd))
    except ArithmeticError:
        return None
    if value <= 0:
        return None
    return (ONE / value).quantize(Decimal("0.00000001"))


def parse_frankfurter(payload: dict) -> list[FinFxRate]:
    """Tek gün (`date` + `rates`) veya aralık (`rates`: tarih → kurlar) yanıtı."""
    rates = payload.get("rates") or {}
    if "date" in payload:
        by_day = {payload["date"]: rates}
    else:
        by_day = rates
    out: list[FinFxRate] = []
    for raw_day, values in by_day.items():
        day = date.fromisoformat(raw_day)
        for cur, units in (values or {}).items():
            if cur not in FX_CURRENCIES:
                continue
            usd = _usd_rate(units)
            if usd is not None:
                out.append(FinFxRate(date=day, currency=cur, usd_rate=usd, source="ecb"))
    return out


def fetch_ecb_rates(start: date | None = None, end: date | None = None) -> list[FinFxRate]:
    """Son kurlar; start verilirse [start, end] aralığının tüm iş günleri."""
    symbols = ",".join(FX_CURRENCIES)
    if start is None:
        path = "latest"
    else:
        path = f"{start.isoformat()}..{end.isoformat() if end else ''}"
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.get(f"{FRANKFURTER_URL}/{path}", params={"base": "USD", "symbols": symbols})
        response.raise_for_status()
        return parse_frankfurter(response.json())


def load_fx(conn: psycopg.Connection, since: date | None = None) -> FxTable:
    """since verilirse o tarihten önceki son kur da gelir (aralığın başı için)."""
    if since is None:
        rows = conn.execute("SELECT date, currency, usd_rate, source FROM fin_fx_rates").fetchall()
    else:
        rows = conn.execute(
            """
            SELECT date, currency, usd_rate, source FROM fin_fx_rates WHERE date >= %s
            UNION ALL
            (
                SELECT DISTINCT ON (currency) date, currency, usd_rate, source
                FROM fin_fx_rates WHERE date < %s
                ORDER BY currency, date DESC
            )
            """,
            (since, since),
        ).fetchall()
    return FxTable(
        FinFxRate(date=r[0], currency=r[1], usd_rate=r[2], source=r[3]) for r in rows
    )


def save_rates(conn: psycopg.Connection, rates: Iterable[FinFxRate]) -> int:
    """Upsert; aynı gün tekrar çekilirse ECB düzeltmesi yazılır."""
    count = 0
    with conn.cursor() as cur:
        for rate in rates:
            cur.execute(
                """
                INSERT INTO fin_fx_rates (date, currency, usd_rate, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (date, currency)
                DO UPDATE SET usd_rate = EXCLUDED.usd_rate, source = EXCLUDED.source
                """,
                (rate.date, rate.currency, rate.usd_rate, rate.source),
            )
            count += 1
    return count
