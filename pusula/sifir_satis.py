"""İlk kez satın alan: contact başına ilk deal.

Hesap değişmez: contact'ın en eski (created_at) deal'i.
Contact'ı olmayan, doğrudan lead'e bağlı deal'lar da ilk kez satın alma.
TEKRAR SATIN ALAN — aynı contact'ın sonraki deal'ları.
ATIFSIZ — lead çözülemeyen; temsilci atfı yok.

Temsilci atfı: thread / lead üzerinden leads.owner_rep_id.
deals.owner_rep_id performans için kullanılmaz.

Contact yaşı eşiği yok; sıra yalnızca created_at (eşitlikte deal_id).

İç kind değerleri (sifir/tekrar/atifsiz) hesap için sabit kalır.
Rapor metni LABEL_* ve TERIM_* sabitlerinden gelir.
"""

from __future__ import annotations

from typing import Literal

SaleKind = Literal["sifir", "tekrar", "atifsiz"]

# İç kind; SQL CASE çıktısı değişmez.
ILK_SATIN_ALAN: SaleKind = "sifir"
TEKRAR_SATIN_ALAN: SaleKind = "tekrar"
ATIFSIZ: SaleKind = "atifsiz"

LABEL_ILK_SATIN_ALAN = "ilk kez satın alan"
LABEL_TEKRAR_SATIN_ALAN = "tekrar satın alan"
BASLIK_ILK_SATIN_ALAN = "İlk kez satın alan"

TERIM_ILK_SATIN_ALAN = (
    f"{BASLIK_ILK_SATIN_ALAN} — o kişiye yapılan ilk satış. "
    "Aynı kişiye sonradan yapılan satışlar tekrar satın alma sayılır."
)

# Zoho Deals Stage display (kazanılan).
WON_STAGE = "Kapandı Kazanıldı"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def won_stage_sql(alias: str = "cl") -> str:
    return f"{alias}.stage = {_sql_str(WON_STAGE)}"


def classified_deals_cte() -> str:
    """first_deal + classified. classified.org_id = %s (bir placeholder).

    classified.kind: sifir | tekrar | atifsiz (iç değer, hesap aynı)
    classified.lead_owner_rep_id: leads.owner_rep_id (deal owner değil).
    """
    ilk = _sql_str(ILK_SATIN_ALAN)
    tekrar = _sql_str(TEKRAR_SATIN_ALAN)
    atifsiz = _sql_str(ATIFSIZ)
    return f"""
    first_deal AS (
        SELECT
            d.org_id,
            d.deal_id,
            row_number() OVER (
                PARTITION BY d.org_id, d.contact_id
                ORDER BY d.created_at ASC NULLS LAST, d.deal_id ASC
            ) AS contact_rn
        FROM deals d
        WHERE d.contact_id IS NOT NULL
    ),
    classified AS (
        SELECT
            d.org_id,
            d.deal_id,
            d.contact_id,
            d.created_at,
            d.closed_at,
            d.stage,
            d.owner_rep_id AS deal_owner_rep_id,
            d.cycle_start_at,
            d.cycle_start_reliable,
            coalesce(d.lead_id, c.lead_id, tl.lead_id) AS lead_id,
            l.owner_rep_id AS lead_owner_rep_id,
            CASE
                WHEN coalesce(d.lead_id, c.lead_id, tl.lead_id) IS NULL
                    THEN {atifsiz}
                WHEN d.contact_id IS NULL THEN {ilk}
                WHEN coalesce(fd.contact_rn, 1) = 1 THEN {ilk}
                ELSE {tekrar}
            END AS kind
        FROM deals d
        LEFT JOIN contacts c
          ON c.org_id = d.org_id AND c.contact_id = d.contact_id
        LEFT JOIN first_deal fd
          ON fd.org_id = d.org_id AND fd.deal_id = d.deal_id
        LEFT JOIN LATERAL (
            SELECT l0.lead_id
            FROM leads l0
            WHERE l0.org_id = d.org_id
              AND l0.thread_id = coalesce(d.thread_id, c.thread_id)
            ORDER BY l0.assigned_at ASC NULLS LAST
            LIMIT 1
        ) tl ON true
        LEFT JOIN leads l
          ON l.org_id = d.org_id
         AND l.lead_id = coalesce(d.lead_id, c.lead_id, tl.lead_id)
        WHERE d.org_id = %s
    )
    """
