"""Çağrı süresi ve temas SQL parçaları — tek kaynak.

Süre eşiği (10 sn) yalnız iki metrikte kullanılır:
  kayıt disiplini — uzun görüşmede sonuç girilme
  görüşme süresi  — 10 sn+ outbound sayısı

Diğer metriklerde temas: scheduled değil ve
call_outcomes.category <> 'not_reached' (süre yok).
Deneme: scheduled değil outbound (süre yok).

Lead ilerleme kovası pusula.lead_reach'tedir.
"""

from __future__ import annotations

# Süre: call_duration_sec yoksa duration_sec (eski anahtar).
# {alias} = events satır alias'ı (örn. e, e3).
DURATION_SEC = """
    COALESCE(
        NULLIF({alias}.meta->>'call_duration_sec', '')::numeric,
        NULLIF({alias}.meta->>'duration_sec', '')::numeric
    )
"""

# call_outcomes join: outcome_key varsa onu, yoksa call_result → raw_value.
OUTCOME_JOIN = """
          LEFT JOIN call_outcomes co
            ON co.org_id = {alias}.org_id
           AND (
                (
                    nullif({alias}.meta->>'outcome_key', '') IS NOT NULL
                    AND co.outcome_key = {alias}.meta->>'outcome_key'
                )
                OR (
                    nullif({alias}.meta->>'outcome_key', '') IS NULL
                    AND nullif({alias}.meta->>'call_result', '') IS NOT NULL
                    AND co.raw_value = {alias}.meta->>'call_result'
                )
           )
"""

TEMAS_MIN_SEC = 10  # yalnız kayıt disiplini ve görüşme süresi


def duration_sec(alias: str = "e") -> str:
    return DURATION_SEC.format(alias=alias)


def outcome_join(alias: str = "e") -> str:
    return OUTCOME_JOIN.format(alias=alias)


def is_temas_sql(alias: str = "e") -> str:
    """Ulaşılmış çağrı (süre yok). co join gerekir.

    category NULL temas değildir.
    """
    return f"""
        coalesce({alias}.meta->>'scheduled', 'false') <> 'true'
        AND co.category IS NOT NULL
        AND co.category <> 'not_reached'
    """


def is_attempt_sql(alias: str = "e") -> str:
    """Scheduled olmayan çağrı (süre yok)."""
    return f"""
        coalesce({alias}.meta->>'scheduled', 'false') <> 'true'
    """


def is_countable_call_sql(alias: str = "e") -> str:
    """Deneme ile aynı."""
    return is_attempt_sql(alias)
