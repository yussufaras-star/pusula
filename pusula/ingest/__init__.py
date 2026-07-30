"""Veri toplama katmanı (ingest).

Dış kaynaklardan (çağrı kayıtları, e-posta, WhatsApp, toplantı notları,
Zoho aktiviteleri) olayları çeker, normalize eder ve events tablosuna
yazar. Ortak iskelet base.py'dedir; her kaynak kendi ingester
modülünde yaşar ve registry'ye kaydolur. Somut ingester modülleri
eklendikçe kayıt defterinin dolması için buradan import edilmelidir.
"""

from pusula.ingest.base import Ingester, IngestError, IngestResult, RawRecord
from pusula.ingest.registry import all_ingesters, get, register

__all__ = [
    "Ingester",
    "IngestError",
    "IngestResult",
    "RawRecord",
    "all_ingesters",
    "get",
    "register",
]
