"""Temas ve çevirme — tek kaynak.

temas_mi(event) / is_temas_sql: görüşme açıldı mı.
  call_result dolu → no_answer ve invalid_number hariç temas.
  call_result boş → duration_sec > 0 ise temas.
  overdue / scheduled faaliyet ve temasa girmez.

cevirme_mi / is_cevirme_sql: call_status = connected (temas olmasa da).
Temsilcinin çevirmesi faaliyet, açılması sonuç.

Lead ilerleme kovası pusula.lead_reach'tedir.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pusula.db.models import Event

# meta.call_result veya outcome_key; ham Zoho picklist eşleri.
NOT_TEMAS_KEYS = frozenset({"no_answer", "invalid_number"})
NOT_TEMAS_RAW = frozenset({"Yanıt yok/Meşgul"})
PLANNED_STATUSES = frozenset({"overdue", "scheduled"})

# Süre eşiğinden (10 sn) bu tanıma çevrilen dosya/yer sayısı.
# weekly_report TEMAS_MIN_SEC: 13, send_nudges süre filtresi: 3,
# update_lead_status has_temas: 1, is_temas_sql gövdesi: 1.
TEMAS_MIGRATION_SITES = 18

# Süre: call_duration_sec yoksa duration_sec (eski anahtar).
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


def duration_sec(alias: str = "e") -> str:
    return DURATION_SEC.format(alias=alias)


def outcome_join(alias: str = "e") -> str:
    return OUTCOME_JOIN.format(alias=alias)


def _meta_of(event: Event | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(event, Event):
        return event.meta or {}
    meta = event.get("meta") if isinstance(event, Mapping) else None
    if isinstance(meta, Mapping):
        return meta
    if isinstance(event, Mapping):
        return event
    return {}


def _duration_value(meta: Mapping[str, Any]) -> float:
    for key in ("call_duration_sec", "duration_sec"):
        raw = meta.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def _result_token(meta: Mapping[str, Any]) -> str | None:
    """call_result öncelikli; yoksa outcome_key."""
    for key in ("call_result", "outcome_key"):
        raw = meta.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None


def is_planned_call(event: Event | Mapping[str, Any]) -> bool:
    """Planlanmış iş: overdue / scheduled. Yapılmış çağrı değil."""
    meta = _meta_of(event)
    status = str(meta.get("call_status") or "").strip()
    if status in PLANNED_STATUSES:
        return True
    scheduled = meta.get("scheduled")
    if scheduled is True:
        return True
    if isinstance(scheduled, str) and scheduled.strip().lower() == "true":
        return True
    return False


def temas_mi(event: Event | Mapping[str, Any]) -> bool:
    """Görüşme açıldı mı. Planlanmış kayıt temas değildir."""
    if is_planned_call(event):
        return False
    meta = _meta_of(event)
    token = _result_token(meta)
    if token is not None:
        return token not in NOT_TEMAS_KEYS and token not in NOT_TEMAS_RAW
    return _duration_value(meta) > 0


def cevirme_mi(event: Event | Mapping[str, Any]) -> bool:
    """Faaliyet: bağlı çağrı. Planlanmış kayıt çevirme değildir."""
    if is_planned_call(event):
        return False
    meta = _meta_of(event)
    return str(meta.get("call_status") or "").strip() == "connected"


def is_planned_sql(alias: str = "e") -> str:
    """overdue / scheduled — faaliyet ve temas dışı."""
    return f"""
        (
            coalesce({alias}.meta->>'call_status', '') IN ('overdue', 'scheduled')
            OR coalesce({alias}.meta->>'scheduled', 'false') = 'true'
        )
    """


def is_not_planned_sql(alias: str = "e") -> str:
    return f"NOT ({is_planned_sql(alias)})"


def is_temas_sql(alias: str = "e") -> str:
    """SQL karşılığı temas_mi. outcome join gerekmez."""
    dur = duration_sec(alias)
    result = f"nullif(btrim(coalesce({alias}.meta->>'call_result', '')), '')"
    key = f"nullif(btrim(coalesce({alias}.meta->>'outcome_key', '')), '')"
    token = f"coalesce({result}, {key})"
    not_keys = ", ".join(f"'{k}'" for k in sorted(NOT_TEMAS_KEYS | NOT_TEMAS_RAW))
    return f"""
        {is_not_planned_sql(alias)}
        AND (
            CASE
                WHEN {token} IS NOT NULL
                    THEN {token} NOT IN ({not_keys})
                ELSE coalesce({dur}, 0) > 0
            END
        )
    """


def is_cevirme_sql(alias: str = "e") -> str:
    """SQL karşılığı cevirme_mi."""
    return f"""
        {is_not_planned_sql(alias)}
        AND {alias}.meta->>'call_status' = 'connected'
    """


def is_attempt_sql(alias: str = "e") -> str:
    """Faaliyet paydası: planlanmış olmayan outbound çağrı adayı."""
    return is_not_planned_sql(alias)


def is_countable_call_sql(alias: str = "e") -> str:
    """Deneme ile aynı (planlanmış hariç)."""
    return is_attempt_sql(alias)
