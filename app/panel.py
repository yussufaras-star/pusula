"""Pusula operasyon paneli — Streamlit.

Üç katman: yönetici, temsilci, ekip ekranı (isimsiz).
Giriş: st.session_state + st.secrets[passwords]. Salt okuma.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import streamlit as st

from pusula.panel_auth import (
    ADMIN_EMAIL,
    AuthUser,
    LOGIN_ERROR,
    lookup_secret_password,
    normalize_login_email,
    password_matches,
    resolve_user,
)
from pusula.panel_ciro import (
    SALES_TEAM_IDS,
    ciro_monthly_by_rep,
    ciro_rep_monthly,
    ciro_team_monthly,
    ciro_ytd_by_rep,
    fmt_tl,
    latest_deal_created_at,
)
from pusula.panel_data import (
    CRM_DK_PER_GORUSME,
    DEFAULT_ARAMA_PER_LEAD,
    DEFAULT_TOPLANTI_GUN,
    OLU_ZAMAN_SN,
    TOPLANTI_DK,
    WINDOW_DAYS,
    arrow,
    fmt_duration,
    fmt_num,
    fmt_pct,
    funnel,
    hourly_table,
    latest_event_created_at,
    load_rep_by_email,
    load_reps,
    mean,
    path_take_rate,
    rep_snapshot,
    sales_cycle,
    source_take_rate,
    take_rate,
    talk_duration_by_rep,
    team_reach_and_join,
    today_blocks,
    weekly_series,
    weekly_team_series,
    workload_board,
)
from pusula.panel_profile import PROFILE_WARNING, performance_profiles

CACHE_TTL = 15 * 60
_TZ = ZoneInfo("Europe/Istanbul")

# Tanım balonları — metin birebir.
HELP_ULASMA = (
    "Yapilan aramalarin kacinda karsi taraf telefonu acti. "
    "Sonucu 'Yanit yok/Mesgul' veya 'Gecersiz numara' olanlar "
    "ulasilamamis sayilir; sure esigi yoktur. Planlanmis ama "
    "yapilmamis aramalar sayilmaz."
)
HELP_ARAMA = (
    "Yapilan arama sayisi. Karsi taraf acmasa bile sayilir. "
    "Planlanmis ama henuz yapilmamis aramalar haric."
)
HELP_ULASILAN = "Telefonun acildigi arama sayisi."
HELP_RANDEVU = (
    "Zoho Bookings uzerinden alinan randevu sayisi. "
    "Iptal edilenler dahildir."
)
HELP_KATILIM = (
    "Randevuya gelenlerin, gelen ve gelmeyenlerin toplamina orani. "
    "Sonucu isaretlenmemis randevular hesaba katilmaz."
)
HELP_TAKE_GENEL = (
    "Tum lead'lerin kacinin satisa dondugu. Satis isareti, lead'in "
    "contact kaydina donmesidir."
)
HELP_TAKE_ULASILANDA = (
    "En az bir kez telefonu acilmis lead'lerin kacinin satisa "
    "dondugu. Payda daha kucuk oldugu icin oran daha yuksek cikar."
)
HELP_DONGU = (
    "Lead'in temsilciye atanmasindan satisa donmesine kadar "
    "gecen gun sayisi. Yalniz satisa donenler hesaba katilir."
)
HELP_TIPIK = (
    "Tipik — siralandiginda tam ortadaki deger. Ortalamadan farki, "
    "birkac cok uzun kaydin sayiyi yukari cekmemesi."
)
HELP_SURE = (
    "Telefonun acildigi gorusmelerin ortalama suresi. Tipik de "
    "gosterilir cunku birkac uzun gorusme ortalamayi yukari cekebilir."
)
HELP_ISYUKU = (
    "Kisi basi gunluk ortalama. Son 90 gunun pazar disi is "
    "gunlerine bolunmustur."
)
HELP_DOLULUK = (
    "Gun doluluk orani — olculebilen islerin (arama, gorusme, "
    "toplanti, CRM kayit) gunun kacini doldurdugu. WhatsApp ve "
    "mola bu hesaba dahil degildir."
)
HELP_LEAD = "Temsilciye atanan yeni lead sayisi."
HELP_HUNI = (
    "Lead'in su anki Zoho statusu. Gecmis statu degisim kaydi "
    "tutulmadigi icin bu anlik bir goruntudur, gecis sayisi degildir."
)
HELP_TOPLANTI = (
    "Lead'ler toplanti durumuna gore gruplanip her grubun satisa "
    "donme orani hesaplanir. Toplantilar, satis tarihinden onceki "
    "randevulardir."
)
HELP_YOL = (
    "Her lead, satisindan once yasadigi en ileri asamaya gore "
    "siniflanir: toplantiya katildi > randevu aldi katilmadi > "
    "sadece arandi > hic aranmadi."
)
HELP_SOURCE = (
    "Lead'in Zoho'daki kaynak alani. Contact Form ile Register "
    "arasindaki temas orani farki dusuktur (~4 puan)."
)
HELP_BUGUN = (
    "Bugunun bloklari. Ok, son 90 gunun ayni blok gunluk "
    "ortalamasina gore."
)
HELP_CIRO = (
    "Kapandi Kazanildi asamasindaki anlasmalarin toplam "
    "tutari. Ilk kez satin alan ve tekrar satin alan ayrimi "
    "yapilmaz, hepsi sayilir."
)
HELP_CIRO_ORT = "Toplam ciro / satis adedi."

COL_HELP: dict[str, str] = {
    "arama": HELP_ARAMA,
    "arama/gün": HELP_ARAMA,
    "ulaşılan": HELP_ULASILAN,
    "ulaşılan/gün": HELP_ULASILAN,
    "ulaşma %": HELP_ULASMA,
    "randevu": HELP_RANDEVU,
    "randevu/gün": HELP_RANDEVU,
    "katılım %": HELP_KATILIM,
    "ortalama": HELP_SURE,
    "tipik": HELP_TIPIK,
    "ortalama gün": HELP_DONGU,
    "tipik gün": HELP_DONGU,
    "genelde": HELP_TAKE_GENEL,
    "ulaşılarda": HELP_TAKE_ULASILANDA,
    "kaynak": HELP_SOURCE,
    "yol": HELP_YOL,
    "PLANLANAN": HELP_ISYUKU,
    "GERÇEKLEŞEN": HELP_ISYUKU,
    "PLANLANAN dk": HELP_ISYUKU,
    "GERÇEKLEŞEN dk": HELP_ISYUKU,
    "plan gerçekleşme": (
        "Plan gerçekleşme oranı — gerçekleşen / planlanan. "
        "Her satır ve toplam süre için ayrı hesaplanır."
    ),
    "durum": HELP_HUNI,
    "1.Arama-Ulaşılamadı": HELP_HUNI,
    "2.Arama-Ulaşılamadı": HELP_HUNI,
    "3.Arama-Ulaşılamadı": HELP_HUNI,
    "Aging": HELP_HUNI,
    "ciro": HELP_CIRO,
    "satış başına ortalama": HELP_CIRO_ORT,
    "ortalama tutar": HELP_CIRO_ORT,
}

CHART_HELP: dict[str, str] = {
    "arama": HELP_ARAMA,
    "arama_ham": HELP_ARAMA,
    "ulasma_orani": HELP_ULASMA,
    "randevu": HELP_RANDEVU,
    "katilim_orani": HELP_KATILIM,
    "take_genel": HELP_TAKE_GENEL,
    "take_ulasilanda": HELP_TAKE_ULASILANDA,
}


def _require_env() -> None:
    if not (os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")):
        st.error("DATABASE_URL_POOLED / DATABASE_URL yok")
        st.stop()


def _apply_secrets() -> None:
    """st.secrets'teki havuz URL'sini ortam değişkenine taşır.

    Yerel .env doluysa üzerine yazılmaz. Placeholder (...) yok sayılır.
    """
    try:
        pooled = st.secrets.get("DATABASE_URL_POOLED")
    except Exception:
        return
    if pooled is None:
        return
    text = str(pooled).strip()
    if not text or text == "...":
        return
    if not os.environ.get("DATABASE_URL_POOLED"):
        os.environ["DATABASE_URL_POOLED"] = text


def _passwords() -> dict[str, str]:
    try:
        raw = st.secrets.get("passwords")
    except Exception:
        return {}
    if not raw:
        return {}
    return {str(k): str(v) for k, v in dict(raw).items()}


def _session_user() -> AuthUser | None:
    email = st.session_state.get("auth_email")
    role = st.session_state.get("auth_role")
    if not email or role not in ("admin", "rep"):
        return None
    if role == "rep" and not st.session_state.get("auth_rep_id"):
        return None
    return AuthUser(
        email=str(email),
        role=role,
        rep_id=st.session_state.get("auth_rep_id"),
        full_name=st.session_state.get("auth_name"),
    )


def _store_user(user: AuthUser) -> None:
    st.session_state["auth_email"] = user.email
    st.session_state["auth_role"] = user.role
    st.session_state["auth_rep_id"] = user.rep_id
    st.session_state["auth_name"] = user.full_name


def _render_login() -> None:
    st.title("Pusula")
    if not _passwords():
        st.error("giriş yapılandırması yok")
    with st.form("login"):
        email = st.text_input("Kullanıcı adı")
        password = st.text_input("Şifre", type="password")
        submitted = st.form_submit_button("Giriş")
    if not submitted:
        return
    passwords = _passwords()
    stored = lookup_secret_password(passwords, email)
    if stored is None or not password_matches(password, stored):
        st.error(LOGIN_ERROR)
        return
    rep = None
    if normalize_login_email(email) != ADMIN_EMAIL:
        _require_env()
        rep = load_rep_by_email(email)
    user = resolve_user(email, password, passwords, rep)
    if user is None:
        st.error(LOGIN_ERROR)
        return
    _store_user(user)
    st.rerun()


def _render_header(user: AuthUser) -> None:
    left, right = st.columns([6, 1])
    with left:
        st.title("Pusula")
        st.caption(f"son {WINDOW_DAYS} gün · dönüşüm 1 Mayıs 2026 sonrası")
    with right:
        st.caption(user.email)
        if st.button("Çıkış", use_container_width=True):
            st.session_state.clear()
            st.rerun()


@st.cache_data(ttl=CACHE_TTL)
def _reps() -> list[tuple[str, str]]:
    return [(r.rep_id, r.full_name) for r in load_reps()]


@st.cache_data(ttl=CACHE_TTL)
def _hourly(rep_id: str | None) -> list[dict[str, Any]]:
    return hourly_table(rep_id)


@st.cache_data(ttl=CACHE_TTL)
def _team_dip() -> dict[str, float | None]:
    return team_reach_and_join()


@st.cache_data(ttl=CACHE_TTL)
def _board(
    rep_id: str | None, arama_per_lead: float, toplanti_gun: float
) -> dict[str, Any]:
    return workload_board(rep_id, arama_per_lead, toplanti_gun)


@st.cache_data(ttl=CACHE_TTL)
def _talk() -> list[dict[str, Any]]:
    return talk_duration_by_rep()


@st.cache_data(ttl=CACHE_TTL)
def _cycle() -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    return sales_cycle()


@st.cache_data(ttl=CACHE_TTL)
def _take() -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    return take_rate()


@st.cache_data(ttl=CACHE_TTL)
def _source() -> list[dict[str, Any]]:
    return source_take_rate()


@st.cache_data(ttl=CACHE_TTL)
def _path() -> list[dict[str, Any]]:
    return path_take_rate()


@st.cache_data(ttl=CACHE_TTL)
def _funnel(rep_id: str | None, named: bool) -> list[dict[str, Any]]:
    return funnel(rep_id, named=named)


@st.cache_data(ttl=CACHE_TTL)
def _weekly(rep_id: str | None) -> list[dict[str, Any]]:
    return weekly_series(rep_id)


@st.cache_data(ttl=CACHE_TTL)
def _weekly_team() -> list[dict[str, Any]]:
    return weekly_team_series()


@st.cache_data(ttl=CACHE_TTL)
def _rep_snap(rep_id: str) -> dict[str, Any]:
    return rep_snapshot(rep_id)


@st.cache_data(ttl=CACHE_TTL)
def _today_blocks(rep_id: str | None) -> dict[str, Any]:
    return today_blocks(rep_id)


@st.cache_data(ttl=CACHE_TTL)
def _latest_event() -> datetime | None:
    return latest_event_created_at()


@st.cache_data(ttl=CACHE_TTL)
def _latest_deal() -> datetime | None:
    return latest_deal_created_at()


@st.cache_data(ttl=CACHE_TTL)
def _ciro_ytd(kind: str) -> list[dict[str, Any]]:
    return ciro_ytd_by_rep(kind)  # type: ignore[arg-type]


@st.cache_data(ttl=CACHE_TTL)
def _ciro_monthly(kind: str, rep_id: str | None) -> list[dict[str, Any]]:
    return ciro_monthly_by_rep(kind, rep_id=rep_id)  # type: ignore[arg-type]


@st.cache_data(ttl=CACHE_TTL)
def _ciro_team_months() -> list[dict[str, Any]]:
    return ciro_team_monthly()


@st.cache_data(ttl=CACHE_TTL)
def _ciro_one(rep_id: str) -> list[dict[str, Any]]:
    return ciro_rep_monthly(rep_id)


@st.cache_data(ttl=CACHE_TTL)
def _profiles() -> list[dict[str, Any]]:
    return performance_profiles()


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _col_config(frame: pd.DataFrame) -> dict[str, Any] | None:
    cfg: dict[str, Any] = {}
    for col in frame.columns:
        text = COL_HELP.get(str(col))
        if text:
            cfg[str(col)] = st.column_config.Column(str(col), help=text)
    return cfg or None


def _table(frame: pd.DataFrame) -> None:
    st.dataframe(
        frame,
        hide_index=True,
        use_container_width=False,
        column_config=_col_config(frame),
    )


def _heading(title: str, help_text: str | None = None) -> None:
    st.subheader(title, help=help_text)


def _fmt_clock(value: datetime | None) -> str:
    if value is None:
        return "—"
    dt = value if value.tzinfo is not None else value.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ).strftime("%H:%M")


def _fmt_event_ts(value: datetime | None) -> str:
    if value is None:
        return "—"
    dt = value if value.tzinfo is not None else value.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ).strftime("%d.%m.%Y %H:%M")


def _render_bugun(rep_id: str | None, *, blok_disi: bool) -> None:
    data = _today_blocks(rep_id)
    _heading("Bugün", HELP_BUGUN)
    for block in data.get("blocks") or []:
        if str(block.get("key")) == "blok_disi" and not blok_disi:
            continue
        kind = str(block.get("kind") or "")
        today = block.get("today") or {}
        avg90 = block.get("avg90") or {}
        st.markdown(f"**{block.get('label')}**")
        if kind == "call":
            c1, c2, c3 = st.columns(3)
            with c1:
                _compare(
                    "arama",
                    today.get("arama"),
                    avg90.get("arama"),
                    help_text=HELP_ARAMA,
                )
            with c2:
                _compare(
                    "ulaşılan görüşme",
                    today.get("ulasilan"),
                    avg90.get("ulasilan"),
                    help_text=HELP_ULASILAN,
                )
            with c3:
                _compare(
                    "ulaşma oranı",
                    today.get("ulasma_orani"),
                    avg90.get("ulasma_orani"),
                    pct=True,
                    help_text=HELP_ULASMA,
                )
        elif kind == "meeting":
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                _compare(
                    "randevu",
                    today.get("randevu"),
                    avg90.get("randevu"),
                    help_text=HELP_RANDEVU,
                )
            with c2:
                _compare(
                    "katıldı",
                    today.get("katildi"),
                    avg90.get("katildi"),
                    help_text=HELP_KATILIM,
                )
            with c3:
                _compare(
                    "katılmadı",
                    today.get("katilmadi"),
                    avg90.get("katilmadi"),
                    help_text=HELP_KATILIM,
                )
            with c4:
                _compare(
                    "sonuç girilmedi",
                    today.get("sonuc_girilmedi"),
                    avg90.get("sonuc_girilmedi"),
                    help_text=HELP_KATILIM,
                )
        else:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                _compare(
                    "arama",
                    today.get("arama"),
                    avg90.get("arama"),
                    help_text=HELP_ARAMA,
                )
            with c2:
                _compare(
                    "ulaşılan görüşme",
                    today.get("ulasilan"),
                    avg90.get("ulasilan"),
                    help_text=HELP_ULASILAN,
                )
            with c3:
                _compare(
                    "randevu",
                    today.get("randevu"),
                    avg90.get("randevu"),
                    help_text=HELP_RANDEVU,
                )
            with c4:
                _compare(
                    "katıldı",
                    today.get("katildi"),
                    avg90.get("katildi"),
                    help_text=HELP_KATILIM,
                )
    st.caption(f"Son veri guncelleme: {_fmt_clock(_latest_event())}")


def _line_chart(
    frame: pd.DataFrame,
    y_col: str,
    title: str,
    *,
    is_pct: bool = False,
    x_col: str = "hafta",
    x_title: str | None = None,
) -> None:
    """NaN = boşluk (sıfır değil)."""
    import altair as alt

    data = frame.copy()
    if data.empty or y_col not in data.columns:
        st.caption("veri yetersiz")
        return
    y_enc = alt.Y(y_col, title=title, type="quantitative")
    x_label = x_title if x_title is not None else x_col
    chart = (
        alt.Chart(data)
        .mark_line(point=True, invalid="break-paths-show-domains")
        .encode(
            x=alt.X(f"{x_col}:T", title=x_label),
            y=y_enc,
            color=alt.Color("seri:N", title=""),
            tooltip=[f"{x_col}:T", "seri:N", y_col],
        )
        .properties(height=240)
    )
    st.altair_chart(chart, use_container_width=True)


def _week_delta(rows: list[dict[str, Any]], key: str) -> str:
    values = [r.get(key) for r in rows]
    last = values[-1] if values else None
    avg12 = mean([v for v in values if isinstance(v, (int, float))])
    last_f = float(last) if isinstance(last, (int, float)) else None
    return arrow(last_f, avg12)


def _compare(
    label: str,
    cur: float | None,
    prev: float | None,
    *,
    pct: bool = False,
    duration: bool = False,
    help_text: str | None = None,
) -> None:
    if duration:
        shown = fmt_duration(cur)
    elif pct:
        shown = fmt_pct(cur)
    else:
        shown = fmt_num(cur)
    st.metric(label, shown, arrow(cur, prev), help=help_text)


def _ciro_ytd_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.caption("veri yetersiz")
        return
    show = pd.DataFrame(
        {
            "temsilci": [r["temsilci"] for r in rows],
            "satış": [r["satis"] for r in rows],
            "ciro": [fmt_tl(r["ciro"]) for r in rows],
            "satış başına ortalama": [fmt_tl(r["ortalama"]) for r in rows],
        }
    )
    _table(show)


def _ciro_month_table(rows: list[dict[str, Any]], *, named: bool) -> None:
    if not rows:
        st.caption("veri yetersiz")
        return
    data: dict[str, list[Any]] = {
        "ay": [r["ay_etiket"] for r in rows],
        "satış": [r["satis"] for r in rows],
        "ciro": [fmt_tl(r["ciro"]) for r in rows],
        "satış başına ortalama": [fmt_tl(r["ortalama"]) for r in rows],
    }
    if named:
        data = {
            "ay": data["ay"],
            "temsilci": [r["temsilci"] for r in rows],
            "satış": data["satış"],
            "ciro": data["ciro"],
            "satış başına ortalama": data["satış başına ortalama"],
        }
    _table(pd.DataFrame(data))


def _deal_caption() -> None:
    st.caption(
        f"Deals son kayıt: {_fmt_event_ts(_latest_deal())}. "
        "Ingest gecikirse son ay eksik görünebilir."
    )


def _render_ciro_yonetici(rep_id: str | None) -> None:
    _heading("Ciro", HELP_CIRO)
    st.markdown("2026 başından bugüne")
    sales_id = rep_id if rep_id in SALES_TEAM_IDS else None
    ytd = _ciro_ytd("sales")
    if sales_id:
        ytd = [r for r in ytd if r["rep_id"] == sales_id]
    _ciro_ytd_table(ytd)
    monthly = _ciro_monthly("sales", sales_id)
    st.markdown("Aylık", help=HELP_CIRO_ORT)
    _ciro_month_table(monthly, named=sales_id is None)

    team_m = _ciro_team_months()
    team_df = _df(team_m)
    if not team_df.empty:
        t = team_df[["ay", "ciro"]].copy()
        t["seri"] = "satış ekibi"
        st.markdown("Aylık toplam ciro (satış ekibi)", help=HELP_CIRO)
        _line_chart(t, "ciro", "ciro", x_col="ay", x_title="ay")

    if sales_id:
        one = _ciro_one(sales_id)
        parts: list[pd.DataFrame] = []
        if one:
            r = _df(one)[["ay", "ciro"]].copy()
            r["seri"] = "seçilen"
            parts.append(r)
        if not team_df.empty:
            o = team_df[["ay", "kisi_basi"]].copy()
            o = o.rename(columns={"kisi_basi": "ciro"})
            o["seri"] = "operasyon"
            parts.append(o)
        if parts:
            st.markdown("Aylık ciro (seçilen ve operasyon ortalaması)", help=HELP_CIRO)
            _line_chart(
                pd.concat(parts, ignore_index=True),
                "ciro",
                "ciro",
                x_col="ay",
                x_title="ay",
            )

    st.markdown("Satış sonrası ekip", help=HELP_CIRO)
    _ciro_ytd_table(_ciro_ytd("after_sales"))
    _ciro_month_table(_ciro_monthly("after_sales", None), named=True)
    _deal_caption()


def _render_ciro_temsilci(rep_id: str) -> None:
    _heading("Ciro", HELP_CIRO)
    ytd = [r for r in _ciro_ytd("sales") if r["rep_id"] == rep_id]
    _ciro_ytd_table(ytd)
    monthly = _ciro_one(rep_id)
    st.markdown("Aylık", help=HELP_CIRO_ORT)
    _ciro_month_table(monthly, named=False)
    if monthly:
        frame = _df(monthly)[["ay", "ciro"]].copy()
        frame["seri"] = "kendisi"
        st.markdown("Aylık ciro", help=HELP_CIRO)
        _line_chart(frame, "ciro", "ciro", x_col="ay", x_title="ay")
    _deal_caption()


def _render_profil() -> None:
    st.subheader("Performans profili")
    st.info(PROFILE_WARNING)
    cards = _profiles()
    for card in cards:
        with st.container(border=True):
            st.markdown(f"**{card['temsilci']}**")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("Güçlü yönler")
                items = card.get("guclu") or []
                if items:
                    for line in items:
                        st.write(line)
                else:
                    st.caption("eşik üstü metrik yok")
            with c2:
                st.markdown("Zayıf yönler")
                items = card.get("zayif") or []
                if items:
                    for line in items:
                        st.write(line)
                else:
                    st.caption("eşik altı metrik yok")
            st.markdown("Eğri yönü")
            for line in card.get("egri") or []:
                st.write(line)
            st.markdown("Süreklilik")
            for line in card.get("sureklilik") or []:
                st.write(line)
            st.caption(" · ".join(card.get("kapsam") or []))


def render_yonetici() -> None:
    reps = _reps()
    options = [("tumu", "tümü")] + reps
    labels = [lab for _, lab in options]
    choice = st.selectbox("Temsilci", labels, key="yonetici_rep")
    rep_id = None
    for rid, lab in options:
        if lab == choice:
            rep_id = None if rid == "tumu" else rid
            break

    _render_bugun(rep_id, blok_disi=True)

    st.subheader("Saatlik")
    hourly = _hourly(rep_id)
    hframe = _df(hourly)
    show = hframe.copy()
    show["ulaşma %"] = show["ulasma_orani"].map(fmt_pct)
    show["katılım %"] = show["katilim_orani"].map(fmt_pct)
    _table(
        show[
            ["saat", "arama", "ulasilan", "ulaşma %", "randevu", "katildi", "katılım %"]
        ].rename(
            columns={
                "ulasilan": "ulaşılan",
                "katildi": "katıldı",
            }
        )
    )
    dip = _team_dip()
    c1, c2 = st.columns(2)
    c1.metric(
        "ekip ulaşma oranı",
        fmt_pct(dip.get("ulasma_orani")),
        help=HELP_ULASMA,
    )
    c2.metric(
        "ekip katılım oranı",
        fmt_pct(dip.get("katilim_orani")),
        help=HELP_KATILIM,
    )

    _heading("Günlük iş yükü (kişi başı)", HELP_ISYUKU)
    p1, p2 = st.columns(2)
    arama_per_lead = p1.number_input(
        "lead başına arama (plan)",
        min_value=0.5,
        max_value=20.0,
        value=float(DEFAULT_ARAMA_PER_LEAD),
        step=0.5,
    )
    toplanti_gun = p2.number_input(
        "günde gerçekleşen toplantı (plan)",
        min_value=0.0,
        max_value=20.0,
        value=float(DEFAULT_TOPLANTI_GUN),
        step=0.5,
    )
    board = _board(rep_id, float(arama_per_lead), float(toplanti_gun))
    bframe = _df(board["rows"]).rename(
        columns={
            "planlanan": "PLANLANAN",
            "plan dk": "PLANLANAN dk",
            "gerçekleşen": "GERÇEKLEŞEN",
            "gerçek dk": "GERÇEKLEŞEN dk",
        }
    )
    bframe["plan gerçekleşme"] = bframe["plan gerçekleşme"].map(fmt_pct)
    _table(bframe)
    v1, v2, v3 = st.columns(3)
    v1.metric("planlanan toplam", f"{board['plan_saat']} saat")
    v2.metric("gerçekleşen toplam", f"{board['gercek_saat']} saat")
    v3.metric(
        "gün doluluk oranı",
        fmt_pct(board.get("doluluk")),
        help=HELP_DOLULUK,
    )
    st.caption(
        f"plan gerçekleşme (süre) {fmt_pct(board.get('toplam_oran'))} · "
        f"ulaşılamayan arama ort. {fmt_duration(board.get('miss_sn'))} · "
        f"ulaşılan görüşme ort. {fmt_duration(board.get('hit_sn'))} · "
        f"toplantı {int(TOPLANTI_DK)} dk · CRM {CRM_DK_PER_GORUSME} dk/görüşme · "
        f"ölü zaman {fmt_duration(OLU_ZAMAN_SN)}/arama · {int(board.get('workdays') or 0)} iş günü"
    )

    _heading("Görüşme süresi", HELP_SURE)
    talk = _df(_talk())
    if not talk.empty:
        talk_show = pd.DataFrame(
            {
                "temsilci": talk["temsilci"],
                "ortalama": talk["ortalama_sn"].map(fmt_duration),
                "tipik": talk["medyan_sn"].map(fmt_duration),
                "görüşme": talk["n"],
            }
        )
        _table(talk_show)
    else:
        st.caption("veri yetersiz")

    _heading("Satış döngüsü (gün)", HELP_DONGU)
    cycle_rep, cycle_team = _cycle()
    d1, d2, d3 = st.columns(3)
    d1.metric(
        "ortalama gün",
        fmt_num(cycle_team.get("ortalama_gun")),
        help=HELP_DONGU,
    )
    d2.metric(
        "tipik gün",
        fmt_num(cycle_team.get("medyan_gun")),
        help=HELP_TIPIK,
    )
    d3.metric("n", str(cycle_team.get("n") or 0))
    cyc = _df(cycle_rep)
    if not cyc.empty:
        cyc["ortalama_gun"] = cyc["ortalama_gun"].map(lambda v: fmt_num(v, 1))
        cyc["medyan_gun"] = cyc["medyan_gun"].map(lambda v: fmt_num(v, 1))
    _table(
        cyc.rename(
            columns={"ortalama_gun": "ortalama gün", "medyan_gun": "tipik gün"}
        )
    )

    _heading("Satışa dönme oranı", HELP_TAKE_GENEL)
    take_rep, take_team = _take()
    t1, t2 = st.columns(2)
    t1.metric(
        "Genelde satışa dönme oranı",
        fmt_pct(take_team.get("genel")),
        help=HELP_TAKE_GENEL,
    )
    t2.metric(
        "Ulaşılarda satışa dönme oranı",
        fmt_pct(take_team.get("ulasilanda")),
        help=HELP_TAKE_ULASILANDA,
    )
    tframe = _df(take_rep)
    tframe["genelde"] = tframe["genel"].map(fmt_pct)
    tframe["ulaşılarda"] = tframe["ulasilanda"].map(fmt_pct)
    _table(tframe[["temsilci", "leads", "genelde", "ulaşılarda"]])

    _heading("Satış kaynağı", HELP_SOURCE)
    src = _df(_source())
    src["genelde"] = src["genelde"].map(fmt_pct)
    src["ulaşılarda"] = src["ulasilanda"].map(fmt_pct)
    st.markdown("Lead source", help=HELP_SOURCE)
    _table(src[["kaynak", "lead", "satis", "genelde", "ulaşılarda"]])
    path = _df(_path())
    path["genelde"] = path["genelde"].map(fmt_pct)
    path["ulaşılarda"] = path["ulasilanda"].map(fmt_pct)
    st.markdown("Satışa giden yol", help=HELP_YOL)
    _table(path[["yol", "lead", "satis", "genelde", "ulaşılarda"]])

    _heading("Huni", HELP_HUNI)
    fun = _funnel(rep_id, True)
    _table(_df(fun))

    _render_ciro_yonetici(rep_id)
    _render_profil()

    st.subheader("Haftalık gelişim (12 hafta)")
    team_w = _weekly_team()
    if rep_id is None:
        _render_weekly_charts(team_w, None, kisi_basi=True)
    else:
        rep_w = _weekly(rep_id)
        _render_weekly_charts(team_w, rep_w, kisi_basi=True)


def _render_weekly_charts(
    team_rows: list[dict[str, Any]],
    rep_rows: list[dict[str, Any]] | None,
    *,
    kisi_basi: bool,
    take: bool = True,
) -> None:
    specs: list[tuple[str, str, bool]] = [
        ("arama", "arama (kişi başı)" if kisi_basi else "arama", False),
        ("ulasma_orani", "ulaşma oranı", True),
        ("randevu", "randevu", False),
        ("katilim_orani", "katılım oranı", True),
    ]
    for key, title, is_pct in specs:
        parts: list[pd.DataFrame] = []
        team_df = _df(team_rows)
        if not team_df.empty:
            t = team_df[["hafta", key]].copy()
            t["seri"] = "operasyon"
            parts.append(t)
        if rep_rows is not None:
            rdf = _df(rep_rows)
            if not rdf.empty:
                r = rdf[["hafta", key]].copy()
                r["seri"] = "seçilen"
                parts.append(r)
        if not parts:
            continue
        merged = pd.concat(parts, ignore_index=True)
        st.markdown(title, help=CHART_HELP.get(key))
        _line_chart(merged, key, title, is_pct=is_pct)
        src = rep_rows if rep_rows is not None else team_rows
        st.caption(f"son hafta vs 12 haftalık ortalama: {_week_delta(src, key)}")
    if take:
        _render_donusum_weekly(rep_rows if rep_rows is not None else team_rows)


def _render_donusum_weekly(rows: list[dict[str, Any]]) -> None:
    frame = _df(rows)
    parts: list[pd.DataFrame] = []
    for key, label in (
        ("take_genel", "genelde"),
        ("take_ulasilanda", "ulaşılarda"),
    ):
        if frame.empty or key not in frame.columns:
            continue
        t = frame[["hafta", key]].copy()
        t["seri"] = label
        t = t.rename(columns={key: "oran"})
        parts.append(t)
    if not parts:
        return
    merged = pd.concat(parts, ignore_index=True)
    st.markdown("satışa dönme oranı", help=HELP_TAKE_GENEL)
    _line_chart(merged, "oran", "satışa dönme oranı", is_pct=True)
    st.caption(
        f"genelde son hafta vs 12 haftalık ortalama: "
        f"{_week_delta(rows, 'take_genel')}"
    )
    st.caption(
        f"ulaşılarda son hafta vs 12 haftalık ortalama: "
        f"{_week_delta(rows, 'take_ulasilanda')}"
    )


def render_temsilci(locked_rep_id: str | None = None) -> None:
    reps = _reps()
    if not reps:
        st.write("aktif satış temsilcisi yok")
        return
    if locked_rep_id is not None:
        names = {rid: name for rid, name in reps}
        if locked_rep_id not in names:
            st.write("aktif satış temsilcisi yok")
            return
        rep_id = locked_rep_id
    else:
        names = {name: rid for rid, name in reps}
        choice = st.selectbox("Temsilci", list(names), key="temsilci_sel")
        rep_id = names[choice]
    snap = _rep_snap(rep_id)
    cur = snap["current"]
    prev = snap["previous"]

    st.caption(f"son {WINDOW_DAYS} gün, önceki {WINDOW_DAYS} günle kıyas")
    _render_bugun(rep_id, blok_disi=False)
    c1, c2, c3 = st.columns(3)
    with c1:
        _compare(
            "ulaşma oranı",
            cur.get("ulasma_orani"),
            prev.get("ulasma_orani"),
            pct=True,
            help_text=HELP_ULASMA,
        )
        _compare(
            "arama / gün",
            cur.get("arama_gun"),
            prev.get("arama_gun"),
            help_text=HELP_ARAMA,
        )
    with c2:
        _compare(
            "ulaşılan / gün",
            cur.get("ulasilan_gun"),
            prev.get("ulasilan_gun"),
            help_text=HELP_ULASILAN,
        )
        _compare(
            "görüşme ortalama",
            cur.get("ortalama_sn"),
            prev.get("ortalama_sn"),
            duration=True,
            help_text=HELP_SURE,
        )
    with c3:
        _compare(
            "görüşme tipik",
            cur.get("medyan_sn"),
            prev.get("medyan_sn"),
            duration=True,
            help_text=HELP_TIPIK,
        )
        _compare(
            "gelen lead",
            cur.get("lead"),
            prev.get("lead"),
            help_text=HELP_LEAD,
        )

    d1, d2, d3 = st.columns(3)
    with d1:
        _compare("randevu", cur.get("randevu"), prev.get("randevu"), help_text=HELP_RANDEVU)
    with d2:
        _compare("katıldı", cur.get("katildi"), prev.get("katildi"), help_text=HELP_KATILIM)
    with d3:
        _compare(
            "katılım oranı",
            cur.get("katilim_orani"),
            prev.get("katilim_orani"),
            pct=True,
            help_text=HELP_KATILIM,
        )

    w1, w2, w3 = st.columns(3)
    with w1:
        _compare(
            "randevu / gün",
            cur.get("randevu_gun"),
            prev.get("randevu_gun"),
            help_text=HELP_RANDEVU,
        )
    with w2:
        _compare(
            "toplantı / gün",
            cur.get("toplanti_gun"),
            prev.get("toplanti_gun"),
            help_text=HELP_ISYUKU,
        )
    with w3:
        _compare(
            "CRM kayıt / gün",
            cur.get("crm_gun"),
            prev.get("crm_gun"),
            help_text=HELP_ISYUKU,
        )
    st.caption(f"CRM kayıt tahmini {CRM_DK_PER_GORUSME} dk/görüşme")

    _compare(
        "ulaşılan görüşmenin randevuya dönme oranı",
        cur.get("temas_randevu_orani"),
        prev.get("temas_randevu_orani"),
        pct=True,
    )

    _heading("Saatlik arama ve ulaşma", HELP_ULASMA)
    hourly = _hourly(rep_id)
    hframe = _df(hourly)
    show = hframe[["saat", "arama", "ulasilan", "ulasma_orani"]].copy()
    show["ulaşma %"] = show["ulasma_orani"].map(fmt_pct)
    _table(
        show[["saat", "arama", "ulasilan", "ulaşma %"]].rename(
            columns={"ulasilan": "ulaşılan"}
        )
    )

    _render_ciro_temsilci(rep_id)

    st.subheader("Haftalık gelişim (12 hafta)")
    rows = _weekly(rep_id)
    frame = _df(rows)
    if frame.empty:
        st.caption("veri yetersiz")
        return
    frame["seri"] = "kendisi"
    for key, title, is_pct in (
        ("arama_ham", "arama", False),
        ("ulasma_orani", "ulaşma oranı", True),
        ("randevu", "randevu", False),
        ("katilim_orani", "katılım oranı", True),
    ):
        st.markdown(title, help=CHART_HELP.get(key))
        _line_chart(frame[["hafta", key, "seri"]], key, title, is_pct=is_pct)
        src_key = key
        st.caption(f"son hafta vs 12 haftalık ortalama: {_week_delta(rows, src_key)}")


def render_ekip() -> None:
    st.caption("isim yok. operasyon geneli.")
    _heading("Toplantı etkisi", HELP_TOPLANTI)
    path = _df(_path())
    path["genelde"] = path["genelde"].map(fmt_pct)
    path["ulaşılarda"] = path["ulasilanda"].map(fmt_pct)
    _table(path[["yol", "lead", "satis", "genelde", "ulaşılarda"]])

    _heading("Saatlik katılım oranı", HELP_KATILIM)
    hourly = _hourly(None)
    hframe = _df(hourly)
    kat = hframe[["saat", "randevu", "katildi", "katilim_orani"]].copy()
    kat["katılım %"] = kat["katilim_orani"].map(fmt_pct)
    _table(
        kat[["saat", "randevu", "katildi", "katılım %"]].rename(
            columns={"katildi": "katıldı"}
        )
    )

    _heading("Saatlik ulaşma oranı", HELP_ULASMA)
    ulas = hframe[["saat", "arama", "ulasilan", "ulasma_orani"]].copy()
    ulas["ulaşma %"] = ulas["ulasma_orani"].map(fmt_pct)
    _table(
        ulas[["saat", "arama", "ulasilan", "ulaşma %"]].rename(
            columns={"ulasilan": "ulaşılan"}
        )
    )

    _heading("Huni", HELP_HUNI)
    _table(_df(_funnel(None, False)))


def main() -> None:
    st.set_page_config(page_title="Pusula panel", layout="wide")
    _apply_secrets()
    user = _session_user()
    if user is None:
        _render_login()
        return
    _require_env()
    _render_header(user)
    if user.role == "admin":
        tab_y, tab_t, tab_e = st.tabs(["Yönetici", "Temsilci", "Ekip"])
        with tab_y:
            render_yonetici()
        with tab_t:
            render_temsilci()
        with tab_e:
            render_ekip()
    else:
        render_temsilci(locked_rep_id=user.rep_id)
    st.caption(f"Veriler son guncelleme: {_fmt_event_ts(_latest_event())}")


if __name__ == "__main__":
    main()
