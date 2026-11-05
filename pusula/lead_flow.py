"""Lead akışı: haftalık gelen lead ve temsilci başına günlük lead, % değişim.

Satış-pazarlama toplantısı için. İki ölçü:
- Gelen: Zoho'ya düşen tekil kişi (lead_inflow_daily.n_unique; Ekip
  Panosu'yla aynı sayım, toplu içe aktarım günleri hariç).
- Temsilci başı günlük lead: satış temsilcisine atanan lead (leads.assigned_at,
  bugünkü sahip) / dönemde lead alan temsilci / gün. Lead'ler pazar dahil
  her gün gelip atandığından gün takvim günüdür (tam hafta 7). Ayrılan
  temsilci lead aldığı haftalarda sayılır.

Haftalar pazartesi başlar; bu hafta sürüyorsa işaretli. Bugün yarım gün
olduğundan günlük ortalamalar dünden sonrasını saymaz. Süren hafta tam
haftayla kıyaslanmaz: "aynı güne kadar" bu hafta pazartesi→dün ile geçen
hafta pazartesi→aynı gün. Dört haftalık kıyas son dört tamamlanmış haftanın
ortalaması / önceki dört.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from pusula.ciro_trend import change, week_start
from pusula.config import get_org_id
from pusula.panel_data import connect

WEEK_CHOICES = (8, 12, 26)
SPAN = 4  # dört haftalık kıyas

Assigned = tuple[str, date, int]  # rep_id, gün, adet


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def by_rep(assigned: Iterable[Assigned], a: date, b: date) -> dict[str, int]:
    """Kapalı aralıkta temsilci → atanan lead."""
    out: dict[str, int] = defaultdict(int)
    for rep_id, day, n in assigned:
        if a <= day <= b and n:
            out[rep_id] += n
    return dict(out)


def daily_per_rep(assigned: Iterable[Assigned], a: date, b: date) -> float | None:
    """Saf: [a, b] aralığında temsilci başına günlük atanan lead."""
    days = (b - a).days + 1
    reps = by_rep(assigned, a, b)
    if days <= 0 or not reps:
        return None
    return round(sum(reps.values()) / len(reps) / days, 1)


def week_rows(
    inflow: dict[date, int], assigned: list[Assigned], weeks: int, today: date
) -> list[dict[str, Any]]:
    """Saf: weeks tamamlanmış hafta + süren hafta (ilk haftanın değişimi için
    bir hafta fazladan hesaplanır)."""
    current = week_start(today)
    first = current - timedelta(weeks=weeks + 1)
    rows: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for i in range(weeks + 2):
        bas = first + timedelta(weeks=i)
        running = bas == current
        # Süren haftada günlük ortalama düne kadar (bugün yarım).
        last = today - timedelta(days=1) if running else bas + timedelta(days=6)
        gelen = sum(n for d, n in inflow.items() if bas <= d <= min(bas + timedelta(days=6), today))
        row = {
            "bas": bas,
            "bit": bas + timedelta(days=6),
            "gelen": gelen,
            "gunluk_gelen": round(gelen / 7, 1) if not running else None,
            "atanan": sum(by_rep(assigned, bas, min(bas + timedelta(days=6), today)).values()),
            "temsilci": len(by_rep(assigned, bas, min(bas + timedelta(days=6), today))),
            "kisi_gunluk": daily_per_rep(assigned, bas, last),
            "suruyor": running,
            "degisim_gelen": None,
            "degisim_kisi_gunluk": None,
        }
        if prev is not None and not running:
            row["degisim_gelen"] = change(row["gelen"], prev["gelen"])
            row["degisim_kisi_gunluk"] = change(row["kisi_gunluk"] or 0, prev["kisi_gunluk"] or 0)
        rows.append(row)
        prev = row
    return rows[1:]


def to_date(inflow: dict[date, int], assigned: list[Assigned], today: date) -> dict[str, Any] | None:
    """Saf: bu hafta pazartesi→dün ile geçen hafta pazartesi→aynı gün; pazartesi kıyas yoktur."""
    cur = week_start(today)
    upto = today - timedelta(days=1)
    if upto < cur:
        return None
    prev, prev_end = cur - timedelta(weeks=1), upto - timedelta(weeks=1)
    g = sum(n for d, n in inflow.items() if cur <= d <= upto)
    pg = sum(n for d, n in inflow.items() if prev <= d <= prev_end)
    k, pk = daily_per_rep(assigned, cur, upto), daily_per_rep(assigned, prev, prev_end)
    return {
        "gun": (upto - cur).days + 1,
        "bit": upto,
        "onceki_bas": prev,
        "onceki_bit": prev_end,
        "gelen": g,
        "onceki_gelen": pg,
        "degisim_gelen": change(g, pg),
        "kisi_gunluk": k,
        "onceki_kisi_gunluk": pk,
        "degisim_kisi_gunluk": change(k or 0, pk or 0),
    }


def four_weeks(done: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Saf: son dört tamamlanmış haftanın ortalaması ile önceki dört."""
    if len(done) < 2 * SPAN:
        return None
    last, before = done[-SPAN:], done[-2 * SPAN : -SPAN]
    g, pg = _avg([r["gelen"] for r in last]), _avg([r["gelen"] for r in before])
    k = _avg([r["kisi_gunluk"] for r in last if r["kisi_gunluk"] is not None])
    pk = _avg([r["kisi_gunluk"] for r in before if r["kisi_gunluk"] is not None])
    return {
        "bas": last[0]["bas"],
        "onceki_bas": before[0]["bas"],
        "gelen": g,
        "onceki_gelen": pg,
        "degisim_gelen": change(g or 0, pg or 0),
        "kisi_gunluk": k,
        "onceki_kisi_gunluk": pk,
        "degisim_kisi_gunluk": change(k or 0, pk or 0),
    }


