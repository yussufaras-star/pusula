"""Lead–contact–deal zincirini onarır (geriye dönük).

Teşhis + onarım:
  1) Aynı normalize telefon/email ile lead+contact farklı thread mi?
  2) Zoho Leads.Converted_Contact üzerinden zoho_lead + zoho_contact
     aynı thread'e düşürülür (isim benzerliği yok).
  3) contacts.lead_id ve deals.lead_id doldurulur.
  4) Eksik converted lead satırları sync_lead_identities ile yazılır.

Zoho'da Contacts.Lead_Id / Deals.Lead_Id yok (zoho_schema_check);
eşleme Converted_Contact + telefon/email.

Kullanım:
    python scripts/repair_lead_contact_chain.py
    python scripts/repair_lead_contact_chain.py --diagnose-only
    python scripts/repair_lead_contact_chain.py --apply

Varsayılan dry-run. DATABASE_URL gerekir.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.config import get_org_id
from pusula.db import client
from pusula.db.identity import resolve_thread_detailed
from pusula.ingest.lead_identity import sync_lead_identities
from pusula.zoho.crm import coql

_CHUNK = 100


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _lookup_id(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("id"):
        return str(value["id"])
    return _as_str(value)


def diagnose(conn: Any, org_id: str) -> dict[str, int]:
    """Rakamları bas; tahmin yok."""
    stats: dict[str, int] = {}

    row = conn.execute(
        """
        SELECT
          count(*) AS contacts,
          count(lead_id) AS with_lead_id,
          count(thread_id) AS with_thread
        FROM contacts WHERE org_id = %s
        """,
        (org_id,),
    ).fetchone()
    stats["contacts"] = int(row[0])
    stats["contacts_with_lead_id"] = int(row[1])
    print(
        f"contacts: {row[0]} lead_id_dolu={row[1]} threadli={row[2]}"
    )

    row = conn.execute(
        """
        SELECT
          count(*) AS deals,
          count(lead_id) AS with_lead_id,
          count(thread_id) AS with_thread
        FROM deals WHERE org_id = %s
        """,
        (org_id,),
    ).fetchone()
    stats["deals"] = int(row[0])
    stats["deals_with_lead_id"] = int(row[1])
    print(f"deals: {row[0]} lead_id_dolu={row[1]} threadli={row[2]}")

    row = conn.execute(
        """
        SELECT count(DISTINCT l.lead_id)
        FROM leads l
        JOIN deals d ON d.org_id = l.org_id AND d.thread_id = l.thread_id
        WHERE l.org_id = %s
        """,
        (org_id,),
    ).fetchone()
    stats["lead_deal_thread_join"] = int(row[0])
    print(f"lead-deal ayni thread (distinct lead): {row[0]}")

    # Aynı telefon kimliği lead+contact satırında farklı thread?
    # identities unique → aynı id_value tek thread; yine de ölç.
    row = conn.execute(
        """
        WITH lead_phones AS (
          SELECT i.id_value AS phone, l.thread_id AS lead_thread
          FROM identities i
          JOIN leads l ON l.org_id = i.org_id AND l.thread_id = i.thread_id
          WHERE i.org_id = %s AND i.id_type = 'phone'
        ),
        contact_phones AS (
          SELECT i.id_value AS phone, c.thread_id AS contact_thread
          FROM identities i
          JOIN contacts c ON c.org_id = i.org_id AND c.thread_id = i.thread_id
          WHERE i.org_id = %s AND i.id_type = 'phone'
        )
        SELECT
          count(*) AS pairs,
          count(*) FILTER (WHERE lead_thread = contact_thread) AS same_thread,
          count(*) FILTER (WHERE lead_thread <> contact_thread) AS diff_thread
        FROM lead_phones lp
        JOIN contact_phones cp ON cp.phone = lp.phone
        """,
        (org_id, org_id),
    ).fetchone()
    stats["phone_pairs"] = int(row[0])
    stats["phone_same_thread"] = int(row[1])
    stats["phone_diff_thread"] = int(row[2])
    print(
        f"aynı telefon lead+contact: çift={row[0]} "
        f"aynı_thread={row[1]} FARKLI_thread={row[2]}"
    )

    row = conn.execute(
        """
        WITH lead_emails AS (
          SELECT i.id_value AS email, l.thread_id AS lead_thread
          FROM identities i
          JOIN leads l ON l.org_id = i.org_id AND l.thread_id = i.thread_id
          WHERE i.org_id = %s AND i.id_type = 'email'
        ),
        contact_emails AS (
          SELECT i.id_value AS email, c.thread_id AS contact_thread
          FROM identities i
          JOIN contacts c ON c.org_id = i.org_id AND c.thread_id = i.thread_id
          WHERE i.org_id = %s AND i.id_type = 'email'
        )
        SELECT
          count(*) AS pairs,
          count(*) FILTER (WHERE lead_thread = contact_thread) AS same_thread,
          count(*) FILTER (WHERE lead_thread <> contact_thread) AS diff_thread
        FROM lead_emails le
        JOIN contact_emails ce ON ce.email = le.email
        """,
        (org_id, org_id),
    ).fetchone()
    stats["email_pairs"] = int(row[0])
    stats["email_diff_thread"] = int(row[2])
    print(
        f"aynı email lead+contact: çift={row[0]} "
        f"aynı_thread={row[1]} FARKLI_thread={row[2]}"
    )

    row = conn.execute(
        """
        SELECT count(*) FROM (
          SELECT thread_id FROM identities
          WHERE org_id = %s AND id_type = 'zoho_lead'
          INTERSECT
          SELECT thread_id FROM identities
          WHERE org_id = %s AND id_type = 'zoho_contact'
        ) t
        """,
        (org_id, org_id),
    ).fetchone()
    stats["threads_with_both"] = int(row[0])
    print(f"thread'de hem zoho_lead hem zoho_contact: {row[0]}")

    return stats


def _fetch_converted_pairs_raw() -> list[dict[str, Any]]:
    """Ham Zoho kayıtları + parse edilmiş id'ler (teşhis için)."""
    out: list[dict[str, Any]] = []
    for rec in coql(
        "select id, Converted_Contact from Leads "
        "where Converted__s = true"
    ):
        raw_cc = rec.get("Converted_Contact")
        lead_id = _as_str(rec.get("id"))
        contact_id = _lookup_id(raw_cc)
        out.append(
            {
                "zoho_raw_id": rec.get("id"),
                "zoho_raw_id_type": type(rec.get("id")).__name__,
                "Converted_Contact_raw": raw_cc,
                "Converted_Contact_raw_type": type(raw_cc).__name__,
                "lead_id": lead_id,
                "contact_id": contact_id,
            }
        )
    return out


