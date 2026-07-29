"""Zoho entegrasyonu.

OAuth token yönetimi (auth) ve CRM okuma istemcisi (crm) burada yaşar.
Cliq istemcisi sonraki görevlerde eklenecek.
"""

from pusula.zoho.auth import ZohoAuthError, get_access_token
from pusula.zoho.crm import ZohoCrmError, bulk_read, coql, get_records, get_related

__all__ = [
    "ZohoAuthError",
    "ZohoCrmError",
    "bulk_read",
    "coql",
    "get_access_token",
    "get_records",
    "get_related",
]
