"""Lead'ler için yerel pusula_state hesaplar (Zoho'ya YAZMAZ).

Kurallar (öncelik sırasıyla):
  1. closed  — Zoho status sonuçlanmış (Randevu, Nitelikli Değil*,
               Aranmamalı, Almadı-*, Mevcut Müşteri, İletişim Kurulamadı…)
  2. nurture — temas kurulmuş canlı lead (Satış Fırsatı, Düşünmek İstiyor,
               Müsait Olmadığını Belirtti); 48s kuralı uygulanmaz
  3. 48s / 3 arama kuralı SADECE şu Zoho statülerine:
       Yeni Müşteri Adayı, 1/2/3.Arama-Ulaşılamadı, Aging,
       Yabancı No/Mesaj-Mail Atıldı, Yabancı No Mesaj/Mail İletildi
         aging    — outbound >= 3 ve temas yok
         active   — pazarsız 48 saat dolmamış VE outbound < 3
                    (veya >=3 arama ama temas var)
         stale    — 48 saat dolmuş, arama < 3, atamadan < 10 gün
         archived — 48 saat dolmuş, arama < 3, atamadan >= 10 gün

Pazar hesabı: public.pazarsiz_saat(baslangic, bitis).
leads.status (Zoho alanı) okunur, asla güncellenmez.

Kullanım:
    python scripts/update_lead_status.py
    python scripts/update_lead_status.py --apply

Varsayılan dry-run. .env otomatik yüklenir; DATABASE_URL gerekir.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from pusula.config import get_org_id

_STATES = ("active", "stale", "aging", "archived", "nurture", "closed")

# Sonraki pusula_state; leads.status'a dokunulmaz.
_CANDIDATES_SQL = """
    SELECT
        l.lead_id,
        CASE
            WHEN l.status IN (
                    'Randevu',
                    'Aranmamalı',
                    'Mevcut Müşteri',
                    'İletişim Kurulamadı/Numara Hatalı'
                )
                OR l.status LIKE 'Almadı-%%'
                OR l.status LIKE 'Nitelikli Değil%%'
                THEN 'closed'
            WHEN l.status IN (
                    'Satış Fırsatı',
                    'Düşünmek İstiyor',
                    'Müsait Olmadığını Belirtti'
                )
                THEN 'nurture'
            WHEN l.status IN (
                    'Yeni Müşteri Adayı',
                    '1.Arama-Ulaşılamadı',
                    '2.Arama-Ulaşılamadı',
                    '3.Arama-Ulaşılamadı',
                    'Aging',
                    'Yabancı No/Mesaj-Mail Atıldı',
                    'Yabancı No Mesaj/Mail İletildi'
                )
                THEN CASE
                    WHEN outbound_calls >= 3 AND NOT has_temas THEN 'aging'
                    WHEN outbound_calls >= 3 AND has_temas THEN 'active'
                    WHEN pazar_h < 48 AND outbound_calls < 3 THEN 'active'
                    WHEN pazar_h >= 48 AND outbound_calls < 3
                         AND age < interval '10 days' THEN 'stale'
                    WHEN pazar_h >= 48 AND outbound_calls < 3
                         AND age >= interval '10 days' THEN 'archived'
                    ELSE 'active'
                END
            ELSE 'active'
        END AS next_state
    FROM (
        SELECT
            l.lead_id,
            l.status,
            public.pazarsiz_saat(
                coalesce(l.assigned_at, l.created_at),
                now()
            ) AS pazar_h,
            now() - coalesce(l.assigned_at, l.created_at) AS age,
            (
                SELECT count(*)::int
                FROM events e
                WHERE e.org_id = l.org_id
                  AND e.thread_id = l.thread_id
                  AND e.channel = 'call'
                  AND e.direction = 'outbound'
                  AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
                  AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
            ) AS outbound_calls,
            EXISTS (
                SELECT 1
                FROM events e
                WHERE e.org_id = l.org_id
                  AND e.thread_id = l.thread_id
                  AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
                  AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
                  AND (
                      (e.channel = 'call' AND e.direction = 'inbound')
                      OR e.channel = 'meeting'
                      OR (
                          e.channel = 'call'
                          AND coalesce(e.meta->>'outcome_key', '')
                              NOT IN ('', 'no_answer', 'invalid_number')
                      )
                  )
            ) AS has_temas
        FROM leads l
        WHERE l.org_id = %s
    ) l
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lead pusula_state günceller (Zoho'ya yazmaz)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Gerçekten yaz (varsayılan: dry-run)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL ortam değişkeni tanımlı değil")
        return 1

    org_id = get_org_id()
    errors = 0
    try:
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(_CANDIDATES_SQL, (org_id,)).fetchall()
            by_state: dict[str, list[str]] = defaultdict(list)
            for lead_id, next_state in rows:
                by_state[str(next_state)].append(str(lead_id))

            print(f"lead: {len(rows)} (org={org_id})")
            for state in _STATES:
                print(f"  -> {state}: {len(by_state.get(state, []))}")
            other = set(by_state) - set(_STATES)
            for state in sorted(other):
                print(f"  -> {state}: {len(by_state[state])}")

            if dry_run:
                print("dry-run: yazılmadı. Yazmak için --apply kullan.")
                return 0

            written: dict[str, int] = {s: 0 for s in _STATES}
            for state in _STATES:
                lead_ids = by_state.get(state, [])
                if not lead_ids:
                    continue
                try:
                    cur = conn.execute(
                        """
                        UPDATE leads SET
                            pusula_state = %s,
                            pusula_state_at = now()
                        WHERE org_id = %s
                          AND lead_id = ANY(%s)
                          AND pusula_state IS DISTINCT FROM %s
                        """,
                        (state, org_id, lead_ids, state),
                    )
                    written[state] = int(cur.rowcount)
                except psycopg.Error as exc:
                    errors += 1
                    print(f"hata ({state}): {exc}")

            print(
                "yazıldı: "
                + ", ".join(f"{s}={written[s]}" for s in _STATES)
                + f", hata={errors}"
            )
    except psycopg.Error as exc:
        print(f"lead status güncelleme başarısız: {exc}")
        return 1

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