def diagnose_converted_path(conn: Any, org_id: str) -> None:
    """Converted_Contact yolunu kategori bazında say (yazmadan)."""
    print("\n=== Converted_Contact yol teşhisi (kategori) ===")
    print(
        "sayac aciklamasi: dry-run'da 'resolved' = cift sayildi "
        "(resolve_thread CAGRILMAZ); 'linked' yalniz --apply ve "
        "thread_id donunce artar. Bu yuzden resolved>0 linked=0 "
        "dry-run'da beklenen celiski, baglama basarisizligi degil."
    )
    raw_rows = _fetch_converted_pairs_raw()
    # Cift = Converted_Contact id parse edilebilenler
    pairs = [r for r in raw_rows if r["lead_id"] and r["contact_id"]]
    cats: dict[str, int] = defaultdict(int)
    samples: list[dict[str, Any]] = []

    for r in raw_rows:
        if not r["lead_id"]:
            cats["lead_id parse edilemedi"] += 1
            continue
        if not r["contact_id"]:
            cats["Converted_Contact bos/parse yok"] += 1
            continue

        lead_id = r["lead_id"]
        contact_id = r["contact_id"]

        c_row = conn.execute(
            """
            SELECT contact_id, lead_id, thread_id FROM contacts
            WHERE org_id = %s AND contact_id = %s
            """,
            (org_id, contact_id),
        ).fetchone()
        c_lead: str | None = None
        c_thread: str | None = None

        if c_row is None:
            # Tip/cast alternatif: ayni string baska yolda bulunur mu?
            id_row = conn.execute(
                """
                SELECT thread_id FROM identities
                WHERE org_id = %s AND id_type = 'zoho_contact'
                  AND id_value = %s
                LIMIT 1
                """,
                (org_id, contact_id),
            ).fetchone()
            if id_row is None:
                cats["contact bulunamadi (contacts+identities)"] += 1
                _maybe_sample(samples, r, None, None, "contact yok")
                continue
            c_thread = str(id_row[0])
            c_lead = None
            # contacts satir yok ama identity var — ayri kategori
            # asagida lead_id / thread ile devam; once bu etiketi basmak
            # icin exclusive kategori kullan (identity-only)
            cats_prefix = "contact satiri yok ama zoho_contact identity var; "
        else:
            cats_prefix = ""
            c_lead = str(c_row[1]) if c_row[1] else None
            c_thread = str(c_row[2]) if c_row[2] else None

        l_row = conn.execute(
            """
            SELECT lead_id, thread_id FROM leads
            WHERE org_id = %s AND lead_id = %s
            """,
            (org_id, lead_id),
        ).fetchone()
        zl_row = conn.execute(
            """
            SELECT thread_id FROM identities
            WHERE org_id = %s AND id_type = 'zoho_lead' AND id_value = %s
            LIMIT 1
            """,
            (org_id, lead_id),
        ).fetchone()
        l_thread = (
            str(l_row[1])
            if l_row and l_row[1]
            else (str(zl_row[0]) if zl_row else None)
        )

        if c_lead == lead_id:
            cats[cats_prefix + "lead_id zaten dolu (dogru lead)"] += 1
            _maybe_sample(samples, r, c_lead, l_thread, "zaten dolu")
            continue

        if c_lead is not None and c_lead != lead_id:
            cats[cats_prefix + "lead_id dolu ama farkli lead"] += 1
            _maybe_sample(samples, r, c_lead, l_thread, "farkli lead_id")
            continue

        if c_thread and l_thread and c_thread == l_thread:
            cats[cats_prefix + "thread eslesiyor ama lead_id bos (refill adayi)"] += 1
            _maybe_sample(samples, r, c_lead, l_thread, "refill adayi")
            continue

        if c_thread and l_thread and c_thread != l_thread:
            cats[cats_prefix + "thread eslesmiyor (merge gerekir)"] += 1
            _maybe_sample(samples, r, c_lead, l_thread, "thread farkli")
            continue

        if l_thread is None and zl_row is None and l_row is None:
            cats[cats_prefix + "lead bulunamadi (leads+zoho_lead identity yok)"] += 1
            _maybe_sample(samples, r, c_lead, None, "lead yok")
            continue

        if c_thread is None:
            cats[cats_prefix + "contact thread yok"] += 1
            _maybe_sample(samples, r, c_lead, l_thread, "contact thread yok")
            continue

        cats[cats_prefix + "kosul disi / diger"] += 1
        _maybe_sample(samples, r, c_lead, l_thread, "diger")

    # Id tip/format ozeti (exclusive degil; ayri rapor)
    fmt_lead_strip = 0
    fmt_cc_strip = 0
    fmt_cc_dict = 0
    fmt_cc_str = 0
    for raw in pairs:
        if str(raw["zoho_raw_id"]).strip() != raw["lead_id"]:
            fmt_lead_strip += 1
        cc = raw["Converted_Contact_raw"]
        if isinstance(cc, dict):
            fmt_cc_dict += 1
            cid = cc.get("id")
            if cid is not None and str(cid).strip() != raw["contact_id"]:
                fmt_cc_strip += 1
        elif isinstance(cc, str):
            fmt_cc_str += 1
    print(
        f"id format ozeti (ciftler, exclusive degil): "
        f"Converted_Contact dict={fmt_cc_dict} str={fmt_cc_str}, "
        f"lead strip farki={fmt_lead_strip}, contact strip farki={fmt_cc_strip}"
    )

    total_in = len(raw_rows)
    cat_sum = sum(cats.values())
    print(f"girdi (Zoho Converted__s=true kayit): {total_in}")
    print(f"parse edilmis cift (lead+contact id): {len(pairs)}")
    for k in sorted(cats.keys()):
        print(f"  {k}: {cats[k]}")
    print(f"kategori toplami: {cat_sum} (girdi ile esit mi: {cat_sum == total_in})")

    print(
        f"\nmevcut dry-run sayac simulasyonu: "
        f"resolved={len(pairs)} linked=0 "
        f"(apply=False → resolve_thread atlanir, linked hic artmaz)"
    )

    print("\n=== 5 ornek kayit (ham) ===")
    shown = samples[:5]
    if len(shown) < 5:
        for r in pairs[:(5 - len(shown))]:
            shown.append(
                {
                    "raw": r,
                    "local_lead_id": None,
                    "lead_thread": None,
                    "note": "ornek",
                }
            )
    for i, s in enumerate(shown, 1):
        r = s["raw"]
        print(f"--- ornek {i} ({s.get('note')}) ---")
        print(f"  Zoho id ham: {r['zoho_raw_id']!r} type={r['zoho_raw_id_type']}")
        print(
            f"  Converted_Contact ham: {r['Converted_Contact_raw']!r} "
            f"type={r['Converted_Contact_raw_type']}"
        )
        print(f"  parse lead_id: {r['lead_id']!r}")
        print(f"  parse contact_id: {r['contact_id']!r}")
        print(f"  local contacts.lead_id: {s.get('local_lead_id')!r}")
        print(f"  lead/identity thread: {s.get('lead_thread')!r}")


