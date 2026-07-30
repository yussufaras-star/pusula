"""Pusula yapılandırması.

Ortam değişkenlerinden okunan ayarların tek giriş noktası.

ORG_ID: hangi organizasyonun verisi üzerinde çalışıldığı. Çok
kiracılılık bugün kurulmuyor; değerin tek yerden okunması, ileride
başka satış operasyonlarının aynı kod tabanıyla çalışabilmesi için.
"""

import os

_DEFAULT_ORG_ID = "rexven"


def get_org_id() -> str:
    """Aktif organizasyon kimliği. ORG_ID tanımlı değilse 'rexven'."""
    return os.environ.get("ORG_ID", _DEFAULT_ORG_ID)
