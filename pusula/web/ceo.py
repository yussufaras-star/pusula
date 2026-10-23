"""CEO özeti API'si (/api/ceo-summary). Özeti sabah işi hesaplayıp kaydeder (~1 dk);
burada yalnız kaydedilenler okunur."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder

from pusula import ceo_card, ceo_summary, finance_card, finance_summary
from pusula.panel_auth import AuthUser
from pusula.web import cache
from pusula.web.auth import admin_user, finance_user

router = APIRouter()


@router.get("/api/ceo-summary")
def recent(user: AuthUser = Depends(admin_user)) -> Any:
    def build() -> Any:
        rows = ceo_summary.load_recent()
        # Panel, Cliq'e giden kartın aynısını gösterir.
        for row in rows:
            row["html"] = ceo_card.render_html(row["payload"])
        return jsonable_encoder({"summaries": rows})

    return cache.cached(("ceo-summary",), 5 * 60, build)


@router.get("/api/finance-summary")
def finance_recent(user: AuthUser = Depends(finance_user)) -> Any:
    def build() -> Any:
        rows = ceo_summary.load_recent(table=finance_summary.TABLE)
        for row in rows:
            row["html"] = finance_card.render_html(row["payload"])
        return jsonable_encoder({"summaries": rows})

    return cache.cached(("finance-summary",), 5 * 60, build)