def _maybe_sample(
    samples: list[dict[str, Any]],
    raw: dict[str, Any],
    local_lead_id: str | None,
    lead_thread: str | None,
    note: str,
) -> None:
    if len(samples) >= 8:
        return
    # Cesitlilik: ayni note'tan en fazla 2
    same = sum(1 for s in samples if s.get("note") == note)
    if same >= 2:
        return
    samples.append(
        {
            "raw": raw,
            "local_lead_id": local_lead_id,
            "lead_thread": lead_thread,
            "note": note,
        }
    )


def diagnose_rematch_path(conn: Any, org_id: str) -> None:
    """Rematch yolunu kategori bazında say (yazmadan, tek sorgular).

    Rematch leads tablosunda telefon aramaz: contact thread'indeki
    identities phone/email + zoho_contact ile resolve_thread_detailed
    cagirir; lead_id sonra _lead_for_thread (zoho_lead identity → leads)
    ile bulunur. Degerler zaten identities'te normalize halde durur
    (ingest'te normalize_phone/email).
    """
    print("\n=== rematch yol teşhisi (kategori) ===")
    print(
        "arama yolu: identities (phone/email on contact.thread) + "
        "resolve_thread(zoho_contact); leads.phone kolonu YOK. "
        "normalize: ingest yazarken yapildi; rematch id_value'yu "
        "oldugu gibi resolve_thread'e verir (normalize_phone tekrar)."
    )
    print(
        "mevcut dry-run: rematch() erken return → resolved=0 leadli=0 "
        "(hic kategori sayilmaz; asagidaki bu boslugu doldurur)."
    )

    from pusula.db.identity import normalize_email, normalize_phone

    row = conn.execute(
        """
        WITH base AS (
          SELECT
            c.contact_id,
            c.lead_id AS c_lead,
            c.thread_id,
            (
              SELECT i.id_value FROM identities i
              WHERE i.org_id = c.org_id AND i.thread_id = c.thread_id
                AND i.id_type = 'phone'
              ORDER BY i.id_value LIMIT 1
            ) AS phone,
            (
              SELECT i.id_value FROM identities i
              WHERE i.org_id = c.org_id AND i.thread_id = c.thread_id
                AND i.id_type = 'email'
              ORDER BY i.id_value LIMIT 1
            ) AS email,
            (
              SELECT i.id_value FROM identities i
              WHERE i.org_id = c.org_id AND i.thread_id = c.thread_id
                AND i.id_type = 'zoho_lead'
              ORDER BY i.id_value LIMIT 1
            ) AS thread_zoho_lead,
            (
              SELECT l.lead_id FROM leads l
              WHERE l.org_id = c.org_id AND l.thread_id = c.thread_id
              ORDER BY l.assigned_at ASC NULLS LAST LIMIT 1
            ) AS thread_lead_row
          FROM contacts c
          WHERE c.org_id = %s
        ),
        classified AS (
          SELECT
            contact_id,
            phone,
            email,
            CASE
              WHEN c_lead IS NOT NULL
                   AND coalesce(thread_zoho_lead, thread_lead_row) = c_lead
                THEN 'lead_id zaten dolu'
              WHEN c_lead IS NOT NULL
                   AND coalesce(thread_zoho_lead, thread_lead_row) IS NOT NULL
                   AND coalesce(thread_zoho_lead, thread_lead_row) <> c_lead
                THEN 'lead_id dolu ama thread''de farkli zoho_lead'
              WHEN c_lead IS NOT NULL
                THEN 'lead_id dolu ama thread''de lead bulunamadi'
              WHEN coalesce(thread_zoho_lead, thread_lead_row) IS NOT NULL
                THEN 'lead bulundu (refill adayi, lead_id bos)'
              WHEN phone IS NULL AND email IS NULL
                THEN 'telefon/email yok (identity)'
              WHEN EXISTS (
                SELECT 1 FROM identities i
                JOIN identities zl
                  ON zl.org_id = i.org_id AND zl.thread_id = i.thread_id
                 AND zl.id_type = 'zoho_lead'
                WHERE i.org_id = %s
                  AND (
                    (phone IS NOT NULL AND i.id_type = 'phone'
                     AND i.id_value = phone)
                    OR (email IS NOT NULL AND i.id_type = 'email'
                     AND i.id_value = email)
                  )
              )
                THEN 'lead identity ayni phone/email thread''de (resolve bulur)'
              ELSE 'lead bulunamadi (phone/email ile zoho_lead yok)'
            END AS cat
          FROM base
        )
        SELECT cat, count(*)::int FROM classified GROUP BY 1 ORDER BY 1
        """,
        (org_id, org_id),
    ).fetchall()

    cats = {str(r[0]): int(r[1]) for r in row}
    total_in = sum(cats.values())
    print(f"girdi (contacts): {total_in}")
    for k in sorted(cats.keys()):
        print(f"  {k}: {cats[k]}")
    print(
        f"kategori toplami: {total_in} "
        f"(girdi ile esit mi: {total_in == total_in})"
    )

    # Normalize drift: sample phones/emails from identities on contact threads
    samples = conn.execute(
        """
        SELECT i.id_type, i.id_value
        FROM identities i
        JOIN contacts c ON c.org_id = i.org_id AND c.thread_id = i.thread_id
        WHERE i.org_id = %s AND i.id_type IN ('phone', 'email')
        """,
        (org_id,),
    ).fetchall()
    norm_phone_ok = norm_phone_drift = 0
    norm_email_ok = norm_email_drift = 0
    for id_type, id_value in samples:
        val = str(id_value)
        if id_type == "phone":
            renorm = normalize_phone(val)
            if renorm == val:
                norm_phone_ok += 1
            else:
                norm_phone_drift += 1
        else:
            renorm_e = normalize_email(val)
            if renorm_e == val:
                norm_email_ok += 1
            else:
                norm_email_drift += 1
    print(
        f"normalize dogrulama: phone ok={norm_phone_ok} "
        f"drift={norm_phone_drift}, "
        f"email ok={norm_email_ok} drift={norm_email_drift}"
    )
    print(
        f"\nmevcut dry-run sayac simulasyonu: "
        f"contacts={total_in} resolved=0 leadli=0 "
        f"(apply=False → fonksiyon hemen return)"
    )



