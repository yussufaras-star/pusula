"""Maaş ve prim pusulası: maaş belirlerken yol gösteren çalışma ekranı.

Maaşları takip etmez; 2027 zammını kurallarla önerir ve gerekçesini yazar.

Kurallar (yıl bazlı, 1 Ocak'ta yürürlüğe girer):
- Ülke tabanı: Türkiye'de yaşayan (AŞ bordrosu, LLC TL sözleşmesi) beklenen
  TÜFE kadar; Almanya'da yaşayan (GmbH) Almanya artışı kadar.
- Türkiye'de yaşayıp döviz alanın artışı TL karşılığı TÜFE kadar büyüyecek
  şekilde: (1 + TÜFE) / (1 + TL'nin değer kaybı) − 1.
- Asgari ücrete bağlı satırlar (asgari ücretli bordro, Bağ-Kur) yasal artışı alır.
  Asgari ücret + döviz paketi olanların toplamı döviz
  kuralıyla büyür; AŞ payı yasal artışı alır, döviz payı kalanı tamamlar.
- Kıdem: ilk yıldan sonraki her tam yıl için ek puan (tavanlı); 2026'da girenlerin
  taban artışı çalışılan aya oranlanır (maaşları giriş ayının fiyatlarıyla kuruldu).
- Kişiye özel karar (zam yok, eşitlik düzeltmesi) kuralı geçersiz kılar.

Geçmiş (fin_comp_history) prim tablosundan aylık yüklenir: Rexven kârı, havuz,
donation (şirket geneli); kişi başı havuz primi (USD), satış primi ve maaş +
multinet paketi (TL).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date
from typing import Annotated, Any, Literal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from pusula.finance.fx import FxTable
from pusula.finance.payroll import FinPayroll, line_cost

PLAN_YEAR = 2027
ASGARI_NET_2026 = 28075.50

HistoryKind = Literal["kar", "havuz", "donation", "havuz_prim", "satis_prim", "paket"]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_comp_history (
    id        bigserial PRIMARY KEY,
    month     date NOT NULL CHECK (extract(day FROM month) = 1),
    person    text,
    kind      text NOT NULL CHECK (kind IN ('kar', 'havuz', 'donation', 'havuz_prim', 'satis_prim', 'paket')),
    currency  text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    amount    numeric(18, 2) NOT NULL
);
CREATE INDEX IF NOT EXISTS fin_comp_history_month ON fin_comp_history (month);
ALTER TABLE fin_comp_history ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS fin_comp_plan (
    year        integer PRIMARY KEY,
    params      jsonb NOT NULL DEFAULT '{}',
    people      jsonb NOT NULL DEFAULT '{}',
    updated_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE fin_comp_plan ENABLE ROW LEVEL SECURITY;
"""

Rate = Annotated[float, Field(ge=-0.5, le=2)]


class PlanParams(BaseModel):
    """Zam kuralları. None: veriden türetilir."""

    tr: Rate = 0.30  # 2026 TÜFE beklentisi (kesin rakam 3 Ocak)
    de: Rate = 0.04  # Almanya: enflasyon (%2,8) + ~1 puan reel
    asgari: Rate | None = None  # 2027 asgari ücret artışı; None → tr
    c_level_usd: Rate | None = None  # kurucuların döviz paketi; None → alım gücü kuralı
    kidem_step: Annotated[float, Field(ge=0, le=0.1)] = 0.01
    kidem_cap: Annotated[int, Field(ge=0, le=10)] = 3
    kur_artis: Rate | None = None  # yıllık TL değer kaybı; None → son 12 ay


class PersonPlan(BaseModel):
    pct: Rate | None = None  # sabit toplam artış (ör. 0 = zam yok)
    adj: Annotated[float, Field(ge=-0.5, le=1)] = 0.0  # ek düzeltme (eşitlik, telafi)
    note: Annotated[str, Field(max_length=300)] = ""


