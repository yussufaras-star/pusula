"""Pusula operasyon paneli — Streamlit.

Üç katman: yönetici, temsilci, ekip ekranı (isimsiz).
Giriş: st.session_state + st.secrets[passwords]. Salt okuma.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
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
    CIRO_START,
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
    FUNNEL_DROPPED_STATUS,
    OLU_ZAMAN_SN,
    TOPLANTI_DK,
    WINDOW_DAYS,
    DateWindow,
    arrow,
    conv_window,
    default_window,
    fmt_day,
    fmt_duration,
    fmt_num,
    fmt_pct,
    fmt_window,
    funnel,
    funnel_dropped_by_rep,
    hourly_table,
    latest_event_created_at,
    lead_reach_breakdown,
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
from pusula.temas import RETURN_CALL_LOOKBACK_DAYS

CACHE_TTL = 15 * 60
_TZ = ZoneInfo("Europe/Istanbul")

# Tanım balonları — metin birebir.
HELP_ULASMA = (
    "Kac kisiye ulasildigi. Payda: donemde en az bir giden arama "
    "yapilan benzersiz lead. Pay: en az bir giden gorusme (sure > 0) "
    "veya en az bir donus alinan benzersiz lead. Ayni lead bir kez "
    "sayilir. Donusler dahildir."
)
HELP_ARANAN_LEAD = (
    "Donemde en az bir giden arama yapilan benzersiz lead."
)
HELP_GIDEN_TEMAS = (
    "En az bir giden temas (sure > 0 gorusme) kurulan benzersiz lead."
)
HELP_DONUSLE_GELEN = (
    "Giden aramayla ulasilamamis ama sonrasinda kendisi donmus lead'ler."
)
HELP_BENZERSIZ_ULASILAN = (
    "Giden temas ile donusle gelenin toplami. Ayni lead bir kez sayilir."
)
HELP_ARAMA = (
    "Giden arama sayisi. Karsi taraf acmasa bile sayilir. "
    "Planlanmis ama henuz yapilmamis aramalar haric."
)
HELP_ULASILAN = (
    "Telefonun acildigi giden gorusme ile donus aramasinin toplami. "
    "Arama sayisidir; ulasma orani lead bazlidir."
)
HELP_DONUS = (
    f"Musterinin geri aramasidir. Yonu gelen, suresi sifirdan buyuk "
    f"ve ayni hatta onceki {RETURN_CALL_LOOKBACK_DAYS} gun icinde "
    "giden arama kaydi vardir. Suresi sifir olanlar cevapsizdir, "
    "sayilmaz. Ulasma oraninda o lead bir kez sayilir."
)
HELP_GELEN = (
    "Yonu gelen, suresi sifirdan buyuk, donus aramasi olmayan "
    "cagri. Suresi sifir olanlar cevapsizdir, sayilmaz. "
    "Ulasma oranina katilmaz."
)
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
HELP_KAYIT = (
    "Bu deger kac kayittan hesaplandi. Kayit sayisi dusukse "
    "sonuc daha az guvenilir."
)
HELP_TIPIK = (
    "Tipik — siralandiginda tam ortadaki deger. Ortalamadan farki, "
    "birkac cok uzun kaydin sayiyi yukari cekmemesi."
)
HELP_SURE = (
    "Telefonun acildigi gorusmelerin ortalama suresi. Tipik de "
    "gosterilir cunku birkac uzun gorusme ortalamayi yukari cekebilir."
)
HELP_BLOK_TOPLAM = (
    "Bu bloktaki ulasilan gorusmelerin sureleri toplami. "
    "Sure yalniz telefonun acildigi gorusmelerden hesaplanir; "
    "ulasilamayan aramalar katilmaz."
)
HELP_BLOK_SURE = (
    "Bu bloktaki ulasilan gorusmelerin ortalama ve tipik suresi. "
    "Sure yalniz telefonun acildigi gorusmelerden hesaplanir; "
    "ulasilamayan aramalar katilmaz."
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
    "giden arama": HELP_ARAMA,
    "arama/gün": HELP_ARAMA,
    "giden arama/gün": HELP_ARAMA,
    "giden arama / gün": HELP_ARAMA,
    "dönüş araması": HELP_DONUS,
    "dönüş araması/gün": HELP_DONUS,
    "gelen arama": HELP_GELEN,
    "gelen arama/gün": HELP_GELEN,
    "ulaşılan": HELP_ULASILAN,
    "ulaşılan/gün": HELP_ULASILAN,
    "ulaşma %": HELP_ULASMA,
    "oran": HELP_ULASMA,
    "aranan lead": HELP_ARANAN_LEAD,
    "giden temas": HELP_GIDEN_TEMAS,
    "dönüşle gelen": HELP_DONUSLE_GELEN,
    "benzersiz ulaşılan": HELP_BENZERSIZ_ULASILAN,
    "randevu": HELP_RANDEVU,
    "randevu/gün": HELP_RANDEVU,
    "katılım %": HELP_KATILIM,
    "ortalama": HELP_SURE,
    "tipik": HELP_TIPIK,
    "ortalama gün": HELP_DONGU,
    "tipik gün": HELP_DONGU,
    "genelde": HELP_TAKE_GENEL,
    "ulaşılanda": HELP_TAKE_ULASILANDA,
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
    "kayıt sayısı": HELP_KAYIT,
    "durum": HELP_HUNI,
    "1.Arama-Ulaşılamadı": HELP_HUNI,
    "2.Arama-Ulaşılamadı": HELP_HUNI,
    "Aging": HELP_HUNI,
    "ciro": HELP_CIRO,
    "satış başına ortalama": HELP_CIRO_ORT,
    "ortalama tutar": HELP_CIRO_ORT,
}

CHART_HELP: dict[str, str] = {
    "arama": HELP_ARAMA,
    "arama_ham": HELP_ARAMA,
    "donus": HELP_DONUS,
    "gelen": HELP_GELEN,
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
        st.caption("dönüşüm 1 Mayıs 2026 sonrası")
    with right:
        st.caption(user.email)
        if st.button("Çıkış", use_container_width=True):
            st.session_state.clear()
            st.rerun()


@st.cache_data(ttl=CACHE_TTL)
def _reps() -> list[tuple[str, str]]:
    return [(r.rep_id, r.full_name) for r in load_reps()]


@st.cache_data(ttl=CACHE_TTL)
def _hourly(rep_id: str | None, start: str, end: str) -> list[dict[str, Any]]:
    return hourly_table(rep_id, DateWindow(date.fromisoformat(start), date.fromisoformat(end)))


@st.cache_data(ttl=CACHE_TTL)
def _team_dip(start: str, end: str) -> dict[str, float | None]:
    return team_reach_and_join(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _reach_break(
    start: str, end: str, rep_id: str | None, by_rep: bool
) -> list[dict[str, Any]]:
    return lead_reach_breakdown(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end)),
        rep_id=rep_id,
        by_rep=by_rep,
    )


@st.cache_data(ttl=CACHE_TTL)
def _board(
    rep_id: str | None, arama_per_lead: float, toplanti_gun: float
) -> dict[str, Any]:
    return workload_board(rep_id, arama_per_lead, toplanti_gun)


@st.cache_data(ttl=CACHE_TTL)
def _talk(start: str, end: str) -> list[dict[str, Any]]:
    return talk_duration_by_rep(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _cycle(start: str, end: str) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    return sales_cycle(DateWindow(date.fromisoformat(start), date.fromisoformat(end)))


@st.cache_data(ttl=CACHE_TTL)
def _take(start: str, end: str) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    return take_rate(DateWindow(date.fromisoformat(start), date.fromisoformat(end)))


@st.cache_data(ttl=CACHE_TTL)
def _source(start: str, end: str) -> list[dict[str, Any]]:
    return source_take_rate(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _path(start: str, end: str) -> list[dict[str, Any]]:
    return path_take_rate(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _funnel(rep_id: str | None, named: bool, start: str, end: str) -> list[dict[str, Any]]:
    return funnel(
        rep_id,
        named=named,
        window=DateWindow(date.fromisoformat(start), date.fromisoformat(end)),
    )


@st.cache_data(ttl=CACHE_TTL)
def _funnel_dropped(start: str, end: str) -> list[dict[str, Any]]:
    return funnel_dropped_by_rep(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _weekly(rep_id: str | None, start: str, end: str) -> list[dict[str, Any]]:
    return weekly_series(
        rep_id, DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _weekly_team(start: str, end: str) -> list[dict[str, Any]]:
    return weekly_team_series(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _rep_snap(rep_id: str, start: str, end: str) -> dict[str, Any]:
    return rep_snapshot(
        rep_id, DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


@st.cache_data(ttl=CACHE_TTL)
def _today_blocks(rep_id: str | None, day: str) -> dict[str, Any]:
    return today_blocks(rep_id, date.fromisoformat(day))


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
def _profiles(start: str, end: str) -> list[dict[str, Any]]:
    return performance_profiles(
        DateWindow(date.fromisoformat(start), date.fromisoformat(end))
    )


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
        use_container_width=True,
        column_config=_col_config(frame),
    )


def _heading(
    title: str,
    help_text: str | None = None,
    window: DateWindow | None = None,
) -> None:
    st.subheader(title, help=help_text)
    if window is not None:
        st.caption(fmt_window(window))


def _keys(window: DateWindow) -> tuple[str, str]:
    return window.start.isoformat(), window.end.isoformat()


def _show_reach_break(rows: list[dict[str, Any]], *, named: bool) -> None:
    """Ulaşma oranı kırılım tablosu. named=False isimsiz toplam."""
    if not rows:
        st.caption("veri yetersiz")
        return
    frame = _df(rows)
    show = pd.DataFrame()
    if named:
        show["temsilci"] = frame["temsilci"]
    show["aranan lead"] = frame["aranan"]
    show["giden temas"] = frame["giden_temas"]
    show["dönüşle gelen"] = frame["donusle_gelen"]
    show["benzersiz ulaşılan"] = frame["ulasilan"]
    show["oran"] = frame["oran"].map(fmt_pct)
    _table(show)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError("tarih bekleniyor")


def _ciro_window() -> DateWindow:
    today = datetime.now(_TZ).date()
    return DateWindow(CIRO_START.date(), today)


def _render_period_controls() -> tuple[DateWindow, date]:
    today = datetime.now(_TZ).date()
    mode = st.radio(
        "Tarih aralığı",
        ("son 30 gün", "son 60 gün", "son 90 gün", "serbest aralık"),
        index=2,
        horizontal=True,
        key="period_mode",
    )
    if mode == "serbest aralık":
        picked = st.date_input(
            "Aralık",
            value=(today - timedelta(days=WINDOW_DAYS), today),
            max_value=today,
            key="period_custom",
        )
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            start, end = _as_date(picked[0]), _as_date(picked[1])
            if start > end:
                start, end = end, start
            window = DateWindow(start, end)
        else:
            window = default_window()
    else:
        span = {"son 30 gün": 30, "son 60 gün": 60, "son 90 gün": 90}[mode]
        window = DateWindow(today - timedelta(days=span), today)
    raw_day = st.date_input(
        "Gün (bloklar)",
        value=today,
        max_value=today,
        key="block_day",
    )
    if isinstance(raw_day, (tuple, list)):
        block_day = _as_date(raw_day[0])
    else:
        block_day = _as_date(raw_day)
    return window, block_day


def _funnel_drop_report(window: DateWindow) -> None:
    start, end = _keys(window)
    dropped = _funnel_dropped(start, end)
    total = sum(int(r["lead"]) for r in dropped)
    print(f"Huni kolon dusen status: {FUNNEL_DROPPED_STATUS}")
    if not dropped:
        print("temsilci kirilimi: 0 lead")
    else:
        for row in dropped:
            print(f"  {row['temsilci']}: {row['lead']}")
        print(f"toplam: {total}")
    st.caption(
        f"{FUNNEL_DROPPED_STATUS} kolon olarak yok. "
        f"{total} lead bu statusde; tabloya tasinmadi, gorunmez."
    )


def _metric_row(items: list[dict[str, Any]]) -> None:
    """Gunluk ozet — buyuk punto. Her satir 4 kolon."""
    cols = st.columns(4)
    for col, item in zip(cols, items[:4]):
        with col:
            _compare(
                str(item["label"]),
                item.get("cur"),
                item.get("prev"),
                pct=bool(item.get("pct", False)),
                duration=bool(item.get("duration", False)),
                help_text=item.get("help_text"),
                empty_label=item.get("empty_label"),
                display=item.get("display"),
            )


def _metric_delta(
    cur: float | None, prev: float | None
) -> tuple[str | None, str]:
    """Streamlit tek ok ekler; isaretli metin gonderme."""
    if cur is None or prev is None:
        return None, "off"
    diff = float(cur) - float(prev)
    if abs(diff) < 0.05:
        return "0", "off"
    return f"{diff:.1f}", "normal"


def _delta_markup(cur: float | None, prev: float | None) -> str:
    """Kompakt rozet: tek ok, artis yesil, dusus kirmizi, sifir gri."""
    if cur is None or prev is None:
        return ":gray[veri yetersiz]"
    diff = float(cur) - float(prev)
    if abs(diff) < 0.05:
        return ":gray[→ 0]"
    if diff > 0:
        return f":green[↑ {diff:.1f}]"
    return f":red[↓ {abs(diff):.1f}]"


def _block_metric(item: dict[str, Any]) -> None:
    """Blok kartı — kucuk punto. Gunluk ozet st.metric buyuk kalir."""
    cur = item.get("cur")
    prev = item.get("prev")
    empty_label = item.get("empty_label")
    if empty_label is not None and cur is None:
        shown = str(empty_label)
        delta_md = ":gray[veri yetersiz]"
    elif item.get("display") is not None:
        shown = str(item["display"])
        delta_md = _delta_markup(cur, prev)
    elif item.get("duration"):
        shown = fmt_duration(cur)
        delta_md = _delta_markup(cur, prev)
    elif item.get("pct"):
        shown = fmt_pct(cur)
        delta_md = _delta_markup(cur, prev)
    else:
        shown = fmt_num(cur)
        delta_md = _delta_markup(cur, prev)
    help_text = item.get("help_text")
    st.caption(str(item["label"]), help=help_text)
    st.markdown(f"**{shown}**")
    st.markdown(delta_md)
    extra = item.get("extra")
    if extra:
        st.caption(str(extra), help=item.get("extra_help"))


def _block_metric_row(items: list[dict[str, Any]]) -> None:
    """Her blok kartında aynı 4 kolon; eksik slot boş kalır."""
    cols = st.columns(4, gap="small")
    for col, item in zip(cols, items[:4]):
        with col:
            _block_metric(item)


def _stat_row(items: list[dict[str, Any]]) -> None:
    """Karsilastirmasiz metrikler; ayni 4 kolon ızgarasi."""
    cols = st.columns(4)
    for col, item in zip(cols, items[:4]):
        with col:
            st.metric(
                str(item["label"]),
                item["value"],
                help=item.get("help"),
            )


def _render_status_bar() -> None:
    from pusula.panel_status import (
        format_block_line,
        format_source_line,
        load_panel_readiness,
    )

    ready = load_panel_readiness()
    st.markdown(format_block_line(ready.blocks))
    failed = [item.label for item in ready.blocks if item.state == "calismadi"]
    if failed:
        st.warning("çalışmadı: " + ", ".join(failed))
    source_text = format_source_line(ready)
    if ready.warn:
        st.warning(source_text)
    else:
        st.info(source_text)


def _fmt_event_ts(value: datetime | None) -> str:
    if value is None:
        return "—"
    dt = value if value.tzinfo is not None else value.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ).strftime("%d.%m.%Y %H:%M")


def _render_block_card(block: dict[str, Any]) -> None:
    kind = str(block.get("kind") or "")
    today = block.get("today") or {}
    avg90 = block.get("avg90") or {}
    with st.container(border=True):
        st.markdown(f"**{block.get('label')}**")
        if kind == "call":
            _block_metric_row(
                [
                    {
                        "label": "giden arama",
                        "cur": today.get("arama"),
                        "prev": avg90.get("arama"),
                        "help_text": HELP_ARAMA,
                    },
                    {
                        "label": "ulaşılan görüşme",
                        "cur": today.get("ulasilan"),
                        "prev": avg90.get("ulasilan"),
                        "help_text": HELP_ULASILAN,
                    },
                    {
                        "label": "ulaşma oranı",
                        "cur": today.get("ulasma_orani"),
                        "prev": avg90.get("ulasma_orani"),
                        "pct": True,
                        "help_text": HELP_ULASMA,
                    },
                    _sure_column(today, avg90),
                ]
            )
            _block_metric_row(
                [
                    {
                        "label": "dönüş araması",
                        "cur": today.get("donus"),
                        "prev": avg90.get("donus"),
                        "help_text": HELP_DONUS,
                    },
                    {
                        "label": "gelen arama",
                        "cur": today.get("gelen"),
                        "prev": avg90.get("gelen"),
                        "help_text": HELP_GELEN,
                    },
                    {
                        "label": "randevu",
                        "cur": today.get("randevu"),
                        "prev": avg90.get("randevu"),
                        "help_text": HELP_RANDEVU,
                    },
                ]
            )
        elif kind == "meeting":
            _block_metric_row(
                [
                    {
                        "label": "randevu",
                        "cur": today.get("randevu"),
                        "prev": avg90.get("randevu"),
                        "help_text": HELP_RANDEVU,
                    },
                    {
                        "label": "katıldı",
                        "cur": today.get("katildi"),
                        "prev": avg90.get("katildi"),
                        "help_text": HELP_KATILIM,
                    },
                    {
                        "label": "katılmadı",
                        "cur": today.get("katilmadi"),
                        "prev": avg90.get("katilmadi"),
                        "help_text": HELP_KATILIM,
                    },
                    {
                        "label": "sonuç girilmedi",
                        "cur": today.get("sonuc_girilmedi"),
                        "prev": avg90.get("sonuc_girilmedi"),
                        "help_text": HELP_KATILIM,
                    },
                ]
            )
            _block_metric_row(
                [
                    {
                        "label": "giden arama",
                        "cur": today.get("arama"),
                        "prev": avg90.get("arama"),
                        "help_text": HELP_ARAMA,
                    },
                    {
                        "label": "ulaşılan görüşme",
                        "cur": today.get("ulasilan"),
                        "prev": avg90.get("ulasilan"),
                        "help_text": HELP_ULASILAN,
                    },
                    {
                        "label": "dönüş araması",
                        "cur": today.get("donus"),
                        "prev": avg90.get("donus"),
                        "help_text": HELP_DONUS,
                    },
                    {
                        "label": "gelen arama",
                        "cur": today.get("gelen"),
                        "prev": avg90.get("gelen"),
                        "help_text": HELP_GELEN,
                    },
                ]
            )
        else:
            _block_metric_row(
                [
                    {
                        "label": "giden arama",
                        "cur": today.get("arama"),
                        "prev": avg90.get("arama"),
                        "help_text": HELP_ARAMA,
                    },
                    {
                        "label": "ulaşılan görüşme",
                        "cur": today.get("ulasilan"),
                        "prev": avg90.get("ulasilan"),
                        "help_text": HELP_ULASILAN,
                    },
                    {
                        "label": "randevu",
                        "cur": today.get("randevu"),
                        "prev": avg90.get("randevu"),
                        "help_text": HELP_RANDEVU,
                    },
                    {
                        "label": "katıldı",
                        "cur": today.get("katildi"),
                        "prev": avg90.get("katildi"),
                        "help_text": HELP_KATILIM,
                    },
                ]
            )
            _block_metric_row(
                [
                    {
                        "label": "dönüş araması",
                        "cur": today.get("donus"),
                        "prev": avg90.get("donus"),
                        "help_text": HELP_DONUS,
                    },
                    {
                        "label": "gelen arama",
                        "cur": today.get("gelen"),
                        "prev": avg90.get("gelen"),
                        "help_text": HELP_GELEN,
                    },
                    _sure_column(today, avg90),
                ]
            )


def _render_bugun(rep_id: str | None, day: date, *, blok_disi: bool) -> None:
    data = _today_blocks(rep_id, day.isoformat())
    _heading(f"Bugün - {fmt_day(day)}", HELP_BUGUN)
    planned: list[dict[str, Any]] = []
    extra: dict[str, Any] | None = None
    for block in data.get("blocks") or []:
        if str(block.get("key")) == "blok_disi":
            if blok_disi:
                extra = block
            continue
        planned.append(block)
    for start in (0, 2):
        pair = planned[start : start + 2]
        if not pair:
            continue
        cols = st.columns(len(pair), gap="small")
        for col, block in zip(cols, pair):
            with col:
                _render_block_card(block)
    if extra is not None:
        _render_block_card(extra)


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


def _sure_column(today: dict[str, Any], avg90: dict[str, Any]) -> dict[str, Any]:
    """Arama blogu 4. kolon: ortalama+tipik, altinda toplam."""
    pair = _fmt_ortalama_tipik(today.get("sure_ort"), today.get("sure_tipik"))
    toplam = today.get("sure_toplam")
    extra = (
        f"toplam {fmt_duration(toplam)}"
        if toplam is not None
        else "toplam veri yetersiz"
    )
    return {
        "label": "görüşme süresi",
        "cur": today.get("sure_ort"),
        "prev": avg90.get("sure_ort"),
        "display": pair,
        "empty_label": "veri yetersiz",
        "help_text": HELP_BLOK_SURE,
        "extra": extra,
        "extra_help": HELP_BLOK_TOPLAM,
    }


def _fmt_ortalama_tipik(
    avg_sec: float | None, tipik_sec: float | None
) -> str | None:
    if avg_sec is None and tipik_sec is None:
        return None
    return (
        f"ortalama {fmt_duration(avg_sec)}, tipik {fmt_duration(tipik_sec)}"
    )


def _compare(
    label: str,
    cur: float | None,
    prev: float | None,
    *,
    pct: bool = False,
    duration: bool = False,
    help_text: str | None = None,
    empty_label: str | None = None,
    display: str | None = None,
) -> None:
    if empty_label is not None and cur is None:
        st.metric(label, empty_label, None, help=help_text)
        return
    if display is not None:
        shown = display
    elif duration:
        shown = fmt_duration(cur)
    elif pct:
        shown = fmt_pct(cur)
    else:
        shown = fmt_num(cur)
    delta, color = _metric_delta(cur, prev)
    if color == "off":
        st.metric(label, shown, delta, delta_color="off", help=help_text)
    else:
        st.metric(label, shown, delta, delta_color="normal", help=help_text)


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
    _heading("Ciro", HELP_CIRO, _ciro_window())
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
    _heading("Ciro", HELP_CIRO, _ciro_window())
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


def _render_profil(window: DateWindow) -> None:
    _heading("Performans profili", window=window)
    st.info(PROFILE_WARNING)
    start, end = _keys(window)
    cards = _profiles(start, end)
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


def render_yonetici(window: DateWindow, block_day: date) -> None:
    reps = _reps()
    options = [("tumu", "tümü")] + reps
    labels = [lab for _, lab in options]
    choice = st.selectbox("Temsilci", labels, key="yonetici_rep")
    rep_id = None
    for rid, lab in options:
        if lab == choice:
            rep_id = None if rid == "tumu" else rid
            break

    start, end = _keys(window)
    conv = conv_window(window)
    _render_bugun(rep_id, block_day, blok_disi=True)

    st.divider()
    _heading("Saatlik", window=window)
    hourly = _hourly(rep_id, start, end)
    hframe = _df(hourly)
    show = hframe.copy()
    show["ulaşma %"] = show["ulasma_orani"].map(fmt_pct)
    show["katılım %"] = show["katilim_orani"].map(fmt_pct)
    _table(
        show[
            [
                "saat",
                "arama",
                "donus",
                "gelen",
                "ulasilan",
                "ulaşma %",
                "randevu",
                "katildi",
                "katılım %",
            ]
        ].rename(
            columns={
                "arama": "giden arama",
                "donus": "dönüş araması",
                "gelen": "gelen arama",
                "ulasilan": "ulaşılan",
                "katildi": "katıldı",
            }
        )
    )
    dip = _team_dip(start, end)
    with st.container(border=True):
        _stat_row(
            [
                {
                    "label": "ekip ulaşma oranı",
                    "value": fmt_pct(dip.get("ulasma_orani")),
                    "help": HELP_ULASMA,
                },
                {
                    "label": "ekip katılım oranı",
                    "value": fmt_pct(dip.get("katilim_orani")),
                    "help": HELP_KATILIM,
                },
                {
                    "label": "ekip dönüş araması",
                    "value": fmt_num(dip.get("donus")),
                    "help": HELP_DONUS,
                },
                {
                    "label": "ekip gelen arama",
                    "value": fmt_num(dip.get("gelen")),
                    "help": HELP_GELEN,
                },
            ]
        )

    _heading("Ulaşma oranı", HELP_ULASMA, window)
    _show_reach_break(_reach_break(start, end, None, True), named=True)

    _heading("Günlük iş yükü (kişi başı)", HELP_ISYUKU, default_window())
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
    with st.container(border=True):
        _stat_row(
            [
                {
                    "label": "planlanan toplam",
                    "value": f"{board['plan_saat']} saat",
                },
                {
                    "label": "gerçekleşen toplam",
                    "value": f"{board['gercek_saat']} saat",
                },
                {
                    "label": "gün doluluk oranı",
                    "value": fmt_pct(board.get("doluluk")),
                    "help": HELP_DOLULUK,
                },
            ]
        )
    st.caption(
        f"plan gerçekleşme (süre) {fmt_pct(board.get('toplam_oran'))} · "
        f"ulaşılamayan arama ort. {fmt_duration(board.get('miss_sn'))} · "
        f"ulaşılan görüşme ort. {fmt_duration(board.get('hit_sn'))} · "
        f"toplantı {int(TOPLANTI_DK)} dk · CRM {CRM_DK_PER_GORUSME} dk/görüşme · "
        f"ölü zaman {fmt_duration(OLU_ZAMAN_SN)}/arama · {int(board.get('workdays') or 0)} iş günü"
    )

    _heading("Görüşme süresi", HELP_SURE, window)
    talk = _df(_talk(start, end))
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

    _heading("Satış döngüsü (gün)", HELP_DONGU, conv)
    cycle_rep, cycle_team = _cycle(start, end)
    with st.container(border=True):
        _stat_row(
            [
                {
                    "label": "ortalama gün",
                    "value": fmt_num(cycle_team.get("ortalama_gun")),
                    "help": HELP_DONGU,
                },
                {
                    "label": "tipik gün",
                    "value": fmt_num(cycle_team.get("medyan_gun")),
                    "help": HELP_TIPIK,
                },
                {
                    "label": "kayıt sayısı",
                    "value": str(cycle_team.get("n") or 0),
                    "help": HELP_KAYIT,
                },
            ]
        )
    cyc = _df(cycle_rep)
    if not cyc.empty:
        cyc["ortalama_gun"] = cyc["ortalama_gun"].map(lambda v: fmt_num(v, 1))
        cyc["medyan_gun"] = cyc["medyan_gun"].map(lambda v: fmt_num(v, 1))
    _table(
        cyc.rename(
            columns={
                "ortalama_gun": "ortalama gün",
                "medyan_gun": "tipik gün",
                "n": "kayıt sayısı",
            }
        )
    )

    _heading("Satışa dönme oranı", HELP_TAKE_GENEL, conv)
    take_rep, take_team = _take(start, end)
    with st.container(border=True):
        _stat_row(
            [
                {
                    "label": "Genelde satışa dönme oranı",
                    "value": fmt_pct(take_team.get("genel")),
                    "help": HELP_TAKE_GENEL,
                },
                {
                    "label": "Ulaşılanda satışa dönme oranı",
                    "value": fmt_pct(take_team.get("ulasilanda")),
                    "help": HELP_TAKE_ULASILANDA,
                },
            ]
        )
    tframe = _df(take_rep)
    tframe["genelde"] = tframe["genel"].map(fmt_pct)
    tframe["ulaşılanda"] = tframe["ulasilanda"].map(fmt_pct)
    _table(tframe[["temsilci", "leads", "genelde", "ulaşılanda"]])

    _heading("Satış kaynağı", HELP_SOURCE, conv)
    src = _df(_source(start, end))
    src["genelde"] = src["genelde"].map(fmt_pct)
    src["ulaşılanda"] = src["ulasilanda"].map(fmt_pct)
    st.markdown("Lead source", help=HELP_SOURCE)
    _table(src[["kaynak", "lead", "satis", "genelde", "ulaşılanda"]])
    path = _df(_path(start, end))
    path["genelde"] = path["genelde"].map(fmt_pct)
    path["ulaşılanda"] = path["ulasilanda"].map(fmt_pct)
    st.markdown("Satışa giden yol", help=HELP_YOL)
    _table(path[["yol", "lead", "satis", "genelde", "ulaşılanda"]])

    _heading("Huni", HELP_HUNI, window)
    fun = _funnel(rep_id, True, start, end)
    _table(_df(fun))
    _funnel_drop_report(window)

    st.divider()
    _render_ciro_yonetici(rep_id)
    st.divider()
    _render_profil(window)
    st.divider()

    _heading("Haftalık gelişim", window=window)
    team_w = _weekly_team(start, end)
    if rep_id is None:
        _render_weekly_charts(team_w, None, kisi_basi=True)
    else:
        rep_w = _weekly(rep_id, start, end)
        _render_weekly_charts(team_w, rep_w, kisi_basi=True)


def _render_weekly_charts(
    team_rows: list[dict[str, Any]],
    rep_rows: list[dict[str, Any]] | None,
    *,
    kisi_basi: bool,
    take: bool = True,
) -> None:
    specs: list[tuple[str, str, bool]] = [
        ("arama", "giden arama (kişi başı)" if kisi_basi else "giden arama", False),
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
        n_weeks = len(src)
        st.caption(
            f"son hafta vs {n_weeks} haftalık ortalama: {_week_delta(src, key)}"
        )
    if take:
        _render_donusum_weekly(rep_rows if rep_rows is not None else team_rows)


def _render_donusum_weekly(rows: list[dict[str, Any]]) -> None:
    frame = _df(rows)
    parts: list[pd.DataFrame] = []
    for key, label in (
        ("take_genel", "genelde"),
        ("take_ulasilanda", "ulaşılanda"),
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
    n_weeks = len(rows)
    st.caption(
        f"genelde son hafta vs {n_weeks} haftalık ortalama: "
        f"{_week_delta(rows, 'take_genel')}"
    )
    st.caption(
        f"ulaşılanda son hafta vs {n_weeks} haftalık ortalama: "
        f"{_week_delta(rows, 'take_ulasilanda')}"
    )


def render_temsilci(
    window: DateWindow, block_day: date, locked_rep_id: str | None = None
) -> None:
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
    start, end = _keys(window)
    snap = _rep_snap(rep_id, start, end)
    cur = snap["current"]
    prev = snap["previous"]

    _render_bugun(rep_id, block_day, blok_disi=False)
    st.divider()
    with st.container(border=True):
        st.markdown("**Günlük özet**")
        st.caption(
            f"{fmt_window(window)}, önceki {window.days} günle kıyas"
        )
        _metric_row(
            [
                {
                    "label": "ulaşma oranı",
                    "cur": cur.get("ulasma_orani"),
                    "prev": prev.get("ulasma_orani"),
                    "pct": True,
                    "help_text": HELP_ULASMA,
                },
                {
                    "label": "giden arama / gün",
                    "cur": cur.get("arama_gun"),
                    "prev": prev.get("arama_gun"),
                    "help_text": HELP_ARAMA,
                },
                {
                    "label": "ulaşılan / gün",
                    "cur": cur.get("ulasilan_gun"),
                    "prev": prev.get("ulasilan_gun"),
                    "help_text": HELP_ULASILAN,
                },
                {
                    "label": "görüşme ortalama",
                    "cur": cur.get("ortalama_sn"),
                    "prev": prev.get("ortalama_sn"),
                    "duration": True,
                    "help_text": HELP_SURE,
                },
            ]
        )
        _metric_row(
            [
                {
                    "label": "dönüş araması / gün",
                    "cur": cur.get("donus_gun"),
                    "prev": prev.get("donus_gun"),
                    "help_text": HELP_DONUS,
                },
                {
                    "label": "gelen arama / gün",
                    "cur": cur.get("gelen_gun"),
                    "prev": prev.get("gelen_gun"),
                    "help_text": HELP_GELEN,
                },
                {
                    "label": "dönüş araması",
                    "cur": cur.get("donus"),
                    "prev": prev.get("donus"),
                    "help_text": HELP_DONUS,
                },
                {
                    "label": "gelen arama",
                    "cur": cur.get("gelen"),
                    "prev": prev.get("gelen"),
                    "help_text": HELP_GELEN,
                },
            ]
        )
        _metric_row(
            [
                {
                    "label": "görüşme tipik",
                    "cur": cur.get("medyan_sn"),
                    "prev": prev.get("medyan_sn"),
                    "duration": True,
                    "help_text": HELP_TIPIK,
                },
                {
                    "label": "gelen lead",
                    "cur": cur.get("lead"),
                    "prev": prev.get("lead"),
                    "help_text": HELP_LEAD,
                },
                {
                    "label": "randevu",
                    "cur": cur.get("randevu"),
                    "prev": prev.get("randevu"),
                    "help_text": HELP_RANDEVU,
                },
                {
                    "label": "katıldı",
                    "cur": cur.get("katildi"),
                    "prev": prev.get("katildi"),
                    "help_text": HELP_KATILIM,
                },
            ]
        )
        _metric_row(
            [
                {
                    "label": "katılım oranı",
                    "cur": cur.get("katilim_orani"),
                    "prev": prev.get("katilim_orani"),
                    "pct": True,
                    "help_text": HELP_KATILIM,
                },
                {
                    "label": "randevu / gün",
                    "cur": cur.get("randevu_gun"),
                    "prev": prev.get("randevu_gun"),
                    "help_text": HELP_RANDEVU,
                },
                {
                    "label": "toplantı / gün",
                    "cur": cur.get("toplanti_gun"),
                    "prev": prev.get("toplanti_gun"),
                    "help_text": HELP_ISYUKU,
                },
                {
                    "label": "CRM kayıt / gün",
                    "cur": cur.get("crm_gun"),
                    "prev": prev.get("crm_gun"),
                    "help_text": HELP_ISYUKU,
                },
            ]
        )
        _metric_row(
            [
                {
                    "label": "ulaşılan görüşmenin randevuya dönme oranı",
                    "cur": cur.get("temas_randevu_orani"),
                    "prev": prev.get("temas_randevu_orani"),
                    "pct": True,
                },
            ]
        )
        st.caption(f"CRM kayıt tahmini {CRM_DK_PER_GORUSME} dk/görüşme")

    _heading("Ulaşma oranı", HELP_ULASMA, window)
    _show_reach_break(_reach_break(start, end, rep_id, False), named=False)

    _heading("Saatlik giden arama ve ulaşma", HELP_ULASMA, window)
    hourly = _hourly(rep_id, start, end)
    hframe = _df(hourly)
    show = hframe[["saat", "arama", "donus", "gelen", "ulasilan", "ulasma_orani"]].copy()
    show["ulaşma %"] = show["ulasma_orani"].map(fmt_pct)
    _table(
        show[["saat", "arama", "donus", "gelen", "ulasilan", "ulaşma %"]].rename(
            columns={
                "arama": "giden arama",
                "donus": "dönüş araması",
                "gelen": "gelen arama",
                "ulasilan": "ulaşılan",
            }
        )
    )

    st.divider()
    _render_ciro_temsilci(rep_id)
    st.divider()

    _heading("Haftalık gelişim", window=window)
    rows = _weekly(rep_id, start, end)
    frame = _df(rows)
    if frame.empty:
        st.caption("veri yetersiz")
        return
    frame["seri"] = "kendisi"
    n_weeks = len(rows)
    for key, title, is_pct in (
        ("arama_ham", "giden arama", False),
        ("ulasma_orani", "ulaşma oranı", True),
        ("randevu", "randevu", False),
        ("katilim_orani", "katılım oranı", True),
    ):
        st.markdown(title, help=CHART_HELP.get(key))
        _line_chart(frame[["hafta", key, "seri"]], key, title, is_pct=is_pct)
        src_key = key
        st.caption(
            f"son hafta vs {n_weeks} haftalık ortalama: {_week_delta(rows, src_key)}"
        )


def render_ekip(window: DateWindow, block_day: date) -> None:
    st.caption("isim yok. operasyon geneli.")
    start, end = _keys(window)
    conv = conv_window(window)
    _render_bugun(None, block_day, blok_disi=False)
    st.divider()
    _heading("Ulaşma oranı", HELP_ULASMA, window)
    _show_reach_break(_reach_break(start, end, None, False), named=False)
    _heading("Toplantı etkisi", HELP_TOPLANTI, conv)
    path = _df(_path(start, end))
    path["genelde"] = path["genelde"].map(fmt_pct)
    path["ulaşılanda"] = path["ulasilanda"].map(fmt_pct)
    _table(path[["yol", "lead", "satis", "genelde", "ulaşılanda"]])

    _heading("Saatlik katılım oranı", HELP_KATILIM, window)
    hourly = _hourly(None, start, end)
    hframe = _df(hourly)
    kat = hframe[["saat", "randevu", "katildi", "katilim_orani"]].copy()
    kat["katılım %"] = kat["katilim_orani"].map(fmt_pct)
    _table(
        kat[["saat", "randevu", "katildi", "katılım %"]].rename(
            columns={"katildi": "katıldı"}
        )
    )

    _heading("Saatlik ulaşma oranı", HELP_ULASMA, window)
    ulas = hframe[["saat", "arama", "donus", "gelen", "ulasilan", "ulasma_orani"]].copy()
    ulas["ulaşma %"] = ulas["ulasma_orani"].map(fmt_pct)
    _table(
        ulas[["saat", "arama", "donus", "gelen", "ulasilan", "ulaşma %"]].rename(
            columns={
                "arama": "giden arama",
                "donus": "dönüş araması",
                "gelen": "gelen arama",
                "ulasilan": "ulaşılan",
            }
        )
    )

    _heading("Huni", HELP_HUNI, window)
    _table(_df(_funnel(None, False, start, end)))
    _funnel_drop_report(window)


def main() -> None:
    st.set_page_config(page_title="Pusula", page_icon="P", layout="wide")
    _apply_secrets()
    user = _session_user()
    if user is None:
        _render_login()
        return
    _require_env()
    with st.container():
        _render_header(user)
        _render_status_bar()
        window, block_day = _render_period_controls()
        if user.role == "admin":
            tab_y, tab_t, tab_e = st.tabs(["Yönetici", "Temsilci", "Ekip"])
            with tab_y:
                render_yonetici(window, block_day)
            with tab_t:
                render_temsilci(window, block_day)
            with tab_e:
                render_ekip(window, block_day)
        else:
            render_temsilci(window, block_day, locked_rep_id=user.rep_id)
        st.caption(f"Veriler son guncelleme: {_fmt_event_ts(_latest_event())}")


if __name__ == "__main__":
    main()