def _fetch_converted_pairs() -> list[tuple[str, str]]:
    """(lead_id, contact_id) — Converted_Contact dolu olanlar."""
    pairs: list[tuple[str, str]] = []
    for rec in _fetch_converted_pairs_raw():
        if rec["lead_id"] and rec["contact_id"]:
            pairs.append((rec["lead_id"], rec["contact_id"]))
    return pairs



def _contact_phones_emails(
    conn: Any, org_id: str, contact_id: str
) -> tuple[str | None, str | None]:
    """Contact thread'indeki ilk phone/email (normalize edilmiş)."""
    row = conn.execute(
        """
        SELECT thread_id FROM contacts
        WHERE org_id = %s AND contact_id = %s
        """,
        (org_id, contact_id),
    ).fetchone()
    if not row or not row[0]:
        # identities'te zoho_contact
        row = conn.execute(
            """
            SELECT thread_id FROM identities
            WHERE org_id = %s AND id_type = 'zoho_contact' AND id_value = %s
            LIMIT 1
            """,
            (org_id, contact_id),
        ).fetchone()
    if not row or not row[0]:
        return None, None
    thread_id = str(row[0])
    phone_row = conn.execute(
        """
        SELECT id_value FROM identities
        WHERE org_id = %s AND thread_id = %s AND id_type = 'phone'
        ORDER BY id_value LIMIT 1
        """,
        (org_id, thread_id),
    ).fetchone()
    email_row = conn.execute(
        """
        SELECT id_value FROM identities
        WHERE org_id = %s AND thread_id = %s AND id_type = 'email'
        ORDER BY id_value LIMIT 1
        """,
        (org_id, thread_id),
    ).fetchone()
    return (
        str(phone_row[0]) if phone_row else None,
        str(email_row[0]) if email_row else None,
    )


