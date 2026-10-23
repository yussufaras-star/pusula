"""Görüşme Analizleri API'si (/api/conversations/*).

Yönetici tüm ekibi ve ekip yorumunu görür. Temsilci yalnız kendi
görüşmelerini, kendi haftalık yorumunu ve ekip sayılarını görür.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from pusula import conversations
from pusula.block_share import post_cliq
from pusula.db.identity import normalize_phone
from pusula.metrics import TZ
from pusula.panel_auth import AuthUser
from pusula.web import cache
from pusula.web.auth import admin_user, current_user
from pusula.web.reports import selected_rep

TTL = 2 * 60

router = APIRouter(prefix="/api/conversations")


def _week(raw: str | None) -> date:
    today = datetime.now(TZ).date()
    if not raw:
        return conversations.week_start_of(today)
    try:
        chosen = date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Hafta YYYY-AA-GG olmalı") from exc
    if chosen > today:
        raise HTTPException(status_code=400, detail="Gelecek hafta seçilemez")
    return conversations.week_start_of(chosen)


@router.get("/weeks")
def weeks(user: AuthUser = Depends(current_user)) -> Any:
    return cache.cached(
        ("conv-weeks",),
        TTL,
        lambda: jsonable_encoder(
            {"weeks": conversations.load_weeks(), "setup": conversations.setup_status()}
        ),
    )


@router.get("")
def week(week: str | None = None, rep: str | None = None, user: AuthUser = Depends(current_user)) -> Any:
    start = _week(week)
    rep_id = selected_rep(user, rep)
    # Temsilci için rep_id hep dolu: load_week ekip yorumunu çıkarır.
    data = cache.cached(
        ("conv-week", start.isoformat(), rep_id),
        TTL,
        lambda: jsonable_encoder(conversations.load_week(start, rep_id)),
    )
    # Yapay zekâ maliyeti yalnız yöneticiye.
    return data if user.role == "admin" else {k: v for k, v in data.items() if k != "maliyet"}


class ShareBody(BaseModel):
    week: str
    rep_id: str


@router.post("/share")
def share(body: ShareBody, user: AuthUser = Depends(admin_user)) -> Any:
    """Temsilcinin haftalık değerlendirmesini kendisine Cliq'ten gönderir. Buton = onay."""
    start = _week(body.week)
    row = conversations.load_share(start, body.rep_id)
    if row is None or not row["narrative"]:
        raise HTTPException(status_code=404, detail="Bu hafta için temsilci değerlendirmesi yok")
    if not row["email"]:
        raise HTTPException(status_code=400, detail="Temsilcinin Cliq e-postası yok")
    text = conversations.share_text(row["name"], start, row["stats"] or {}, row["narrative"])
    try:
        post_cliq(text, row["email"])
    except Exception as exc:  # noqa: BLE001 — Cliq hatası kullanıcıya dönsün
        raise HTTPException(status_code=502, detail=f"Cliq'e gönderilemedi: {exc}") from exc
    conversations.mark_sent(start, body.rep_id, user.email)
    cache.clear()
    return {"ok": True}


class RequestBody(BaseModel):
    phone: str
    days: int = 90


@router.get("/requests")
def requests(user: AuthUser = Depends(admin_user)) -> Any:
    return jsonable_encoder({"requests": conversations.load_requests()})


@router.post("/requests")
def create_request(body: RequestBody, user: AuthUser = Depends(admin_user)) -> Any:
    """Telefon numarasının kayıtlı görüşmelerini özel analize alır. Analiz gece işinde
    (talep edilenler önce) ya da gece işi elle tetiklenince yapılır."""
    phone = normalize_phone(body.phone)
    if phone is None:
        raise HTTPException(status_code=400, detail="Telefon numarası geçersiz")
    if body.days not in (30, 90, 180, 365):
        raise HTTPException(status_code=400, detail="Süre 30, 90, 180 ya da 365 gün olmalı")
    result = conversations.create_request(phone, body.days, user.email)
    if not result["kayitli"]:
        raise HTTPException(status_code=404, detail=f"{phone} ile son {body.days} günde kayıtlı görüşme yok")
    cache.clear()
    return {**result, "phone": phone}


@router.get("/item/{item_id}")
def item(item_id: int, user: AuthUser = Depends(current_user)) -> Any:
    row = conversations.load_item(item_id)
    if row is None or (user.role != "admin" and row.get("rep_id") != user.rep_id):
        raise HTTPException(status_code=404, detail="Görüşme bulunamadı")
    return jsonable_encoder(row)
