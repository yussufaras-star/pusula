"""CEO özeti API'si (/api/ceo-summary). Özeti sabah işi hesaplayıp kaydeder (~1 dk);
burada yalnız kaydedilenler okunur."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder

from pusula import ceo_card, ceo_summary
from pusula.panel_auth import AuthUser
from pusula.web import cache
from pusula.web.auth import admin_user

router = APIRouter(prefix="/api/ceo-summary")


@router.get("")
def recent(user: AuthUser = Depends(admin_user)) -> Any:
    def build() -> Any:
        rows = ceo_summary.load_recent()
        # Panel, Cliq'e giden kartın aynısını gösterir.
        for row in rows:
            row["html"] = ceo_card.render_html(row["payload"])
        return jsonable_encoder({"summaries": rows})

    return cache.cached(("ceo-summary",), 5 * 60, build)