def link_converted_pairs(
    conn: Any, org_id: str, pairs: list[tuple[str, str]], *, apply: bool
) -> tuple[dict[str, int], list[str]]:
    """Converted_Contact çiftlerini resolve_thread ile birleştir."""
    stats = {
        "pairs": len(pairs),
        "resolved": 0,
        "merged_or_linked": 0,
        "errors": 0,
        "skipped_no_contact": 0,
    }
    client.load_blocklist(conn)
    lead_ids: list[str] = []

    for lead_id, contact_id in pairs:
        phone, email = _contact_phones_emails(conn, org_id, contact_id)

        if not apply:
            stats["resolved"] += 1
            lead_ids.append(lead_id)
            continue

        try:
            thread_id, _created = resolve_thread_detailed(
                phone=phone,
                email=email,
                zoho_lead_id=lead_id,
                zoho_contact_id=contact_id,
                conn=conn,
            )
            if thread_id:
                stats["resolved"] += 1
                stats["merged_or_linked"] += 1
                lead_ids.append(lead_id)
            else:
                stats["errors"] += 1
        except Exception as exc:
            stats["errors"] += 1
            print(f"  hata lead={lead_id} contact={contact_id}: {exc}")

    return stats, list(dict.fromkeys(lead_ids))


def rematch_contacts_by_phone_email(
    org_id: str, *, apply: bool, database_url: str
) -> dict[str, int]:
    """Contact'ları phone/email + zoho_contact ile yeniden çöz (chunked)."""
    stats = {"contacts": 0, "resolved": 0, "with_lead": 0, "errors": 0}
    import psycopg

    with psycopg.connect(database_url) as conn:
        client.load_blocklist(conn)
        rows = conn.execute(
            """
            SELECT c.contact_id,
              (
                SELECT i.id_value FROM identities i
                WHERE i.org_id = c.org_id AND i.thread_id = c.thread_id
                  AND i.id_type = 'phone'
                ORDER BY i.id_value LIMIT 1
              ) AS phone,
              (
                SELECT i.id_value FROM identities i
                WHERE i.org_id = c.org_id AND i.thread_id = c.thread_id
                  AND i.id_type = 'email'
                ORDER BY i.id_value LIMIT 1
              ) AS email
            FROM contacts c
            WHERE c.org_id = %s
            ORDER BY c.contact_id
            """,
            (org_id,),
        ).fetchall()
    stats["contacts"] = len(rows)
    if not apply:
        print(f"rematch dry-run: {len(rows)} contact (yazılmadı)")
        return stats

    batch = 50
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        try:
            with psycopg.connect(database_url) as conn:
                client.load_blocklist(conn)
                for contact_id, phone, email in chunk:
                    try:
                        thread_id, _ = resolve_thread_detailed(
                            phone=str(phone) if phone else None,
                            email=str(email) if email else None,
                            zoho_contact_id=str(contact_id),
                            conn=conn,
                        )
                        if not thread_id:
                            continue
                        stats["resolved"] += 1
                        lead_id = _lead_for_thread(conn, org_id, thread_id)
                        conn.execute(
                            """
                            UPDATE contacts SET
                              thread_id = %s,
                              lead_id = COALESCE(%s, lead_id)
                            WHERE org_id = %s AND contact_id = %s
                            """,
                            (thread_id, lead_id, org_id, str(contact_id)),
                        )
                        if lead_id:
                            stats["with_lead"] += 1
                    except Exception as exc:
                        stats["errors"] += 1
                        print(f"  rematch hata contact={contact_id}: {exc}")
                conn.commit()
            print(
                f"  rematch chunk {i}-{i + len(chunk)} "
                f"resolved={stats['resolved']} hata={stats['errors']}",
                flush=True,
            )
        except Exception as exc:
            stats["errors"] += len(chunk)
            print(f"  rematch chunk fail {i}: {exc}", flush=True)
    return stats


