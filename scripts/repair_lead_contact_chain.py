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
    print(f"lead↔deal aynı thread (distinct lead): {row[0]}")

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


def _fetch_converted_pairs() -> list[tuple[str, str]]:
    """(lead_id, contact_id) — Converted_Contact dolu olanlar."""
    pairs: list[tuple[str, str]] = []
    for rec in coql(
        "select id, Converted_Contact from Leads "
        "where Converted__s = true"
    ):
        lead_id = _as_str(rec.get("id"))
        contact_id = _lookup_id(rec.get("Converted_Contact"))
        if lead_id and contact_id:
            pairs.append((lead_id, contact_id))
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

    if args.diagnose_only:
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
            f"lead↔deal thread {before['lead_deal_thread_join']}→"
            f"{after['lead_deal_thread_join']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