class CompPlan(BaseModel):
    params: PlanParams = PlanParams()
    people: dict[str, PersonPlan] = {}


def ensure_schema(conn: psycopg.Connection) -> None:
    row = conn.execute("SELECT to_regclass('fin_comp_plan')").fetchone()
    if row is None or row[0] is None:
        conn.execute(SCHEMA_SQL)


def load_plan(conn: psycopg.Connection, year: int = PLAN_YEAR) -> CompPlan:
    row = conn.execute("SELECT params, people FROM fin_comp_plan WHERE year = %s", (year,)).fetchone()
    return CompPlan(params=row[0], people=row[1]) if row else CompPlan()


def save_plan(conn: psycopg.Connection, plan: CompPlan, year: int = PLAN_YEAR) -> None:
    conn.execute(
        """INSERT INTO fin_comp_plan (year, params, people) VALUES (%s, %s, %s)
           ON CONFLICT (year) DO UPDATE SET params = EXCLUDED.params, people = EXCLUDED.people, updated_at = now()""",
        (year, Jsonb(plan.params.model_dump()), Jsonb({k: v.model_dump() for k, v in plan.people.items()})),
    )


def load_history(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT month, person, kind, currency, amount FROM fin_comp_history ORDER BY month, person")
        return [{**r, "amount": float(r["amount"])} for r in cur.fetchall()]


# --- yardımcılar -----------------------------------------------------------------------


def _months_between(start: date, end: date) -> int:
    """Tam ay; ayın 15'inden sonra girilen ay sayılmaz."""
    months = (end.year - start.year) * 12 + end.month - start.month - (1 if start.day > 15 else 0)
    return max(months, 0)


def kidem_years(start: date | None, on: date) -> int:
    return _months_between(start, on) // 12 if start else 0


def kidem_label(start: date | None, on: date) -> str:
    if not start:
        return "—"
    m = _months_between(start, on)
    years, months = divmod(m, 12)
    if years and months:
        return f"{years} yıl {months} ay"
    return f"{years} yıl" if years else f"{months} ay"


def quarter(month: date) -> str:
    return f"Q{(month.month - 1) // 3 + 1} {month.year}"


def _pct(v: float) -> str:
    return f"%{round(v * 100, 1):g}".replace(".", ",")


def _is_asgari(line: FinPayroll) -> bool:
    if (line.label or "").lower().startswith("bağ-kur"):
        return True
    return line.kind == "bordro" and line.company_id == "tr" and float(line.amount) <= ASGARI_NET_2026 * 1.02


# --- 2027 önerisi ----------------------------------------------------------------------


def _usd(fx: FxTable, currency: str) -> float:
    rate = fx.rate(currency)
    if rate is None:
        fx.missing.add(currency)
        return 0.0
    return float(rate)


def plan_person(
    person: str,
    lines: list[FinPayroll],
    plan: CompPlan,
    fx: FxTable,
    kur_artis: float,
    try_usd_27: float,
    today: date,
) -> dict[str, Any]:
    p = plan.params
    own = plan.people.get(person, PersonPlan())
    c_level = any(x.c_level for x in lines)
    start = min((x.start_date for x in lines if x.start_date), default=None)
    unit = next((x.unit for x in lines if x.unit), "")
    on = date(PLAN_YEAR, 1, 1)
    years = kidem_years(start, on)
    asgari = p.asgari if p.asgari is not None else p.tr
    fx_rule = (1 + p.tr) / (1 + kur_artis) - 1  # TR'de yaşayana döviz: TL karşılığı TÜFE kadar
    reasons: list[str] = []

    # Kişiye ait çarpanlar (asgari satırlara uygulanmaz)
    prorate = 1.0
    if start and start >= date(PLAN_YEAR - 1, 1, 1):
        prorate = min(1.0, _months_between(start, on) / 12)
    bonus = 0.0 if c_level else p.kidem_step * min(max(years - 1, 0), p.kidem_cap)

    def adjusted(base: float) -> float:
        if own.pct is not None:
            return own.pct
        return (1 + base * prorate + bonus) * (1 + own.adj) - 1

    def base_rate(line: FinPayroll) -> float:
        if line.company_id == "gmbh":
            return p.de
        if line.currency == "TRY":
            return p.tr
        if c_level and p.c_level_usd is not None:
            return p.c_level_usd
        return fx_rule

    rates: dict[int, float] = {}
    has_asgari = any(_is_asgari(x) for x in lines)
    fx_lines = [x for x in lines if x.currency != "TRY" and not _is_asgari(x) and x.company_id != "gmbh"]
    package = has_asgari and len(fx_lines) == 1 and len(lines) == 2
    for idx, line in enumerate(lines):
        if _is_asgari(line):
            rates[idx] = asgari
        else:
            rates[idx] = adjusted(base_rate(line))
    if package:
        # Toplam döviz paketi büyür; AŞ asgari payı yasal artışla, döviz payı kalanla
        fx_line = fx_lines[0]
        rate_fx = _usd(fx, fx_line.currency)
        as_line = next(x for x in lines if _is_asgari(x))
        total0 = float(fx_line.amount) + float(as_line.amount) * _usd(fx, "TRY") / rate_fx
        target = total0 * (1 + rates[lines.index(fx_line)])
        new_as = float(as_line.amount) * (1 + asgari) * try_usd_27 / rate_fx
        rates[lines.index(fx_line)] = (target - new_as) / float(fx_line.amount) - 1 if float(fx_line.amount) else 0.0

    out_lines = []
    cost_now = cost_27 = value_now = value_27 = 0.0
    for idx, line in enumerate(lines):
        r = rates[idx]
        amount = float(line.amount)
        cost = line_cost(line)
        cur_usd = _usd(fx, line.currency)
        new_amount = amount * (1 + r)
        if line.kind == "sozlesme":
            new_total = new_amount + float(line.meal) * (1 + p.tr)
        elif line.company_id == "gmbh":
            new_total = line_cost(line.model_copy(update={"amount": new_amount})).total
        else:
            # TR bordro: 2027 dilimleri asgariyle büyür; maliyet net artışıyla orantılı
            new_total = (cost.total - float(line.meal)) * (1 + r) + float(line.meal) * (1 + p.tr)
        usd_27 = try_usd_27 if line.currency == "TRY" else cur_usd
        line_now, line_27 = cost.total * cur_usd, new_total * usd_27
        cost_now += line_now
        cost_27 += line_27
        value_now += amount * cur_usd
        value_27 += new_amount * cur_usd
        out_lines.append(
            {
                "id": line.id,
                "company_id": line.company_id,
                "kind": line.kind,
                "currency": line.currency,
                "basis": line.basis,
                "label": line.label,
                "amount": round(amount, 2),
                "new_amount": round(new_amount, 2),
                "rate": round(r, 4),
                "asgari": _is_asgari(line),
                "net": round(cost.net, 2),
                "cost_usd": round(line_now, 2),
                "cost27_usd": round(line_27, 2),
            }
        )
    rate = value_27 / value_now - 1 if value_now else 0.0
    if package:
        rate = target / total0 - 1  # paket döviz cinsinden büyür

    # Gerekçe
    if own.pct is not None:
        reasons.append(f"Karar: {_pct(own.pct)}" + (f" — {own.note}" if own.note else ""))
    else:
        countries = {"gmbh" if x.company_id == "gmbh" else "tr" for x in lines if not _is_asgari(x)}
        if "gmbh" in countries:
            reasons.append(f"Almanya tabanı {_pct(p.de)}")
        if any(x.company_id != "gmbh" and x.currency == "TRY" and not _is_asgari(x) for x in lines):
            reasons.append(f"Türkiye tabanı {_pct(p.tr)} (TÜFE)")
        if fx_lines:
            if c_level and p.c_level_usd is not None:
                reasons.append(f"C-Level döviz paketi {_pct(p.c_level_usd)} (kurucu kararı)")
            else:
                reasons.append(f"Dövizle ödeniyor: TL karşılığı TÜFE kadar büyür → {_pct(fx_rule)}")
        if prorate < 1:
            reasons.append(f"{start.strftime('%m.%Y') if start else ''}'de girdi: taban {round(prorate * 12)}/12 oranında")
        if bonus:
            reasons.append(f"Kıdem {years} yıl: +{bonus * 100:.0f} puan")
        if own.adj:
            reasons.append(f"Düzeltme {'+' if own.adj > 0 else ''}{_pct(own.adj)}" + (f" — {own.note}" if own.note else ""))
        elif own.note:
            reasons.append(own.note)
    if has_asgari:
        reasons.append(f"Asgari ücrete bağlı kısım yasal artışla ({_pct(asgari)})")

    return {
        "person": person,
        "unit": unit,
        "c_level": c_level,
        "start_date": start,
        "kidem": kidem_label(start, today),
        "kidem_years": kidem_years(start, today),
        "kidem_bonus": 0.0 if own.pct is not None else round(bonus, 4),
        "companies": sorted({x.company_id for x in lines}),
        "lines": out_lines,
        "rate": round(rate, 4),
        "cost_usd": round(cost_now, 2),
        "cost27_usd": round(cost_27, 2),
        "value_usd": round(value_now, 2),
        "reasons": reasons,
        "plan": own.model_dump(),
        "germany": all(x.company_id == "gmbh" for x in lines if not _is_asgari(x)),
        "kinds": "+".join(sorted({x.kind for x in lines if not _is_asgari(x)})),
    }


# --- geçmiş ----------------------------------------------------------------------------


def history_view(rows: list[dict[str, Any]], fx: FxTable) -> dict[str, Any]:
    """Aylık / çeyreklik prim, kâr ve donation (USD) + kişi başı 2026 primi ve 2026 zammı."""
    months: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    prim_by_person: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    paket: dict[str, dict[date, float]] = defaultdict(dict)
    for r in rows:
        m = r["month"]
        rate = float(fx.rate(r["currency"], m) or 0)
        usd = r["amount"] * rate
        if r["kind"] in ("kar", "havuz", "donation"):
            months[m][r["kind"]] += usd
        elif r["kind"] in ("havuz_prim", "satis_prim"):
            months[m][r["kind"]] += usd
            prim_by_person[r["person"]][m] += usd
        elif r["kind"] == "paket":
            paket[r["person"]][m] = r["amount"]

    month_rows = []
    for m in sorted(months):
        v = months[m]
        prim = v["havuz_prim"] + v["satis_prim"]
        month_rows.append(
            {
                "month": m,
                "quarter": quarter(m),
                "kar_usd": round(v["kar"], 2),
                "havuz_prim_usd": round(v["havuz_prim"], 2),
                "satis_prim_usd": round(v["satis_prim"], 2),
                "prim_usd": round(prim, 2),
                "donation_usd": round(v["donation"], 2),
            }
        )
    quarters: dict[str, dict[str, Any]] = {}
    for mr in month_rows:
        q = quarters.setdefault(mr["quarter"], {"quarter": mr["quarter"], "months": 0, **{k: 0.0 for k in mr if k.endswith("_usd")}})
        q["months"] += 1
        for k in mr:
            if k.endswith("_usd"):
                q[k] += mr[k]
    quarter_rows = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in q.items()} for q in quarters.values()]

    # Kişi başı 2026 ortalama aylık prim; 2026 zammı = Ara 2025 → Oca 2026 maaş + multinet
    prim_2026 = {
        person: round(sum(v for m, v in by.items() if m.year == PLAN_YEAR - 1) / max(1, sum(1 for m in by if m.year == PLAN_YEAR - 1)), 2)
        for person, by in prim_by_person.items()
        if any(m.year == PLAN_YEAR - 1 for m in by)
    }
    dec, jan = date(PLAN_YEAR - 2, 12, 1), date(PLAN_YEAR - 1, 1, 1)
    raise_2026 = {
        person: {"old": by[dec], "new": by[jan], "rate": round(by[jan] / by[dec] - 1, 4)}
        for person, by in paket.items()
        if by.get(dec) and by.get(jan)
    }
    return {"months": month_rows, "quarters": quarter_rows, "prim_2026": prim_2026, "raise_2026": raise_2026}


