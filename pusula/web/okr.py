"""Ekip Panosu API'si (/api/okr/*): OKR, haftalık check-in ve CFR.

Yetki: yönetici her şeyi yazar. Temsilci ekip hedeflerini ve kendi kişisel
hedeflerini görür; kişisel hedefini kendisi yazar (Doerr: hedeflerin bir
kısmı aşağıdan yukarı gelir), sorumlusu olduğu anahtar sonuca check-in
yapar, takdir ve kendi konuşma notunu ekler.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from pusula import okr
from pusula.live import data_as_of
from pusula.metrics import TZ
from pusula.okr_template import template_for
from pusula.panel_auth import AuthUser
from pusula.panel_data import load_reps
from pusula.sales_report import REPORT_START, build_sales_report
from pusula.web import cache
from pusula.web.auth import admin_user, current_user

REPORT_TTL = 5 * 60
HISTORY_TTL = 6 * 60 * 60

router = APIRouter(prefix="/api/okr")


class ObjectiveBody(BaseModel):
    year: int = Field(ge=2020, le=2100)
    quarter: int = Field(ge=0, le=4)
    owner: str = okr.TEAM
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    kind: Literal["taahhut", "iddiali"] = "taahhut"
    status: Literal["taslak", "aktif", "kapandi"] = "aktif"
    parent_id: int | None = None
    final_score: float | None = Field(default=None, ge=0, le=1)
    reflection: str | None = Field(default=None, max_length=4000)
    sort: int = 0


class KeyResultBody(BaseModel):
    objective_id: int
    title: str = Field(min_length=1, max_length=200)
    metric: str | None = None
    unit: Literal["tl", "adet", "yuzde", "sn", "gun", "sayi"] = "sayi"
    direction: Literal["up", "down"] = "up"
    start_value: float = 0
    target_value: float
    owner_rep_id: str | None = None
    sort: int = 0


class CheckinBody(BaseModel):
    kr_id: int
    value: float | None = None
    confidence: int | None = Field(default=None, ge=1, le=10)
    note: str | None = Field(default=None, max_length=2000)


class CfrBody(BaseModel):
    kind: Literal["konusma", "geri_bildirim", "takdir"]
    rep_id: str | None = None
    objective_id: int | None = None
    text: str = Field(min_length=1, max_length=2000)


class SeedBody(BaseModel):
    year: int


def _now() -> datetime:
    return datetime.now(TZ)


def _require_database() -> None:
    if not (os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")):
        raise HTTPException(status_code=503, detail="DATABASE_URL_POOLED / DATABASE_URL yapılandırılmamış")


def _scope(user: AuthUser) -> str | None:
    """None: yönetici (her şeyi görür)."""
    return None if user.role == "admin" else user.rep_id


def _can_write_owner(user: AuthUser, owner: str) -> bool:
    return user.role == "admin" or (owner != okr.TEAM and owner == user.rep_id)


def period_values(year: int, quarter: int, now: datetime) -> dict[str, dict[str, Any]]:
    """Sahip → dönemin sales_report satırı. Rapor önbelleği /api/sales-report ile ortaktır."""
    today = now.date()
    first, last = okr.period_bounds(year, quarter)
    first = max(first, REPORT_START)
    end = min(last, today)
    if end < first:
        return {}
    as_of = data_as_of()
    report = cache.cached(
        ("sales-report", first.isoformat(), end.isoformat(), today.isoformat(), as_of),
        REPORT_TTL if end >= today else HISTORY_TTL,
        lambda: jsonable_encoder(build_sales_report(first, end, now)),
    )
    out = {okr.TEAM: report["team"]}
    for row in report["reps"]:
        out[str(row["rep_id"])] = row
    return out


@router.get("")
def board(year: int | None = None, quarter: int | None = None, user: AuthUser = Depends(current_user)) -> Any:
    _require_database()
    now = _now()
    today = now.date()
    year = year or today.year
    quarter = okr.current_quarter(today) if quarter is None else quarter
    if not 2020 <= year <= 2100 or not 0 <= quarter <= 4:
        raise HTTPException(status_code=400, detail="Dönem geçersiz")
    rows = okr.load_year(year)
    rep_scope = _scope(user)
    objectives = [o for o in rows["objectives"] if okr.visible(o["owner"], rep_scope)]
    ids = {o["id"] for o in objectives}
    krs = [k for k in rows["krs"] if k["objective_id"] in ids]
    kr_ids = {k["id"] for k in krs}
    checkins = [c for c in rows["checkins"] if c["kr_id"] in kr_ids]
    reps = load_reps()
    names = {r.rep_id: r.full_name for r in reps}
    in_period = {o["id"] for o in objectives if o["quarter"] == quarter}
    needs_values = any(k.get("metric") and k["objective_id"] in in_period for k in krs)
    values = period_values(year, quarter, now) if needs_values else {}
    payload = okr.build_board(
        year=year,
        quarter=quarter,
        objectives=objectives,
        krs=krs,
        checkins=checkins,
        values=values,
        names=names,
        today=today,
    )
    payload["cfr"] = [
        {**c, "rep_name": names.get(c.get("rep_id") or "")}
        for c in rows["cfr"]
        if okr.cfr_visible(c, rep_scope)
    ][:60]
    payload["reps"] = [
        {"rep_id": r.rep_id, "name": r.full_name}
        for r in reps
        if rep_scope is None or r.rep_id == rep_scope
    ]
    payload["metrics"] = [
        {"key": key, "label": m.label, "unit": m.unit, "cumulative": m.cumulative, "direction": m.direction}
        for key, m in okr.METRICS.items()
    ]
    payload["can_edit_team"] = user.role == "admin"
    payload["template_available"] = user.role == "admin" and template_for(year) is not None and not rows["objectives"]
    payload["today"] = today
    return jsonable_encoder(payload)


# --- hedefler -----------------------------------------------------------------


def _objective_or_404(obj_id: int) -> dict[str, Any]:
    row = okr.objective_row(obj_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Hedef bulunamadı")
    return row


def _check_parent(body: ObjectiveBody, own_id: int | None = None) -> None:
    if body.parent_id is None:
        return
    if body.parent_id == own_id:
        raise HTTPException(status_code=400, detail="Hedef kendisine bağlanamaz")
    parent = okr.objective_row(body.parent_id)
    if parent is None or parent["year"] != body.year:
        raise HTTPException(status_code=400, detail="Bağlı hedef aynı yılda olmalı")


@router.post("/objectives")
def create_objective(body: ObjectiveBody, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _require_database()
    if not _can_write_owner(user, body.owner):
        raise HTTPException(status_code=403, detail="Yalnız kendi kişisel hedefini ekleyebilirsin")
    _check_parent(body)
    values = body.model_dump()
    values["created_by"] = user.email
    return {"id": okr.insert("okr_objectives", values)}


@router.patch("/objectives/{obj_id}")
def update_objective(obj_id: int, body: ObjectiveBody, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _require_database()
    row = _objective_or_404(obj_id)
    if not (_can_write_owner(user, row["owner"]) and _can_write_owner(user, body.owner)):
        raise HTTPException(status_code=403, detail="Bu hedefi düzenleme yetkin yok")
    _check_parent(body, obj_id)
    okr.update("okr_objectives", obj_id, body.model_dump())
    return {"ok": True}


@router.delete("/objectives/{obj_id}")
def delete_objective(obj_id: int, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _require_database()
    row = _objective_or_404(obj_id)
    if not _can_write_owner(user, row["owner"]):
        raise HTTPException(status_code=403, detail="Bu hedefi silme yetkin yok")
    okr.delete("okr_objectives", obj_id)
    return {"ok": True}


# --- anahtar sonuçlar -------------------------------------------------------------


def _validate_kr(body: KeyResultBody) -> dict[str, Any]:
    values = body.model_dump()
    if body.metric:
        metric = okr.METRICS.get(body.metric)
        if metric is None:
            raise HTTPException(status_code=400, detail="Bilinmeyen metrik")
        values["unit"] = metric.unit
        values["direction"] = metric.direction
    if body.target_value == body.start_value:
        raise HTTPException(status_code=400, detail="Hedef değer başlangıçtan farklı olmalı")
    return values


@router.post("/key-results")
def create_kr(body: KeyResultBody, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _require_database()
    obj = _objective_or_404(body.objective_id)
    if not _can_write_owner(user, obj["owner"]):
        raise HTTPException(status_code=403, detail="Bu hedefe anahtar sonuç ekleme yetkin yok")
    return {"id": okr.insert("okr_key_results", _validate_kr(body))}


def _kr_or_404(kr_id: int) -> dict[str, Any]:
    row = okr.kr_row(kr_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Anahtar sonuç bulunamadı")
    return row


@router.patch("/key-results/{kr_id}")
def update_kr(kr_id: int, body: KeyResultBody, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _require_database()
    row = _kr_or_404(kr_id)
    if body.objective_id != row["objective_id"]:
        raise HTTPException(status_code=400, detail="Anahtar sonuç başka hedefe taşınamaz")
    if not _can_write_owner(user, row["owner"]):
        raise HTTPException(status_code=403, detail="Bu anahtar sonucu düzenleme yetkin yok")
    values = _validate_kr(body)
    values.pop("objective_id")
    okr.update("okr_key_results", kr_id, values)
    return {"ok": True}


@router.delete("/key-results/{kr_id}")
def delete_kr(kr_id: int, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _require_database()
    row = _kr_or_404(kr_id)
    if not _can_write_owner(user, row["owner"]):
        raise HTTPException(status_code=403, detail="Bu anahtar sonucu silme yetkin yok")
    okr.delete("okr_key_results", kr_id)
    return {"ok": True}


# --- check-in ve CFR ---------------------------------------------------------------


@router.post("/checkins")
def checkin(body: CheckinBody, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    """Haftalık check-in: elle ölçülen sonuçta değer, her sonuçta güven ve not."""
    _require_database()
    row = _kr_or_404(body.kr_id)
    allowed = (
        user.role == "admin"
        or row["owner"] == user.rep_id
        or (row.get("owner_rep_id") is not None and row["owner_rep_id"] == user.rep_id)
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Bu anahtar sonucun sorumlusu değilsin")
    if body.value is None and body.confidence is None and not (body.note or "").strip():
        raise HTTPException(status_code=400, detail="Değer, güven ya da not gir")
    values = body.model_dump()
    values["note"] = (body.note or "").strip() or None
    values["author"] = user.full_name or user.email
    return {"id": okr.insert("okr_checkins", values)}


@router.post("/cfr")
def add_cfr(body: CfrBody, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _require_database()
    if user.role != "admin":
        if body.kind == "geri_bildirim":
            raise HTTPException(status_code=403, detail="Geri bildirimi yönetici yazar")
        if body.kind == "konusma" and body.rep_id != user.rep_id:
            raise HTTPException(status_code=403, detail="Konuşma notu yalnız kendin için")
    values = body.model_dump()
    values["text"] = body.text.strip()
    values["author"] = user.full_name or user.email
    return {"id": okr.insert("okr_cfr", values)}


@router.delete("/cfr/{row_id}")
def delete_cfr(row_id: int, user: AuthUser = Depends(admin_user)) -> dict[str, Any]:
    _require_database()
    okr.delete("okr_cfr", row_id)
    return {"ok": True}


@router.post("/seed")
def seed(body: SeedBody, user: AuthUser = Depends(admin_user)) -> dict[str, Any]:
    """Önerilen taslağı yükler; yılda hedef varsa hiçbir şey yapmaz."""
    _require_database()
    template = template_for(body.year)
    if template is None:
        raise HTTPException(status_code=404, detail="Bu yıl için taslak yok")
    return {"added": okr.seed_year(body.year, template, by=user.email)}
