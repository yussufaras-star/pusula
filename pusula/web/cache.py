"""Süreç içi TTL önbelleği. Serverless örnek başına; doğruluk için değil hız için."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")

_LIMIT = 256
_STORE: dict[Hashable, tuple[float, Any]] = {}
_LOCK = threading.RLock()


def get(key: Hashable) -> Any | None:
    now = time.monotonic()
    with _LOCK:
        hit = _STORE.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if expires_at <= now:
            del _STORE[key]
            return None
        return value


def put(key: Hashable, value: Any, ttl: float) -> None:
    now = time.monotonic()
    with _LOCK:
        for stale in [k for k, (exp, _) in _STORE.items() if exp <= now]:
            del _STORE[stale]
        while len(_STORE) >= _LIMIT:
            del _STORE[min(_STORE, key=lambda k: _STORE[k][0])]
        _STORE[key] = (now + ttl, value)


def cached(key: Hashable, ttl: float, build: Callable[[], T]) -> T:
    hit = get(key)
    if hit is not None:
        return hit
    value = build()
    put(key, value, ttl)
    return value


def clear() -> None:
    with _LOCK:
        _STORE.clear()
