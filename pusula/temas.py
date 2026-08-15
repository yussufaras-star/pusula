"""Çağrı süresi ve temas (temas) SQL parçaları — tek kaynak.

Temas: outbound call, scheduled değil, süre >= 30 sn,
call_outcomes.category <> 'not_reached'.

Deneme / çağrı sayımı: scheduled değil; süre < 10 sn hesaba katılmaz
(süre yoksa satır da katılmaz).

send_nudges.py ve weekly_report.py buradan okur.
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

TEMAS_MIN_SEC = 30
CALL_MIN_SEC = 10  # bunun altı hiçbir metrikte sayılmaz


def duration_sec(alias: str = "e") -> str:
    return DURATION_SEC.format(alias=alias)


def outcome_join(alias: str = "e") -> str:
    return OUTCOME_JOIN.format(alias=alias)


def is_temas_sql(alias: str = "e") -> str:
    """WHERE içinde kullanılacak temas koşulu (co join gerekir)."""
    dur = duration_sec(alias)
    return f"""
        coalesce({alias}.meta->>'scheduled', 'false') <> 'true'
        AND {dur} >= {TEMAS_MIN_SEC}
        AND coalesce(co.category, '') <> 'not_reached'
    """


def is_countable_call_sql(alias: str = "e") -> str:
    """Süre >= 10 ve scheduled değil (temas zorunlu değil)."""
    dur = duration_sec(alias)
    return f"""
        coalesce({alias}.meta->>'scheduled', 'false') <> 'true'
        AND {dur} >= {CALL_MIN_SEC}
    """
