"""Paket bazlı müşteri analizi: Zoho Contacts "Paket" alanına göre müşteri dağılımı.

Gruplar (yönetici, 15 Ekim): Premium, Plus, Elite aktif; free, Freemium, Inactive
ve Premium'dan boşaltılmış paket churn (pasif); hiç paket girilmemiş ayrı
(çoğu Zoho'da paketi henüz girilmemiş yeni satış).
Her grup: müşteri, sahip dağılımı, satıyor (Son 1 Yıllık Satış > 0), abonelik
bitişi durumu, CRM'de abonelik / Re-Co satışı. Pasiflerde aktif paketten çıkış
ayı (free_since). Kaynak: contact_profiles + deal_packages (saatlik).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from pusula.churn import ACTIVE_PACKAGES, RENEWAL_PACKAGE, SUBSCRIPTION_PACKAGES
from pusula.config import get_org_id
from pusula.panel_ciro import month_first
from pusula.panel_data import connect
from pusula.sifir_satis import WON_STAGE

GROUPS: dict[str, tuple[str, str]] = {
    "premium": ("Premium", "aktif"),
    "plus": ("Plus", "aktif"),
    "elite": ("Elite", "aktif"),
    "free": ("Free", "pasif"),
    "freemium": ("Freemium", "pasif"),
    "inactive": ("Inactive", "pasif"),
    "bosaltilmis": ("Premium'dan boşaltılmış", "pasif"),
    "diger": ("Diğer", "pasif"),
    "bos": ("Paket girilmemiş", "bos"),
}
OWNERS = ("Nihat Kemer", "Burcu Özkan")
SOON_DAYS = 60


def group_of(paket: str | None, free_since: date | None) -> str:
    key = (paket or "").strip().lower()
    if not key:
        return "bosaltilmis" if free_since else "bos"
    if key in ACTIVE_PACKAGES or key in ("free", "freemium", "inactive"):
        return key
    return "diger"


def summarize(rows: Iterable[dict[str, Any]], today: date) -> dict[str, Any]:
    """Saf: müşteri satırları → paket grupları ve aylık çıkışlar.

    rows: {paket, owner, son1, bit, free_since, abonelik_satis, reco}.
    """
    groups: dict[str, dict[str, Any]] = {}
    exits: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total = 0
    for r in rows:
        total += 1
        key = group_of(r.get("paket"), r.get("free_since"))
        g = groups.setdefault(
            key,
            {"key": key, "ad": GROUPS[key][0], "tur": GROUPS[key][1], "musteri": 0,
             "sahip": {o: 0 for o in (*OWNERS, "Diğer")}, "satiyor": 0,
             "bitis_gecmis": 0, "bitis_yakin": 0, "bitis_ileri": 0, "bitis_yok": 0,
             "abonelik_satisi": 0, "reco": 0, "cikis_30g": 0, "cikis_bu_yil": 0},
        )
        g["musteri"] += 1
        g["sahip"][r.get("owner") if r.get("owner") in OWNERS else "Diğer"] += 1
        g["satiyor"] += 1 if (r.get("son1") or 0) > 0 else 0
        bit = r.get("bit")
        if bit is None:
            g["bitis_yok"] += 1
        elif bit < today:
            g["bitis_gecmis"] += 1
        elif bit <= today + timedelta(days=SOON_DAYS):
            g["bitis_yakin"] += 1
        else:
            g["bitis_ileri"] += 1
        g["abonelik_satisi"] += 1 if r.get("abonelik_satis") else 0
        g["reco"] += 1 if r.get("reco") else 0
        since = r.get("free_since")
        if g["tur"] == "pasif" and since:
            g["cikis_30g"] += 1 if since >= today - timedelta(days=30) else 0
            g["cikis_bu_yil"] += 1 if since.year == today.year else 0
            if since.year >= today.year - 1:
                exits[month_first(since)][key] += 1
    order = list(GROUPS)
    out = sorted(groups.values(), key=lambda g: order.index(g["key"]))
    for g in out:
        g["pay"] = round(100 * g["musteri"] / total, 1) if total else None
    tur = {t: sum(g["musteri"] for g in out if g["tur"] == t) for t in ("aktif", "pasif", "bos")}
    return {
        "toplam": total,
        "aktif": tur["aktif"],
        "pasif": tur["pasif"],
        "bos": tur["bos"],
        "gruplar": out,
        "cikislar": [
            {"ay": m, "toplam": sum(v.values()), **{k: v.get(k, 0) for k in GROUPS if GROUPS[k][1] == "pasif"}}
            for m, v in sorted(exits.items())
        ],
        "yakin_gun": SOON_DAYS,
    }


def load_paket(today: date, owner: str | None = None) -> dict[str, Any]:
    org_id = get_org_id()
    with connect() as conn:
        ok = conn.execute("SELECT to_regclass('contact_profiles') IS NOT NULL").fetchone()
        if not ok or not ok[0]:
            return {"available": False}
        has_deals = conn.execute(
            """SELECT EXISTS (SELECT 1 FROM information_schema.columns
                              WHERE table_name = 'deal_packages' AND column_name = 'contact_id')"""
        ).fetchone()[0]
        deals_sql = (
            """bool_or(d.package = ANY(%s)), bool_or(d.package = %s)
               FROM contact_profiles c
               LEFT JOIN deal_packages d ON d.org_id = c.org_id AND d.contact_id = c.contact_id AND d.stage = %s"""
            if has_deals
            else "false, false FROM contact_profiles c"
        )
        params: list[Any] = [list(SUBSCRIPTION_PACKAGES), RENEWAL_PACKAGE, WON_STAGE] if has_deals else []
        params.append(org_id)
        owner_sql = ""
        if owner:
            owner_sql = " AND c.owner_name = %s"
            params.append(owner)
        rows = conn.execute(
            f"""
            SELECT c.paket, c.owner_name, c.son1_satis, c.abonelik_bit,
                   (to_jsonb(c) ->> 'free_since')::date, {deals_sql}
            WHERE c.org_id = %s {owner_sql}
            GROUP BY c.contact_id, c.paket, c.owner_name, c.son1_satis, c.abonelik_bit, to_jsonb(c) ->> 'free_since'
            """,
            params,
        ).fetchall()
    data = summarize(
        (
            {"paket": p, "owner": o, "son1": s, "bit": b, "free_since": f, "abonelik_satis": a, "reco": r}
            for p, o, s, b, f, a, r in rows
        ),
        today,
    )
    return {"available": True, "owner": owner, **data}
