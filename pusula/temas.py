"""Çağrı süresi ve temas (temas) SQL parçaları — tek kaynak.

Temas: outbound call, scheduled değil, süre >= 10 sn,
call_outcomes.category <> 'not_reached'.

Deneme: scheduled değil, süre > 0.
Bağlanmadı: scheduled değil, süre 0 veya süre alanı yok.
Bunlar temas oranının paydasına girmez.

send_nudges.py, weekly_report.py ve take_snapshot.py buradan okur.
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

TEMAS_MIN_SEC = 10
TEMAS_MIN_SEC_OLD = 30  # etki karşılaştırması
CALL_MIN_SEC = 10  # temas süresi; 1-9 sn deneme ama temas değil


def duration_sec(alias: str = "e") -> str:
    return DURATION_SEC.format(alias=alias)


def outcome_join(alias: str = "e") -> str:
    return OUTCOME_JOIN.format(alias=alias)


def is_temas_sql(alias: str = "e", min_sec: int | None = None) -> str:
    """WHERE içinde kullanılacak temas koşulu (co join gerekir).

    category NULL temas değildir (SQL: NULL <> 'not_reached' bilinmiyor).
    """
    sec = TEMAS_MIN_SEC if min_sec is None else min_sec
    dur = duration_sec(alias)
    return f"""
        coalesce({alias}.meta->>'scheduled', 'false') <> 'true'
        AND {dur} >= {sec}
        AND co.category IS NOT NULL
        AND co.category <> 'not_reached'
    """


def is_attempt_sql(alias: str = "e") -> str:
    """Süre > 0 ve scheduled değil (bağlanmadı hariç deneme)."""
    dur = duration_sec(alias)
    return f"""
        coalesce({alias}.meta->>'scheduled', 'false') <> 'true'
        AND {dur} > 0
    """


def is_baglanmadi_sql(alias: str = "e") -> str:
    """Süre 0 veya yok; scheduled değil."""
    dur = duration_sec(alias)
    return f"""
        coalesce({alias}.meta->>'scheduled', 'false') <> 'true'
        AND ({dur} IS NULL OR {dur} = 0)
    """


def is_countable_call_sql(alias: str = "e") -> str:
    """Deneme ile aynı: süre > 0, scheduled değil."""
    return is_attempt_sql(alias)
