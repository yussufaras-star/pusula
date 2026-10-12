"""Vercel Python fonksiyonu. /api/* istekleri vercel.json ile buraya yönlenir."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pusula.web.app import app  # noqa: E402

__all__ = ["app"]
