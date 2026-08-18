"""Sıfır satış: contact başına ilk deal.

SIFIR SATIS — bir contact'ın en eski (created_at) deal'i.
Contact'ı olmayan, doğrudan lead'e bağlı deal'lar da sıfır satış.
TEKRAR SATIS — aynı contact'ın sonraki deal'ları.
ATIFSIZ — lead çözülemeyen; temsilci atfı yok.

Temsilci atfı: thread / lead üzerinden leads.owner_rep_id.
deals.owner_rep_id performans için kullanılmaz.

Contact yaşı eşiği yok; sıra yalnızca created_at (eşitlikte deal_id).
"""

from __future__ import annotations

from typing import Literal

SaleKind = Literal["sifir", "tekrar", "atifsiz"]

SIFIR: SaleKind = "sifir"
TEKRAR: SaleKind = "tekrar"
ATIFSIZ: SaleKind = "atifsiz"

# Zoho Deals Stage display (kazanılan).
WON_STAGE = "Kapandı Kazanıldı"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def won_stage_sql(alias: str = "cl") -> str:
    return f"{alias}.stage = {_sql_str(WON_STAGE)}"


def classified_deals_cte() -> str:
    """first_deal + classified. classified.org_id = %s (bir placeholder).

    classified.kind: sifir | tekrar | atifsiz
    classified.lead_owner_rep_id: leads.owner_rep_id (deal owner değil).
    """
    sifir = _sql_str(SIFIR)
    tekrar = _sql_str(TEKRAR)
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
                WHEN d.contact_id IS NULL THEN {sifir}
                WHEN coalesce(fd.contact_rn, 1) = 1 THEN {sifir}
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
