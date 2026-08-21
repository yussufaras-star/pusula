"""Lead ilerleme kovası — Zoho Lead_Status tek kaynak.

Ölçüm: lead ilerledi mi. Kanal (arama, WhatsApp, mail) status'ta birleşir.

  DOKUNULMAMIS — henüz işlenmemiş
  DENENDI     — arandı, ulaşılamadı
  ULASILDI    — diğer tüm dolu statüler

Ham status string'leri burada durur; başka dosyaya kopyalanmaz.
"""

from __future__ import annotations

from typing import Literal

ReachBucket = Literal["dokunulmamis", "denendi", "ulasildi"]

DOKUNULMAMIS: ReachBucket = "dokunulmamis"
DENENDI: ReachBucket = "denendi"
ULASILDI: ReachBucket = "ulasildi"

# Zoho Lead_Status display değerleri.
_DOKUNULMAMIS_EXACT: tuple[str, ...] = ("Yeni Müşteri Adayı",)
_DENENDI_EXACT: tuple[str, ...] = ("Aging",)
_DENENDI_LIKE: str = "%Arama-Ulaşılamadı"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def reach_bucket(status: str | None) -> ReachBucket | None:
    """Python tarafı; status boşsa None (üç kovaya girmez)."""
    if status is None:
        return None
    text = status.strip()
    if not text:
        return None
    if text in _DOKUNULMAMIS_EXACT:
        return DOKUNULMAMIS
    if text in _DENENDI_EXACT or "Arama-Ulaşılamadı" in text:
        return DENENDI
    return ULASILDI


def reach_bucket_sql(status_expr: str = "l.status") -> str:
    """CASE ifadesi; JOIN gerekmez."""
    dokun = ", ".join(_sql_str(s) for s in _DOKUNULMAMIS_EXACT)
    denendi_eq = ", ".join(_sql_str(s) for s in _DENENDI_EXACT)
    like = _sql_str(_DENENDI_LIKE).replace("%", "%%")
    return f"""
    CASE
      WHEN {status_expr} IN ({dokun}) THEN '{DOKUNULMAMIS}'
      WHEN {status_expr} IN ({denendi_eq})
        OR {status_expr} LIKE {like} THEN '{DENENDI}'
      WHEN {status_expr} IS NOT NULL AND btrim({status_expr}) <> ''
        THEN '{ULASILDI}'
      ELSE NULL
    END
    """


def is_dokunulmamis_sql(status_expr: str = "l.status") -> str:
    dokun = ", ".join(_sql_str(s) for s in _DOKUNULMAMIS_EXACT)
    return f"{status_expr} IN ({dokun})"
