"""Personel maliyeti (maaş yükü): çalışan başına işverene toplam maliyet.

Şirketlerin bordro raporundaki gibi:
    net ödenen + çalışan kesintileri (SGK/sigorta, gelir ve damga vergisi) = brüt
    brüt + işveren payı (SGK/sigorta) + yan hak (yemek kartı) + kıdem karşılığı
        = toplam işveren maliyeti

Satırlar (fin_payroll) ödeme kanalıdır; bir kişinin birden çok satırı olabilir
(ör. AŞ bordrosunda asgari ücret + LLC'den USD). Tür:
- bordro / tr   : Türkiye bordrosu. 2026 gelir vergisi tarifesi, asgari ücret
                  istisnası, SGK tavanı; yıllık ortalama (kümülatif matrah aylara
                  yayılır). Kıdem karşılığı brüt/12, kıdem tavanıyla sınırlı.
- bordro / gmbh : Almanya bordrosu. Vergi sınıfı I, kilise vergisi yok, çocuksuz;
                  §32a tarifesi, 2026 sigorta tavanları, U1/U2/BG ek yükü.
- sozlesme      : fatura / sözleşme / freelancer. Maliyet ödenen tutardır.

Parametreler tek yerde (TR, DE); yıl değişince burası güncellenir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, model_validator

from pusula.db.models import CompanyId, Currency
from pusula.finance.fx import FxTable

PayKind = Literal["bordro", "sozlesme"]
PayBasis = Literal["net", "brut"]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_payroll (
    id          bigserial PRIMARY KEY,
    person      text NOT NULL CHECK (length(person) BETWEEN 1 AND 120),
    unit        text NOT NULL DEFAULT '',
    c_level     boolean NOT NULL DEFAULT false,
    start_date  date,
    company_id  text NOT NULL REFERENCES fin_companies (id),
    kind        text NOT NULL CHECK (kind IN ('bordro', 'sozlesme')),
    currency    text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    amount      numeric(18, 2) NOT NULL CHECK (amount >= 0),
    basis       text NOT NULL DEFAULT 'net' CHECK (basis IN ('net', 'brut')),
    meal        numeric(18, 2) NOT NULL DEFAULT 0 CHECK (meal >= 0),
    label       text,
    active      boolean NOT NULL DEFAULT true,
    notes       text,
    sort        integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (kind = 'sozlesme' OR company_id IN ('tr', 'gmbh'))
);
ALTER TABLE fin_payroll ENABLE ROW LEVEL SECURITY;
"""

COLUMNS = (
    "id, person, unit, c_level, start_date, company_id, kind, currency, amount, basis, meal,"
    " label, active, notes, sort, created_at"
)
WRITABLE = frozenset(
    {
        "person", "unit", "c_level", "start_date", "company_id", "kind", "currency", "amount",
        "basis", "meal", "label", "active", "notes", "sort",
    }
)


class FinPayroll(BaseModel):
    """fin_payroll tablosu: bir kişinin bir ödeme kanalı (aylık)."""

    id: int | None = None
    person: Annotated[str, Field(min_length=1, max_length=120)]
    unit: str = ""
    c_level: bool = False
    start_date: date | None = None
    company_id: CompanyId
    kind: PayKind
    currency: Currency
    amount: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
    basis: PayBasis = "net"
    meal: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] = Decimal("0")
    label: str | None = None
    active: bool = True
    notes: str | None = None
    sort: int = 0
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _bordro_company(self) -> FinPayroll:
        if self.kind == "bordro" and self.company_id not in ("tr", "gmbh"):
            raise ValueError("Bordro yalnız AŞ (tr) veya GmbH için; LLC ödemesi sözleşmedir")
        return self


def ensure_schema(conn: psycopg.Connection) -> None:
    """Tablo yoksa kurar (ALTER kilidi her istekte alınmasın diye önce bakar)."""
    row = conn.execute("SELECT to_regclass('fin_payroll')").fetchone()
    if row is None or row[0] is None:
        conn.execute(SCHEMA_SQL)


def load_payroll(conn: psycopg.Connection) -> list[FinPayroll]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM fin_payroll ORDER BY sort, person, id")
        return [FinPayroll(**r) for r in cur.fetchall()]


