"""Test harness: run pure-logic tests where psycopg's native DLL cannot load.

Windows Smart App Control blocks the psycopg-binary DLL on some dev machines.
When that happens we register stub modules so imports succeed; tests that
really need a database are skipped via the ``db`` marker. CI imports the real
driver and this file is a no-op there.
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

try:
    import psycopg  # noqa: F401

    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False
    for name in (
        "psycopg",
        "psycopg.rows",
        "psycopg.types",
        "psycopg.types.json",
        "psycopg.errors",
        "psycopg_pool",
    ):
        module = types.ModuleType(name)
        module.__getattr__ = lambda attr: mock.MagicMock(name=attr)  # type: ignore[method-assign]
        sys.modules[name] = module


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: test needs a working psycopg driver")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if PSYCOPG_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="psycopg native driver blocked on this machine")
    for item in items:
        if "db" in item.keywords or "db" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)
