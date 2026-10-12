"""Pusula API. React arayüzü public/ altından statik servis edilir."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pusula.compare import (
    AVG,
    MAX_SPAN_DAYS,
    TEAM,
    Side,
    load_side,
    series_group,
    side_cut,
)
from pusula.config import get_org_id
from pusula.hourly_nudges import run_hourly_nudges
from pusula.live import data_as_of, history_by_rep_hour, load_live
from pusula.live_ingest import run_live_ingest
from pusula.metrics import TZ
from pusula.panel_auth import (
    ADMIN_EMAIL,
    AuthUser,
    lookup_secret_password,
    normalize_login_email,
    password_matches,
    resolve_user,
)
from pusula.panel_data import DateWindow, load_rep_by_email, load_reps
from pusula.panel_profile import PROFILE_WARNING, performance_profiles
from pusula.web import cache
from pusula.web.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    admin_user,
    current_user,
    encode_session,
    passwords,
    require_cron,
)
from pusula.web.reports import build_reports, selected_rep

load_dotenv()

logger = logging.getLogger(__name__)
LIVE_TTL = 60
HISTORY_TTL = 6 * 60 * 60
REPORT_TTL = 5 * 60
MAX_LOOKBACK_DAYS = 400
MANUAL_INGEST_MIN_GAP = 60
PUBLIC_DIR = Path(__file__).resolve().parents[2] / "public"

app = FastAPI(title="Pusula", docs_url=None, redoc_url=None)


class LoginBody(BaseModel):
    email: str
    password: str


def _now() -> datetime:
    return datetime.now(TZ)


def require_database() -> None:
    if not (os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")):
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL_POOLED / DATABASE_URL yapılandırılmamış",
        )


def parse_day(raw: str | None, today: date) -> date:
    if not raw:
        return today
    try:
        chosen = date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Tarih YYYY-AA-GG olmalı") from exc
    if chosen > today or chosen < today - timedelta(days=MAX_LOOKBACK_DAYS):
        raise HTTPException(status_code=400, detail="Tarih aralık dışında")
    return chosen


def scope_live(payload: dict[str, Any], user: AuthUser) -> dict[str, Any]:
    """Temsilci yalnız kendi kartını ve ekip toplamını görür."""
    if user.role == "admin":
        return payload
    own = [card for card in payload["reps"] if card["rep_id"] == user.rep_id]
    return {**payload, "reps": own}


# --- oturum ---------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    required = (
        "DATABASE_URL_POOLED",
        "PUSULA_PASSWORDS_JSON",
        "PUSULA_SESSION_SECRET",
        "CRON_SECRET",
        "CLIQ_WEBHOOK_URL",
    )
    missing = [key for key in required if not os.environ.get(key)]
    return {"ok": not missing, "missing_configuration": missing}


@app.post("/api/login")
def login(body: LoginBody, response: Response) -> dict[str, str]:
    secrets = passwords()
    email = normalize_login_email(body.email)
    expected = lookup_secret_password(secrets, email)
    if expected is None or not password_matches(body.password, expected):
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı")
    rep = None
    if email != ADMIN_EMAIL:
        require_database()
        rep = load_rep_by_email(email)
    user = resolve_user(email, body.password, secrets, rep)
    if user is None:
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı")
    response.set_cookie(
        COOKIE_NAME,
        encode_session(user),
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=bool(os.environ.get("VERCEL")),
        samesite="strict",
        path="/",
    )
    return {"email": user.email, "role": user.role}


@app.post("/api/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return {"ok": "true"}


@app.get("/api/me")
def me(user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    return {
        "email": user.email,
        "role": user.role,
        "rep_id": user.rep_id,
        "full_name": user.full_name,
        "org_id": get_org_id(),
    }


@app.get("/api/reps")
def representatives(user: AuthUser = Depends(admin_user)) -> Any:
    require_database()
    return jsonable_encoder(load_reps())


# --- canlı kokpit -----------------------------------------------------------


@app.get("/api/live")
def live(day: str | None = None, user: AuthUser = Depends(current_user)) -> Any:
    require_database()
    now = _now()
    chosen = parse_day(day, now.date())
    history = cache.cached(
        ("history", chosen.isoformat()),
        HISTORY_TTL,
        lambda: history_by_rep_hour(chosen, now),
    )
    is_today = chosen == now.date()
    # Bugün için anahtar veri damgasını içerir: başka örnekte yapılan
    # güncelleme de önbelleği anında geçersiz kılar.
    as_of = data_as_of() if is_today else None
    payload = cache.cached(
        ("live", chosen.isoformat(), as_of.isoformat() if as_of else None),
        LIVE_TTL if is_today else HISTORY_TTL,
        lambda: jsonable_encoder(load_live(chosen, now, history=history)),
    )
    return scope_live(payload, user)


# --- karşılaştırma ----------------------------------------------------------

SUBJECT_LABELS = {TEAM: "Ekip toplamı", AVG: "Temsilci ortalaması"}


def compare_subject(raw: str, user: AuthUser, names: dict[str, str]) -> str:
    """Temsilci yalnız kendisini, ekip toplamını ve ortalamayı seçebilir."""
    if raw in SUBJECT_LABELS:
        return raw
    if user.role == "rep" and raw != user.rep_id:
        raise HTTPException(status_code=403, detail="Yalnız kendi verinizi kıyaslayabilirsiniz")
    if raw not in names:
        raise HTTPException(status_code=404, detail="Temsilci bulunamadı")
    return raw


def compare_side(first: str, last: str, subject: str, today: date) -> Side:
    start = parse_day(first, today)
    end = parse_day(last, today)
    if end < start:
        raise HTTPException(status_code=400, detail="Bitiş başlangıçtan önce olamaz")
    if (end - start).days >= MAX_SPAN_DAYS:
        raise HTTPException(status_code=400, detail="Dönem en fazla bir yıl olabilir")
    return Side(start, end, subject)


@app.get("/api/compare")
def compare(
    a_from: str,
    a_to: str,
    b_from: str,
    b_to: str,
    a_subject: str = TEAM,
    b_subject: str = TEAM,
    align: bool = True,
    user: AuthUser = Depends(current_user),
) -> Any:
    require_database()
    now = _now()
    today = now.date()
    reps = load_reps()
    names = {rep.rep_id: rep.full_name for rep in reps}
    sides = (
        compare_side(a_from, a_to, compare_subject(a_subject, user, names), today),
        compare_side(b_from, b_to, compare_subject(b_subject, user, names), today),
    )
    own_cuts = [side_cut(side, now) for side in sides]
    cuts = (
        [side_cut(sides[0], now, other=sides[1]), side_cut(sides[1], now, other=sides[0])]
        if align
        else own_cuts
    )
    touches_today = any(side.last >= today for side in sides)
    group = series_group(*sides)

    def build() -> Any:
        payload = [
            load_side(
                side,
                cut,
                rep_ids=list(names),
                label=SUBJECT_LABELS.get(side.subject) or names[side.subject],
                group=group,
                aligned=cut < own,
            )
            for side, cut, own in zip(sides, cuts, own_cuts)
        ]
        return jsonable_encoder({"now": now, "a": payload[0], "b": payload[1]})

    key = ("compare", *sides, align, today.isoformat())
    return cache.cached(key, LIVE_TTL if touches_today else HISTORY_TTL, build)


# --- raporlar (eski panel analizleri) ----------------------------------------


@app.get("/api/reports")
def reports(
    days: int = 90,
    rep_id: str | None = None,
    user: AuthUser = Depends(current_user),
) -> Any:
    if days not in (30, 90, 365):
        raise HTTPException(status_code=400, detail="days 30, 90 veya 365 olmalı")
    require_database()
    chosen_rep = selected_rep(user, rep_id)
    today = _now().date()
    return cache.cached(
        ("reports", user.role, user.email, chosen_rep, days, today.isoformat()),
        REPORT_TTL,
        lambda: jsonable_encoder(build_reports(user, chosen_rep, days, today)),
    )


@app.get("/api/profiles")
def profiles(days: int = 90, user: AuthUser = Depends(admin_user)) -> Any:
    if days not in (30, 90, 365):
        raise HTTPException(status_code=400, detail="days 30, 90 veya 365 olmalı")
    require_database()
    today = _now().date()
    window = DateWindow(start=today - timedelta(days=days), end=today)
    return cache.cached(
        ("profiles", days, today.isoformat()),
        15 * 60,
        lambda: jsonable_encoder(
            {"warning": PROFILE_WARNING, "profiles": performance_profiles(window)}
        ),
    )


# --- zamanlanmış işler -------------------------------------------------------


@app.get("/api/cron/hourly-nudges", dependencies=[Depends(require_cron)])
def hourly_nudges() -> Any:
    require_database()
    return jsonable_encoder(run_hourly_nudges(_now()))


@app.post("/api/ingest")
def ingest_now(user: AuthUser = Depends(admin_user)) -> Any:
    """Paneldeki 'Verileri güncelle' butonu. Dakikada en fazla bir kez."""
    require_database()
    now = _now()
    last = data_as_of()
    if last is not None and (now - last).total_seconds() < MANUAL_INGEST_MIN_GAP:
        return {"skipped": "yeni_guncellendi", "data_as_of": last}
    # Mesai dışında da çalışır; pencereli kaynaklar çeyrek saat kuralına uyar
    # ki istek Vercel'in 60 sn sınırına sığsın.
    report = run_live_ingest(now, force=True)
    cache.clear()
    return jsonable_encoder(report)


@app.get("/api/cron/live-ingest", dependencies=[Depends(require_cron)])
def live_ingest() -> Any:
    """Harici tetikleyici (ör. cron-job.org, 5 dk). Vercel 60 sn sınırında kalır."""
    require_database()
    report = run_live_ingest(_now())
    cache.clear()
    return jsonable_encoder(report)


# --- arayüz ------------------------------------------------------------------

_FALLBACK_HTML = """<!doctype html><meta charset="utf-8"><title>Pusula</title>
<p style="font-family:system-ui;padding:2rem">Arayüz derlenmemiş:
<code>npm --prefix web run build</code></p>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> Any:
    page = PUBLIC_DIR / "index.html"
    if page.is_file():
        return FileResponse(page, headers={"Cache-Control": "no-cache"})
    return HTMLResponse(_FALLBACK_HTML)


if (PUBLIC_DIR / "assets").is_dir() and not os.environ.get("VERCEL"):
    # Vercel public/ klasörünü CDN'den verir; yerelde FastAPI verir.
    app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="assets")