def load_payroll_row(conn: psycopg.Connection, row_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM fin_payroll WHERE id = %s", (row_id,))
        return cur.fetchone()


# --- Türkiye ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrParams:
    asgari_brut: float = 33030.0
    sgk_tavan_kat: float = 7.5
    sgk_isci: float = 0.15  # %14 SGK + %1 işsizlik
    # %21,75 (MYÖ %12 + kısa vadeli %2,25 + GSS %7,5) − 2 puan teşvik (imalat dışı, 2026)
    # + %2 işsizlik
    sgk_isveren: float = 0.2175
    damga: float = 0.00759
    gv_dilim: tuple[tuple[float, float], ...] = (
        (190000, 0.15), (400000, 0.20), (1500000, 0.27), (5300000, 0.35), (float("inf"), 0.40),
    )
    kidem_tavan: float = 73729.87  # Temmuz–Aralık 2026

    @property
    def sgk_tavan(self) -> float:
        return self.asgari_brut * self.sgk_tavan_kat


TR = TrParams()


def _gelir_vergisi(matrah: float, p: TrParams) -> float:
    vergi, alt = 0.0, 0.0
    for ust, oran in p.gv_dilim:
        if matrah <= alt:
            break
        vergi += (min(matrah, ust) - alt) * oran
        alt = ust
    return vergi


def tr_brut_to_net(brut: float, p: TrParams = TR) -> float:
    """Aylık brüt → aylık net (yıllık ortalama, asgari ücret istisnalı)."""
    sgk = min(brut, p.sgk_tavan) * p.sgk_isci
    gv = _gelir_vergisi(12 * (brut - sgk), p) - _gelir_vergisi(12 * p.asgari_brut * (1 - p.sgk_isci), p)
    dv = 12 * (brut - p.asgari_brut) * p.damga
    return brut - sgk - (max(gv, 0.0) + max(dv, 0.0)) / 12


# --- Almanya ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeParams:
    bbg_rv: float = 8450.0  # emeklilik + işsizlik tavanı (aylık)
    bbg_kv: float = 5812.5  # sağlık + bakım tavanı (aylık)
    an: dict[str, float] = field(default_factory=lambda: {"rv": 0.093, "av": 0.013, "kv": 0.0875, "pv": 0.024})
    ag: dict[str, float] = field(default_factory=lambda: {"rv": 0.093, "av": 0.013, "kv": 0.0875, "pv": 0.018})
    umlagen: float = 0.02  # U1/U2/Insolvenz/BG


DE = DeParams()


def _de_gelir_vergisi(zve: float) -> float:
    """§32a EStG (2025 katsayıları), tek kişi."""
    zve = float(int(zve))
    if zve <= 12096:
        return 0.0
    if zve <= 17443:
        y = (zve - 12096) / 1e4
        return (932.3 * y + 1400) * y
    if zve <= 68480:
        z = (zve - 17443) / 1e4
        return (176.64 * z + 2397) * z + 1015.13
    if zve <= 277825:
        return 0.42 * zve - 10911.92
    return 0.45 * zve - 19246.67


def de_brut_to_net(brut: float, p: DeParams = DE) -> float:
    rv, kv = min(brut, p.bbg_rv), min(brut, p.bbg_kv)
    sosyal = rv * (p.an["rv"] + p.an["av"]) + kv * (p.an["kv"] + p.an["pv"])
    vorsorge = 12 * (rv * p.an["rv"] + kv * (p.an["kv"] + p.an["pv"]))
    gv = _de_gelir_vergisi(12 * brut - vorsorge - 1230 - 36)
    soli = max(0.0, min(gv * 0.055, (gv - 19950) * 0.119))
    return brut - sosyal - (gv + soli) / 12


def _net_to_brut(net: float, to_net: Any) -> float:
    lo, hi = net, net * 3 + 1
    for _ in range(80):
        mid = (lo + hi) / 2
        if to_net(mid) < net:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --- satır maliyeti --------------------------------------------------------------------


@dataclass(frozen=True)
class LineCost:
    """Aylık, satırın para biriminde."""

    net: float
    brut: float
    employee: float  # çalışan kesintileri (SGK/sigorta + vergiler)
    employer: float  # işveren SGK / sosyal sigorta payı
    benefits: float  # yemek kartı vb.
    severance: float  # kıdem tazminatı karşılığı
    total: float


def line_cost(line: FinPayroll, tr: TrParams = TR, de: DeParams = DE) -> LineCost:
    amount = float(line.amount)
    meal = float(line.meal)
    if line.kind == "sozlesme":
        return LineCost(amount, amount, 0.0, 0.0, meal, 0.0, amount + meal)
    if line.company_id == "gmbh":
        brut = amount if line.basis == "brut" else _net_to_brut(amount, lambda b: de_brut_to_net(b, de))
        net = de_brut_to_net(brut, de)
        rv, kv = min(brut, de.bbg_rv), min(brut, de.bbg_kv)
        employer = rv * (de.ag["rv"] + de.ag["av"]) + kv * (de.ag["kv"] + de.ag["pv"]) + brut * de.umlagen
        return LineCost(net, brut, brut - net, employer, meal, 0.0, brut + employer + meal)
    brut = amount if line.basis == "brut" else _net_to_brut(amount, lambda b: tr_brut_to_net(b, tr))
    net = tr_brut_to_net(brut, tr)
    employer = min(brut, tr.sgk_tavan) * tr.sgk_isveren
    severance = min(brut, tr.kidem_tavan) / 12
    return LineCost(net, brut, brut - net, employer, meal, severance, brut + employer + meal + severance)


# --- görünüm ---------------------------------------------------------------------------

PARTS = ("net", "employee", "employer", "benefits", "severance", "total")


def _r(v: float) -> float:
    return round(v, 2)


def payroll_view(lines: list[FinPayroll], fx: FxTable, scope: list[str]) -> dict[str, Any]:
    """Aylık maaş yükü: satır, kişi, şirket ve genel toplam (USD)."""
    people: dict[str, dict[str, Any]] = {}
    companies: dict[str, dict[str, Any]] = {}
    inactive: list[dict[str, Any]] = []
    for line in lines:
        if line.company_id not in scope:
            continue
        if not line.active:
            inactive.append(line.model_dump(exclude={"created_at"}))
            continue
        cost = line_cost(line)
        rate = float(fx.rate(line.currency) or 0)
        if rate == 0:
            fx.missing.add(line.currency)
        usd = {k: getattr(cost, k) * rate for k in PARTS}
        person = people.setdefault(
            line.person,
            {"person": line.person, "unit": line.unit, "c_level": line.c_level, "start_date": line.start_date,
             "lines": [], **{f"{k}_usd": 0.0 for k in PARTS}},
        )
        person["c_level"] = person["c_level"] or line.c_level
        person["unit"] = person["unit"] or line.unit
        person["start_date"] = person["start_date"] or line.start_date
        for k in PARTS:
            person[f"{k}_usd"] += usd[k]
        person["lines"].append(
            {
                **line.model_dump(exclude={"created_at"}),
                **{k: _r(getattr(cost, k)) for k in ("net", "brut", "employee", "employer", "benefits", "severance", "total")},
                "total_usd": _r(usd["total"]),
            }
        )
        co = companies.setdefault(line.company_id, {"company_id": line.company_id, "people": set(), **{f"{k}_usd": 0.0 for k in PARTS}})
        co["people"].add(line.person)
        for k in PARTS:
            co[f"{k}_usd"] += usd[k]

    def rounded(d: dict[str, Any]) -> dict[str, Any]:
        return {k: (_r(v) if k.endswith("_usd") else v) for k, v in d.items()}

    rows = sorted((rounded(p) for p in people.values()), key=lambda p: (not p["c_level"], p["unit"], p["person"]))
    totals = {f"{k}_usd": _r(sum(p[f"{k}_usd"] for p in people.values())) for k in PARTS}
    staff = [p for p in people.values() if not p["c_level"]]
    staff_total = sum(p["total_usd"] for p in staff)
    return {
        "people": rows,
        "companies": [
            {**rounded({k: v for k, v in c.items() if k != "people"}), "headcount": len(c["people"])}
            for c in sorted(companies.values(), key=lambda c: -c["total_usd"])
        ],
        "totals": totals,
        "headcount": len(people),
        "staff_headcount": len(staff),
        "per_head_monthly_usd": _r(staff_total / len(staff)) if staff else None,
        "burden_ratio": _r(totals["total_usd"] / totals["net_usd"]) if totals["net_usd"] else None,
        "inactive": inactive,
        "params": {
            "tr_sgk_isveren": TR.sgk_isveren,
            "tr_kidem_tavan": TR.kidem_tavan,
            "tr_asgari_brut": TR.asgari_brut,
            "de_umlagen": DE.umlagen,
        },
    }