def rep_rows(
    rows: list[dict[str, Any]], assigned: list[Assigned], names: dict[str, str], today: date
) -> list[dict[str, Any]]:
    """Saf: temsilci başına haftalık günlük ortalama, geçen hafta / önceki hafta
    değişimi, dört haftalık ortalama. Lead almadığı hafta None (izin, yeni başlama)."""
    done = [r for r in rows if not r["suruyor"]]
    out = []
    for rep_id in sorted({r for r, _, _ in assigned}):
        mine = [x for x in assigned if x[0] == rep_id]
        seri: list[float | None] = []
        for w in rows:
            last = today - timedelta(days=1) if w["suruyor"] else w["bit"]
            n = sum(by_rep(mine, w["bas"], last).values())
            days = (last - w["bas"]).days + 1
            seri.append(round(n / days, 1) if n and days > 0 else None)
        tamam = seri[: len(done)]
        son = tamam[-1] if tamam else None
        onceki = tamam[-2] if len(tamam) > 1 else None
        ort4 = _avg([x for x in tamam[-SPAN:] if x is not None])
        onceki_ort4 = _avg([x for x in tamam[-2 * SPAN : -SPAN] if x is not None])
        out.append({
            "rep_id": rep_id,
            "name": names.get(rep_id, rep_id),
            "seri": seri,
            "son": son,
            "onceki": onceki,
            "degisim": change(son, onceki) if son is not None and onceki else None,
            "ort4": ort4,
            "onceki_ort4": onceki_ort4,
            "degisim_ort4": change(ort4, onceki_ort4) if ort4 is not None and onceki_ort4 else None,
        })
    out.sort(key=lambda r: (-(r["ort4"] or 0), r["name"]))
    return out


def build(
    inflow: dict[date, int], assigned: list[Assigned], names: dict[str, str], weeks: int, today: date
) -> dict[str, Any]:
    rows = week_rows(inflow, assigned, weeks, today)
    done = [r for r in rows if not r["suruyor"]]
    return {
        "available": True,
        "haftalar": rows,
        "son_hafta": done[-1] if done else None,
        "ayni_gune": to_date(inflow, assigned, today),
        "dort_hafta": four_weeks(done),
        "temsilciler": rep_rows(rows, assigned, names, today),
    }


def load_lead_flow(weeks: int, today: date) -> dict[str, Any]:
    org_id = get_org_id()
    # Değişim ve dört haftalık kıyas için seriden önceki haftalar da okunur.
    span = max(weeks, 2 * SPAN)
    first = week_start(today) - timedelta(weeks=span + 1)
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'lead_inflow_daily' AND column_name = 'n_unique'"
        ).fetchone()
        if not exists:
            return {"available": False}
        inflow_rows = conn.execute(
            """
            SELECT day, coalesce(n_unique, n) FROM lead_inflow_daily
            WHERE org_id = %s AND NOT is_import AND day BETWEEN %s AND %s
            """,
            (org_id, first, today),
        ).fetchall()
        assigned_rows = conn.execute(
            """
            SELECT l.owner_rep_id, (l.assigned_at AT TIME ZONE 'Europe/Istanbul')::date, count(*)::int
            FROM leads l
            JOIN reps r ON r.org_id = l.org_id AND r.rep_id = l.owner_rep_id
            WHERE l.org_id = %s AND r.category = 'sales'
              AND l.assigned_at >= %s AND l.assigned_at < %s
            GROUP BY 1, 2
            """,
            (org_id, first, today + timedelta(days=1)),
        ).fetchall()
        names = conn.execute(
            "SELECT rep_id, full_name FROM reps WHERE org_id = %s AND category = 'sales'", (org_id,)
        ).fetchall()
    inflow = {d: int(n) for d, n in inflow_rows}
    assigned = [(str(r), d, int(n)) for r, d, n in assigned_rows]
    full = build(inflow, assigned, {str(k): str(v) for k, v in names}, span, today)
    # Dört haftalık kıyas uzun seriden; gösterilen seri istenen hafta + süren hafta.
    shown = len(full["haftalar"]) - (weeks + 1)
    full["haftalar"] = full["haftalar"][shown:]
    for rep in full["temsilciler"]:
        rep["seri"] = rep["seri"][shown:]
    full["temsilciler"] = [r for r in full["temsilciler"] if any(x is not None for x in r["seri"])]
    return full