def _lead_for_thread(conn: Any, org_id: str, thread_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT id_value FROM identities
        WHERE org_id = %s AND thread_id = %s AND id_type = 'zoho_lead'
        ORDER BY id_value LIMIT 1
        """,
        (org_id, thread_id),
    ).fetchone()
    if row:
        return str(row[0])
    row = conn.execute(
        """
        SELECT lead_id FROM leads
        WHERE org_id = %s AND thread_id = %s
        ORDER BY assigned_at ASC NULLS LAST LIMIT 1
        """,
        (org_id, thread_id),
    ).fetchone()
    return str(row[0]) if row else None


def refill_contact_lead_ids(conn: Any, org_id: str) -> int:
    """contacts.lead_id = thread üzerindeki zoho_lead / leads."""
    rows = conn.execute(
        """
        SELECT contact_id, thread_id FROM contacts
        WHERE org_id = %s AND thread_id IS NOT NULL
        """,
        (org_id,),
    ).fetchall()
    updated = 0
    for contact_id, thread_id in rows:
        lead_id = _lead_for_thread(conn, org_id, str(thread_id))
        if not lead_id:
            continue
        cur = conn.execute(
            """
            UPDATE contacts SET lead_id = %s
            WHERE org_id = %s AND contact_id = %s
              AND lead_id IS DISTINCT FROM %s
            """,
            (lead_id, org_id, str(contact_id), lead_id),
        )
        updated += cur.rowcount or 0
    return updated


def refill_deal_lead_ids(conn: Any, org_id: str) -> int:
    """deals.lead_id / thread_id: contact → identity fallback."""
    updated = 0
    rows = conn.execute(
        """
        SELECT d.deal_id, d.contact_id, d.lead_id, d.thread_id,
               c.lead_id AS c_lead, c.thread_id AS c_thread
        FROM deals d
        LEFT JOIN contacts c
          ON c.org_id = d.org_id AND c.contact_id = d.contact_id
        WHERE d.org_id = %s
        """,
        (org_id,),
    ).fetchall()
    for deal_id, contact_id, d_lead, d_thread, c_lead, c_thread in rows:
        lead_id = c_lead or d_lead
        thread_id = c_thread or d_thread
        if contact_id and (lead_id is None or thread_id is None):
            row = conn.execute(
                """
                SELECT thread_id FROM identities
                WHERE org_id = %s AND id_type = 'zoho_contact'
                  AND id_value = %s
                LIMIT 1
                """,
                (org_id, str(contact_id)),
            ).fetchone()
            if row and row[0]:
                thread_id = thread_id or str(row[0])
                if lead_id is None:
                    lead_id = _lead_for_thread(conn, org_id, str(row[0]))
        if lead_id is None and thread_id is not None:
            lead_id = _lead_for_thread(conn, org_id, str(thread_id))
        if lead_id is None and thread_id is None:
            continue
        # cycle_start_at lead assigned_at
        cycle = None
        reliable = False
        if lead_id:
            lr = conn.execute(
                """
                SELECT assigned_at FROM leads
                WHERE org_id = %s AND lead_id = %s
                """,
                (org_id, str(lead_id)),
            ).fetchone()
            if lr and lr[0]:
                cycle = lr[0]
                reliable = True
        cur = conn.execute(
            """
            UPDATE deals SET
              lead_id = COALESCE(%s, lead_id),
              thread_id = COALESCE(%s, thread_id),
              cycle_start_at = COALESCE(%s, cycle_start_at),
              cycle_start_reliable = CASE
                WHEN %s THEN true ELSE cycle_start_reliable END
            WHERE org_id = %s AND deal_id = %s
              AND (
                lead_id IS DISTINCT FROM COALESCE(%s, lead_id)
                OR thread_id IS DISTINCT FROM COALESCE(%s, thread_id)
              )
            """,
            (
                lead_id,
                thread_id,
                cycle,
                reliable,
                org_id,
                str(deal_id),
                lead_id,
                thread_id,
            ),
        )
        updated += cur.rowcount or 0
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lead–contact–deal zincir onarımı"
    )
    parser.add_argument(
        "--diagnose-only",
        action="store_true",
        help="Sadece teşhis rakamları",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="DB'ye yaz (yoksa dry-run)",
    )
    parser.add_argument(
        "--rematch",
        action="store_true",
        help="Tüm contact phone/email rematch (ağır; varsayılan kapalı)",
    )
    parser.add_argument(
        "--skip-converted",
        action="store_true",
        help="Converted_Contact adımını atla (sadece refill)",
    )
    args = parser.parse_args()
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL yok")
        return 1

    org_id = get_org_id()
    apply = bool(args.apply)

    print("=== teşhis (önce) ===")
    with client.transaction() as conn:
        before = diagnose(conn, org_id)

    # Uzun Zoho + kategori teshisi ayri baglantida (pool timeout onlemi)
    with client.transaction() as conn:
        diagnose_converted_path(conn, org_id)
    with client.transaction() as conn:
        diagnose_rematch_path(conn, org_id)

    if args.diagnose_only:
        return 0

    if not args.apply:
        print(
            "\n=== dry-run notu ===\n"
            "link_converted_pairs: apply=False iken her cift icin "
            "resolved+=1, resolve_thread CAGIRILMAZ, linked hic artmaz.\n"
            "rematch: apply=False iken erken return → resolved=0.\n"
            "Bu yuzden resolved=N linked=0 / rematch resolved=0 "
            "baglama basarisizligi degil; yazma kapali."
        )
        print("\n=== teşhis (sonra) ===")
        print("(dry-run: DB degismedi, once ile ayni)")
        with client.transaction() as conn:
            diagnose(conn, org_id)
        print("\ndry-run: yazılmadı. Uygulamak için --apply kullan.")
        return 0

    lead_ids: list[str] = []
    if not args.skip_converted:
        print("\n=== Zoho Converted_Contact çiftleri ===")
        pairs = _fetch_converted_pairs()
        print(f"Converted_Contact dolu çift: {len(pairs)}")

        print("\n=== Converted_Contact → resolve_thread ===")
        with client.transaction() as conn:
            link_stats, lead_ids = link_converted_pairs(
                conn, org_id, pairs, apply=apply
            )
            if not apply:
                conn.rollback()
        print(
            f"resolved={link_stats['resolved']} "
            f"linked={link_stats['merged_or_linked']} "
            f"skip_contact={link_stats['skipped_no_contact']} "
            f"hata={link_stats['errors']}"
        )

        if apply and lead_ids:
            print(f"\n=== sync_lead_identities ({len(lead_ids)} lead) ===")
            totals = defaultdict(int)
            for i in range(0, len(lead_ids), _CHUNK):
                chunk = set(lead_ids[i : i + _CHUNK])
                st = sync_lead_identities(chunk)
                for k, v in st.items():
                    totals[k] += v
                print(
                    f"  chunk {i // _CHUNK + 1}: "
                    f"yazilan={st.get('leads_written', 0)} "
                    f"hata={st.get('errors', 0)}"
                )
            print(
                f"toplam leads_written={totals['leads_written']} "
                f"hata={totals['errors']}"
            )

    if args.rematch:
        print("\n=== contact phone/email rematch ===")
        rematch = rematch_contacts_by_phone_email(
            org_id, apply=apply, database_url=database_url
        )
        print(
            f"contacts={rematch['contacts']} "
            f"resolved={rematch.get('resolved', 0)} "
            f"leadli={rematch.get('with_lead', 0)} "
            f"hata={rematch.get('errors', 0)}"
        )
    else:
        print("\n=== contact rematch atlandı ( --rematch ile aç ) ===")

    if apply:
        print("\n=== lead_id refill ===")
        with client.transaction() as conn:
            n_c = refill_contact_lead_ids(conn, org_id)
            n_d = refill_deal_lead_ids(conn, org_id)
        print(f"contacts.lead_id güncellenen={n_c}")
        print(f"deals.lead_id/thread güncellenen={n_d}")

    print("\n=== teşhis (sonra) ===")
    with client.transaction() as conn:
        after = diagnose(conn, org_id)

    if not apply:
        print("\ndry-run: yazılmadı. Uygulamak için --apply kullan.")
    else:
        print(
            "\nözet: "
            f"contacts.lead_id {before['contacts_with_lead_id']}→"
            f"{after['contacts_with_lead_id']}, "
            f"deals.lead_id {before['deals_with_lead_id']}→"
            f"{after['deals_with_lead_id']}, "
            f"lead-deal thread {before['lead_deal_thread_join']}->"
            f"{after['lead_deal_thread_join']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
