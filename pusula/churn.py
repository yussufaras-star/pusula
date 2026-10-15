"""Churn: aboneliği dolan müşterilerden vade gününe kadar yenilemeyenler.

Kural (yönetici, 13 Ekim):
- Abonelik açan satış: kazanılmış Rexven-Premium, Rexven-Plus,
  Rexven-Premium-Kamp veya Re-Co-Premium; her biri satış gününden 12 ay.
- Vade gününe kadar yeni bir abonelik satışı → yeniledi (süre o satıştan 12 ay).
- Ek süre yok: vade geçti ve yenileme yoksa kaybedildi. Sonradan satın
  alırsa "geri kazanıldı" ayrıca sayılır (churn oranından düşülmez).
- Aylık churn = kaybedilen / (yenileyen + kaybedilen), vadesi o ay dolan ve
  vadesi geçmiş müşterilerde. Yıllık: aynı oran 12 ay toplamıyla.
- İadeler churn'e girmez; iade oranı = iade / satış, ayrı.
Kaynak: deal_packages (contact_id ile), contact_profiles (sahip süzmesi).

CRM öncesi müşteriler (yönetici, 14 Ekim; churn operasyonu 2026 başından beri):
satışı CRM'de olmayan abonelik müşteri kaydındaki abonelik bitişinden kurulur.
- CRM'de abonelik satışı varsa ilk satış önceki dönemin yenilemesidir: vade = o
  satışın günü (Re-Co satışı kendi ayına "yeniledi" yazılır).
- CRM'de satış yoksa vade = Zoho "Abonelik Bitiş"; geçtiyse kaybedildi.
Tablo Ocak 2026'dan başlar (öncesinde yenilemeler CRM'de görünmez).

Re-Co (yönetici, 15 Ekim): Deals'teki her kazanılmış Re-Co satışı yenilemedir.
Dönem başında da olsa yok sayılmaz, vadeden sonra gelse de kayıp / geri
kazanma değil "yeniledi" olur (açık dönemin vade ayına yazılır). Müşteri
kaydında abonelik tarihi olmasa da ilk Re-Co önceki dönemin yenilemesidir.

Free (yönetici, 15 Ekim): Zoho müşteri kaydında paketi free'ye çekilmiş müşteri
aboneliğini yenilememiştir. Vadesi gelmemiş görünse de açık dönemi "kaybedildi"
olur (vade ayına yazılır; vade gelmeden oran gösterilmez) ve kaybedilenler
listesinde 120 gün sınırı olmadan "Free'ye çekildi" diye görünür.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from pusula.config import get_org_id
from pusula.panel_ciro import add_months, month_first
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

SUBSCRIPTION_PACKAGES = ("Rexven-Premium", "Rexven-Plus", "Rexven-Premium-Kamp", "Re-Co-Premium")
REFUND_STAGE = "İptaller-İadeler"
TERM_MONTHS = 12
CRM_START = date(2025, 10, 1)
CHURN_START = date(2026, 1, 1)
RENEWAL_PACKAGE = "Re-Co-Premium"
FREE_PACKAGES = ("free", "freemium")


def is_free(paket: str | None) -> bool:
    return (paket or "").strip().lower() in FREE_PACKAGES


def plus_months(day: date, months: int) -> date:
    first = add_months(month_first(day), months)
    last_day = (add_months(first, 1) - first).days
    return first.replace(day=min(day.day, last_day))


# Yenileme dönemin son 6 ayında (veya sonrasında) yapılan satıştır; dönemin
# başındaki ek satış (ör. aynı hafta ikinci paket) yenileme sayılmaz.
RENEWAL_WINDOW = timedelta(days=183)


def due_events(
    purchases: Iterable[date], today: date, renewals: Iterable[date] = ()
) -> list[dict[str, Any]]:
    """Saf: bir müşterinin abonelik satış tarihleri → vade olayları.

    Sonuç: yeniledi (vadeye kadar yeni satış) | kaybedildi (vade geçti,
    yenileme yok; sonradan satın aldıysa geri_kazanildi) | bekliyor (vade gelmedi).
    renewals: Re-Co satış günleri; her biri açık dönemi "yeniledi" kapatır.
    """
    renewal = set(renewals)
    dates = sorted(set(purchases) | renewal)
    if not dates:
        return []
    events: list[dict[str, Any]] = []
    end = plus_months(dates[0], TERM_MONTHS)
    rest = dates[1:]
    while True:
        rest = [d for d in rest if d in renewal or d > end - RENEWAL_WINDOW]
        if not rest:
            sonuc = "bekliyor" if end >= today else "kaybedildi"
            events.append({"vade": end, "sonuc": sonuc, "geri_kazanildi": False})
            return events
        nxt, rest = rest[0], rest[1:]
        if nxt <= end or nxt in renewal:
            events.append({"vade": end, "sonuc": "yeniledi", "geri_kazanildi": False})
        elif end >= today:
            events.append({"vade": end, "sonuc": "bekliyor", "geri_kazanildi": False})
            return events
        else:
            events.append({"vade": end, "sonuc": "kaybedildi", "geri_kazanildi": True})
        end = plus_months(nxt, TERM_MONTHS)


def pre_crm_start(
    sales: list[tuple[date, str | None]], bas: date | None, bit: date | None
) -> date | None:
    """Saf: CRM öncesi müşterinin varsayılan ilk abonelik günü (vade 12 ay sonra).

    CRM öncesi: abonelik başlangıcı CRM'den önce, ya da CRM'deki ilk abonelik
    satışı Re-Co (yenileme). Satış varsa ilk satış vadedir; yoksa abonelik bitişi.
    """
    first = min(sales)[0] if sales else None
    first_pkg = min(sales)[1] if sales else None
    pre = (bas is not None and bas < CRM_START) or first_pkg == RENEWAL_PACKAGE or (not sales and bit is not None)
    if not pre:
        return None
    due = first if first is not None else bit
    return plus_months(due, -TERM_MONTHS) if due else None


def monthly_churn(events: Iterable[dict[str, Any]], today: date, months_back: int = 12, months_ahead: int = 3) -> list[dict[str, Any]]:
    """Saf: vade olayları → ay satırları (en fazla 12 ay geri, Ocak 2026'dan; + önümüzdeki 3 ay)."""
    start = max(add_months(month_first(today), -(months_back - 1)), CHURN_START)
    stop = add_months(month_first(today), months_ahead)
    rows: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in events:
        m = month_first(e["vade"])
        if start <= m <= stop:
            rows[m][e["sonuc"]] += 1
            if e.get("geri_kazanildi"):
                rows[m]["geri_kazanildi"] += 1
    out = []
    m = start
    while m <= stop:
        r = rows.get(m, {})
        decided = r.get("yeniledi", 0) + r.get("kaybedildi", 0)
        out.append(
            {
                "ay": m,
                "vadesi_gelen": decided + r.get("bekliyor", 0),
                "yeniledi": r.get("yeniledi", 0),
                "kaybedildi": r.get("kaybedildi", 0),
                "geri_kazanildi": r.get("geri_kazanildi", 0),
                "bekliyor": r.get("bekliyor", 0),
                # Gelecek ayda yalnız erken yenileyenler görünür; oran yanıltır.
                "churn": round(100 * r.get("kaybedildi", 0) / decided, 1)
                if decided and m <= month_first(today)
                else None,
            }
        )
        m = add_months(m, 1)
    return out


def period_churn(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lost = sum(r["kaybedildi"] for r in rows)
    renewed = sum(r["yeniledi"] for r in rows)
    decided = lost + renewed
    return {
        "yeniledi": renewed,
        "kaybedildi": lost,
        "geri_kazanildi": sum(r["geri_kazanildi"] for r in rows),
        "churn": round(100 * lost / decided, 1) if decided else None,
    }


def contact_events(info: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Saf: müşteri kaydı (sales, pre, paket) → vade olayları; Re-Co satışları
    yenileme, paketi free'ye çekilmişse açık dönem kaybedildi."""
    pre = info.get("pre")
    starts = ([pre] if pre else []) + [d for d, _ in info["sales"]]
    renewals = [d for d, pkg in info["sales"] if pkg == RENEWAL_PACKAGE]
    events = due_events(starts, today, renewals) if starts else []
    if events and events[-1]["sonuc"] == "bekliyor" and is_free(info.get("paket")):
        events[-1] = {**events[-1], "sonuc": "kaybedildi", "free": True}
    return events


UPCOMING_DAYS = 60
LOST_DAYS = 120


def action_lists(
    contacts: dict[str, dict[str, Any]], today: date
) -> dict[str, list[dict[str, Any]]]:
    """Saf: müşteri başına satışlar → iş listeleri.

    yaklasan: vadesi önümüzdeki 60 günde dolacak, henüz yenilemeyenler (vade sırası).
    kaybedilen: son 120 günde vadesi geçip yenilemeyen, geri de dönmeyenler (geri
    kazanma listesi; en yeni kayıp önce). Paketi free'ye çekilenler süre sınırı
    olmadan bu listededir (free=True).
    contacts: contact_id → {name, owner, sales: [(gün, paket)], pre: CRM öncesi
    varsayılan başlangıç | None, paket/bas: müşteri kaydından}.
    """
    upcoming: list[dict[str, Any]] = []
    lost: list[dict[str, Any]] = []
    for cid, info in contacts.items():
        sales = sorted(info["sales"])
        pre = info.get("pre")
        events = contact_events(info, today)
        if not events:
            continue
        last = events[-1]
        base = {
            "contact_id": cid,
            "musteri": info.get("name") or "—",
            "sahip": info.get("owner"),
            "paket": sales[-1][1] if sales else (info.get("paket") or "CRM öncesi"),
            "son_satis": sales[-1][0] if sales else (info.get("bas") or pre),
            "satis_sayisi": len(sales),
            "crm_oncesi": pre is not None,
            "vade": last["vade"],
            "free": bool(last.get("free")),
        }
        days = (last["vade"] - today).days
        if last["sonuc"] == "bekliyor" and days <= UPCOMING_DAYS:
            upcoming.append({**base, "kalan_gun": days})
        elif last["sonuc"] == "kaybedildi" and (-days <= LOST_DAYS or base["free"]):
            lost.append({**base, "gecen_gun": -days})
    upcoming.sort(key=lambda r: (r["vade"], r["musteri"]))
    lost.sort(key=lambda r: (r["gecen_gun"], r["musteri"]))
    return {"yaklasan": upcoming, "kaybedilen": lost}


def load_churn(today: date, owner: str | None = None) -> dict[str, Any]:
    org_id = get_org_id()
    with connect() as conn:
        ok = conn.execute(
            """SELECT to_regclass('deal_packages') IS NOT NULL AND to_regclass('contact_profiles') IS NOT NULL
                      AND EXISTS (SELECT 1 FROM information_schema.columns
                                  WHERE table_name = 'deal_packages' AND column_name = 'contact_id')"""
        ).fetchone()
        if not ok or not ok[0]:
            return {"available": False}
        owner_sql = " AND c.owner_name = %s" if owner else ""
        params: list[Any] = [list(SUBSCRIPTION_PACKAGES), org_id, WON_STAGE]
        if owner:
            params.append(owner)
        purchases = conn.execute(
            f"""
            SELECT d.contact_id, coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date),
                   d.package, c.full_name, c.owner_name
            FROM deal_packages d
            LEFT JOIN contact_profiles c ON c.org_id = d.org_id AND c.contact_id = d.contact_id
            WHERE d.package = ANY(%s) AND d.org_id = %s AND d.stage = %s
              AND d.contact_id IS NOT NULL {owner_sql}
            """,
            params,
        ).fetchall()
        # CRM öncesi müşteriler: abonelik başlangıç/bitiş müşteri kaydından.
        profiles = conn.execute(
            f"""
            SELECT c.contact_id, c.full_name, c.owner_name, c.paket, c.abonelik_bas, c.abonelik_bit
            FROM contact_profiles c
            WHERE c.org_id = %s AND (c.abonelik_bas IS NOT NULL OR c.abonelik_bit IS NOT NULL) {owner_sql}
            """,
            [org_id, *([owner] if owner else [])],
        ).fetchall()
        refunds = conn.execute(
            f"""
            SELECT date_trunc('month', coalesce(d.closing_date, (d.created_at AT TIME ZONE 'Europe/Istanbul')::date))::date,
                   count(*) FILTER (WHERE d.stage = %s)::int,
                   count(*) FILTER (WHERE d.stage = %s)::int
            FROM deal_packages d
            LEFT JOIN contact_profiles c ON c.org_id = d.org_id AND c.contact_id = d.contact_id
            WHERE d.package = ANY(%s) AND d.org_id = %s {owner_sql}
            GROUP BY 1
            """,
            [REFUND_STAGE, WON_STAGE, list(SUBSCRIPTION_PACKAGES), org_id, *([owner] if owner else [])],
        ).fetchall()
    contacts: dict[str, dict[str, Any]] = {}
    for cid, day, package, name, owner_name in purchases:
        if day:
            info = contacts.setdefault(str(cid), {"name": name, "owner": owner_name, "sales": []})
            info["sales"].append((day, package))
    for cid, name, owner_name, paket, bas, bit in profiles:
        info = contacts.get(str(cid))
        if info is None:
            if bit is None:
                continue
            info = contacts.setdefault(str(cid), {"name": name, "owner": owner_name, "sales": []})
        info.update({"paket": paket, "bas": bas})
        info["pre"] = pre_crm_start(info["sales"], bas, bit)
    events = []
    pre_count = 0
    for info in contacts.values():
        if "pre" not in info:
            # Müşteri kaydında abonelik tarihi yok: ilk satış Re-Co ise yine yenileme.
            info["pre"] = pre_crm_start(info["sales"], None, None)
        pre_count += 1 if info.get("pre") else 0
        events += contact_events(info, today)
    months = monthly_churn(events, today)
    refund = {m: (iade, satis) for m, iade, satis in refunds}
    for r in months:
        iade, satis = refund.get(r["ay"], (0, 0))
        r["iade"] = iade
        r["satis"] = satis
        r["iade_orani"] = round(100 * iade / satis, 1) if satis else None
    past = [r for r in months if r["ay"] <= month_first(today)]
    year = [r for r in past if r["ay"].year == today.year]
    return {
        "available": True,
        "owner": owner,
        "aylar": months,
        "son_12_ay": period_churn(past),
        "bu_yil": period_churn(year),
        "musteri": len(contacts),
        "crm_oncesi": pre_count,
        "baslangic": CHURN_START,
        **action_lists(contacts, today),
        "yaklasan_gun": UPCOMING_DAYS,
        "kaybedilen_gun": LOST_DAYS,
        "packages": SUBSCRIPTION_PACKAGES,
    }
