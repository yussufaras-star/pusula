"""İsimle ingester bulmayı sağlayan basit kayıt defteri.

Somut ingester modülleri sınıflarını register ile kaydeder (decorator
olarak da kullanılabilir); scripts/run_ingest.py get ve all_ingesters
üzerinden bulur. Kayıt defteri süreç içidir, import anında dolar.
"""

from pusula.ingest.base import Ingester

_registry: dict[str, type[Ingester]] = {}


def register(ingester_cls: type[Ingester]) -> type[Ingester]:
    """Ingester sınıfını source_name ile kaydeder.

    Aynı isim ikinci kez kaydedilemez; bu genellikle yanlışlıkla
    kopyalanmış bir source_name'e işaret eder.
    """
    source_name = getattr(ingester_cls, "source_name", None)
    if not isinstance(source_name, str) or not source_name:
        raise ValueError(f"{ingester_cls.__name__}: source_name tanımlı değil")
    if source_name in _registry:
        raise ValueError(f"source_name zaten kayıtlı: {source_name}")
    _registry[source_name] = ingester_cls
    return ingester_cls


def get(source_name: str) -> type[Ingester]:
    """İsme karşılık gelen ingester sınıfını döner; yoksa KeyError."""
    if source_name not in _registry:
        raise KeyError(
            f"kayıtlı ingester yok: {source_name!r} (kayıtlılar: {sorted(_registry)})"
        )
    return _registry[source_name]


def all_ingesters() -> list[type[Ingester]]:
    """Kayıtlı tüm ingester sınıfları, source_name sırasıyla."""
    return [_registry[name] for name in sorted(_registry)]