def _norm(name: str) -> str:
    return name.replace("I", "ı").replace("İ", "i").lower().strip()


def _try_depreciation(fx: FxTable, today: date) -> float | None:
    now, past = fx.rate("TRY", today), fx.rate("TRY", date(today.year - 1, today.month, min(today.day, 28)))
    if not now or not past:
        return None
    return float(past) / float(now) - 1


def compass_view(
    lines: list[FinPayroll],
    history: list[dict[str, Any]],
    plan: CompPlan,
    fx: FxTable,
    scope: list[str],
    today: date,
) -> dict[str, Any]:
    p = plan.params
    observed = _try_depreciation(fx, today)
    kur_artis = p.kur_artis if p.kur_artis is not None else (observed if observed is not None else 0.2)
    # 2027 ortalama kuru: yılın ortasına (1 Temmuz) kadar değer kaybı
    years_to_mid = max(0.0, (date(PLAN_YEAR, 7, 1) - today).days / 365)
    try_usd_now = _usd(fx, "TRY")
    try_usd_27 = try_usd_now / (1 + kur_artis) ** years_to_mid

    by_person: dict[str, list[FinPayroll]] = defaultdict(list)
    for line in lines:
        if line.active:
            by_person[line.person].append(line)
    hist = history_view(history, fx)
    norm_prim = {_norm(k): v for k, v in hist["prim_2026"].items()}
    norm_raise = {_norm(k): v for k, v in hist["raise_2026"].items()}

    people = []
    for person, plines in by_person.items():
        if not any(x.company_id in scope for x in plines):
            continue
        row = plan_person(person, plines, plan, fx, kur_artis, try_usd_27, today)
        row["prim_2026_usd"] = norm_prim.get(_norm(person))
        row["raise_2026"] = norm_raise.get(_norm(person))
        people.append(row)

    # Kıdem tutarsızlığı: aynı birim, ülke ve ödeme türünde en az 6 ay sonra giren biri
    # belirgin (%5+) daha fazla alıyorsa
    for row in people:
        if row["c_level"] or not row["start_date"]:
            continue
        for other in people:
            if (
                other is not row
                and not other["c_level"]
                and (other["unit"], other["germany"], other["kinds"]) == (row["unit"], row["germany"], row["kinds"])
                and other["start_date"]
                and _months_between(row["start_date"], other["start_date"]) >= 6
                and other["value_usd"] > row["value_usd"] * 1.05
            ):
                row.setdefault("flags", []).append(
                    f"Daha kıdemsiz {other['person'].split()[0]} ({other['kidem']}) %{(other['value_usd'] / row['value_usd'] - 1) * 100:.0f} daha fazla alıyor"
                )

    people.sort(key=lambda r: (not r["c_level"], r["unit"], -(r["kidem_years"]), r["person"]))

    companies = []
    for cid in scope:
        cl = [(l, r) for r in people for l in r["lines"] if l["company_id"] == cid]
        now = sum(l["cost_usd"] for l, _ in cl)
        if not now:
            continue
        new = sum(l["cost27_usd"] for l, _ in cl)
        local_old = sum(l["amount"] * _usd(fx, l["currency"]) for l, _ in cl)
        local_new = sum(l["new_amount"] * _usd(fx, l["currency"]) for l, _ in cl)
        members = list({id(r): r for _, r in cl}.values())
        staff = [r for r in members if not r["c_level"]]
        companies.append(
            {
                "company_id": cid,
                "headcount": len(members),
                "staff": len(staff),
                "cost_usd": round(now, 2),
                "cost27_usd": round(new, 2),
                "rate_local": round(local_new / local_old - 1, 4) if local_old else 0.0,
                "median_rate": round(statistics.median([r["rate"] for r in staff]), 4) if staff else None,
            }
        )

    total_now = sum(r["cost_usd"] for r in people)
    total_27 = sum(r["cost27_usd"] for r in people)
    staff = [r for r in people if not r["c_level"]]
    raises_staff = [v["rate"] for k, v in hist["raise_2026"].items() if not _is_c_level_name(k, people)]
    q = {r["quarter"]: r for r in hist["quarters"]}
    months_2026 = [m for m in hist["months"] if m["month"].year == PLAN_YEAR - 1]
    prim_2026 = sum(m["prim_usd"] for m in months_2026)
    return {
        "year": PLAN_YEAR,
        "today": today,
        "params": p.model_dump(),
        "plan_people": {k: v.model_dump() for k, v in plan.people.items()},
        "derived": {
            "kur_artis": round(kur_artis, 4),
            "kur_artis_observed": round(observed, 4) if observed is not None else None,
            "usdtry_now": round(1 / try_usd_now, 4) if try_usd_now else None,
            "usdtry_27": round(1 / try_usd_27, 4) if try_usd_27 else None,
            "asgari": p.asgari if p.asgari is not None else p.tr,
            "fx_rule": round((1 + p.tr) / (1 + kur_artis) - 1, 4),
            "asgari_net_27": round(ASGARI_NET_2026 * (1 + (p.asgari if p.asgari is not None else p.tr)), 2),
        },
        "people": people,
        "companies": companies,
        "totals": {
            "cost_usd": round(total_now, 2),
            "cost27_usd": round(total_27, 2),
            "headcount": len(people),
            "staff_rate_median": round(statistics.median([r["rate"] for r in staff]), 4) if staff else None,
            # Kıdem payının 2027 maliyeti (yaklaşık: kişinin artışındaki payı kadar)
            "kidem_cost27_usd": round(sum(r["cost27_usd"] * r["kidem_bonus"] / (1 + r["rate"]) for r in people), 2),
            "staff_rate_local": round(sum(r["value_usd"] * (1 + r["rate"]) for r in staff) / sum(r["value_usd"] for r in staff) - 1, 4) if staff else None,
        },
        "history": {
            **{k: v for k, v in hist.items() if k in ("months", "quarters")},
            "q4_2025": q.get(f"Q4 {PLAN_YEAR - 2}"),
            "prim_2026_usd": round(prim_2026, 2),
            "prim_2026_months": len(months_2026),
            "donation_2026_usd": round(sum(m["donation_usd"] for m in months_2026), 2),
            "kar_2026_usd": round(sum(m["kar_usd"] for m in months_2026), 2),
            "raise_2026_median": round(statistics.median(raises_staff), 4) if raises_staff else None,
            "raise_2026_count": len(raises_staff),
            "raise_2026_weighted": _weighted_raise(hist["raise_2026"], people),
        },
    }


def _is_c_level_name(name: str, people: list[dict[str, Any]]) -> bool:
    n = _norm(name)
    return any(r["c_level"] and _norm(r["person"]) == n for r in people)


def _weighted_raise(raises: dict[str, dict[str, float]], people: list[dict[str, Any]]) -> float | None:
    staff = [v for k, v in raises.items() if not _is_c_level_name(k, people)]
    old = sum(v["old"] for v in staff)
    return round(sum(v["new"] for v in staff) / old - 1, 4) if old else None

