"""Pusula operasyon paneli — Streamlit.

Üç katman: yönetici, temsilci, ekip ekranı (isimsiz).
Kimlik doğrulama yok; iç kullanım. Salt okuma.
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

from pusula.panel_data import (
    CRM_DK_PER_ARAMA,
    WINDOW_DAYS,
    arrow,
    daily_workload,
    fmt_num,
    fmt_pct,
    funnel,
    hourly_table,
    latest_event_created_at,
    load_reps,
    mean,
    path_take_rate,
    rep_snapshot,
    sales_cycle,
    source_take_rate,
    take_rate,
    talk_duration_by_rep,
    team_reach_and_join,
    weekly_series,
    weekly_team_series,
)

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
    "Yapilan cevirme sayisi. Karsi taraf acmasa bile sayilir. "
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
    "Lead'lerin kacinin satisa dondugu. Satis isareti, lead'in "
    "contact kaydina donmesidir. 1 Mayis 2026 sonrasi atanan "
    "lead'ler, mevcut musteriler haric."
)
HELP_TAKE_ULASILANDA = (
    "En az bir kez telefonda konusulmus lead'lerin kacinin "
    "satisa dondugu."
)
HELP_DONGU = (
    "Lead'in temsilciye atanmasindan satisa donmesine kadar "
    "gecen gun sayisi. Yalniz satisa donenler hesaba katilir."
)
HELP_SURE = (
    "Telefonun acildigi gorusmelerin ortalama suresi. Medyan da "
    "gosterilir cunku birkac uzun gorusme ortalamayi yukari cekebilir."
)
HELP_ISYUKU = (
    "Kisi basi gunluk ortalama. Son 90 gunun pazar disi is "
    "gunlerine bolunmustur."
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

COL_HELP: dict[str, str] = {
    "arama": HELP_ARAMA,
    "arama/gün": HELP_ARAMA,
    "ulaşılan": HELP_ULASILAN,
    "ulaşılan/gün": HELP_ULASILAN,
    "ulaşma %": HELP_ULASMA,
    "randevu": HELP_RANDEVU,
    "randevu/gün": HELP_RANDEVU,
    "katılım %": HELP_KATILIM,
    "ortalama sn": HELP_SURE,
    "medyan sn": HELP_SURE,
    "ortalama gün": HELP_DONGU,
    "medyan gün": HELP_DONGU,
    "genel": HELP_TAKE_GENEL,
    "ulaşılanda": HELP_TAKE_ULASILANDA,
    "take_rate": HELP_TAKE_GENEL,
    "kaynak": HELP_SOURCE,
    "yol": HELP_YOL,
    "durum": HELP_HUNI,
    "1.Arama-Ulaşılamadı": HELP_HUNI,
    "2.Arama-Ulaşılamadı": HELP_HUNI,
    "3.Arama-Ulaşılamadı": HELP_HUNI,
    "Aging": HELP_HUNI,
}

CHART_HELP: dict[str, str] = {
    "arama": HELP_ARAMA,
    "arama_ham": HELP_ARAMA,
    "ulasma_orani": HELP_ULASMA,
    "randevu": HELP_RANDEVU,
    "katilim_orani": HELP_KATILIM,
    "take_rate": HELP_TAKE_GENEL,
}


def _require_env() -> None:
    if not (os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")):
        st.error("DATABASE_URL_POOLED / DATABASE_URL yok")
        st.stop()


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
def _workload() -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    return daily_workload()


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
def _latest_event() -> datetime | None:
    return latest_event_created_at()


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


def _fmt_event_ts(value: datetime | None) -> str:
    if value is None:
        return "—"
    dt = value if value.tzinfo is not None else value.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ).strftime("%d.%m.%Y %H:%M")


def _line_chart(
    frame: pd.DataFrame,
    y_col: str,
    title: str,
    *,
    is_pct: bool = False,
) -> None:
    """NaN = boşluk (sıfır değil)."""
    import altair as alt

    data = frame.copy()
    if data.empty or y_col not in data.columns:
        st.caption("veri yetersiz")
        return
    y_enc = alt.Y(y_col, title=title, type="quantitative")
    chart = (
        alt.Chart(data)
        .mark_line(point=True, invalid="break-paths-show-domains")
        .encode(
            x=alt.X("hafta:T", title="hafta"),
            y=y_enc,
            color=alt.Color("seri:N", title=""),
            tooltip=["hafta:T", "seri:N", y_col],
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
    help_text: str | None = None,
) -> None:
    fmt = fmt_pct if pct else fmt_num
    st.metric(label, fmt(cur), arrow(cur, prev), help=help_text)


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
    work, extra = _workload()
    wframe = _df(work)
    _table(
        wframe.rename(
            columns={
                "arama": "arama/gün",
                "arama_dk": "arama dk",
                "ulasilan": "ulaşılan/gün",
                "ulasilan_dk": "ulaşılan dk",
                "randevu": "randevu/gün",
                "randevu_dk": "randevu dk",
                "toplanti": "toplantı/gün",
                "toplanti_dk": "toplantı dk",
                "crm": "CRM kayıt/gün",
                "crm_dk": "CRM dk",
            }
        )
    )
    st.caption(
        f"ulaşılamayan çağrı ort. {fmt_num(extra.get('ulasilamayan_ort_sn'), 1)} sn · "
        f"ulaşılan görüşme medyan {fmt_num(extra.get('ulasilan_medyan_sn'), 0)} sn · "
        f"CRM kayıt tahmini {CRM_DK_PER_ARAMA} dk/arama · "
        f"{int(extra.get('workdays') or 0)} iş günü"
    )

    _heading("Görüşme süresi", HELP_SURE)
    talk = _df(_talk())
    talk_show = talk.rename(
        columns={"ortalama_sn": "ortalama sn", "medyan_sn": "medyan sn"}
    )
    _table(talk_show)

    _heading("Satış döngüsü (gün)", HELP_DONGU)
    cycle_rep, cycle_team = _cycle()
    d1, d2, d3 = st.columns(3)
    d1.metric(
        "ortalama gün",
        fmt_num(cycle_team.get("ortalama_gun")),
        help=HELP_DONGU,
    )
    d2.metric(
        "medyan gün",
        fmt_num(cycle_team.get("medyan_gun")),
        help=HELP_DONGU,
    )
    d3.metric("n", str(cycle_team.get("n") or 0))
    cyc = _df(cycle_rep)
    if not cyc.empty:
        cyc["ortalama_gun"] = cyc["ortalama_gun"].map(lambda v: fmt_num(v, 1))
        cyc["medyan_gun"] = cyc["medyan_gun"].map(lambda v: fmt_num(v, 1))
    _table(
        cyc.rename(
            columns={"ortalama_gun": "ortalama gün", "medyan_gun": "medyan gün"}
        )
    )

    _heading("Take rate", HELP_TAKE_GENEL)
    take_rep, take_team = _take()
    t1, t2 = st.columns(2)
    t1.metric(
        "operasyon genel",
        fmt_pct(take_team.get("genel")),
        help=HELP_TAKE_GENEL,
    )
    t2.metric(
        "ulaşılanda",
        fmt_pct(take_team.get("ulasilanda")),
        help=HELP_TAKE_ULASILANDA,
    )
    tframe = _df(take_rep)
    tframe["genel"] = tframe["genel"].map(fmt_pct)
    tframe["ulasilanda"] = tframe["ulasilanda"].map(fmt_pct)
    _table(tframe.rename(columns={"ulasilanda": "ulaşılanda"}))

    _heading("Satış kaynağı", HELP_SOURCE)
    src = _df(_source())
    src["take_rate"] = src["take_rate"].map(fmt_pct)
    st.markdown("Lead source", help=HELP_SOURCE)
    _table(src)
    path = _df(_path())
    path["take_rate"] = path["take_rate"].map(fmt_pct)
    st.markdown("Satışa giden yol", help=HELP_YOL)
    _table(path)

    _heading("Huni", HELP_HUNI)
    fun = _funnel(rep_id, True)
    _table(_df(fun))

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
    if take:
        specs.append(("take_rate", "take rate", True))
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


def render_temsilci() -> None:
    reps = _reps()
    if not reps:
        st.write("aktif satış temsilcisi yok")
        return
    names = {name: rid for rid, name in reps}
    choice = st.selectbox("Temsilci", list(names), key="temsilci_sel")
    rep_id = names[choice]
    snap = _rep_snap(rep_id)
    cur = snap["current"]
    prev = snap["previous"]

    st.caption(f"son {WINDOW_DAYS} gün, önceki {WINDOW_DAYS} günle kıyas")
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
            "görüşme ortalama sn",
            cur.get("ortalama_sn"),
            prev.get("ortalama_sn"),
            help_text=HELP_SURE,
        )
    with c3:
        _compare(
            "görüşme medyan sn",
            cur.get("medyan_sn"),
            prev.get("medyan_sn"),
            help_text=HELP_SURE,
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
    st.caption(f"CRM kayıt tahmini {CRM_DK_PER_ARAMA} dk/arama")

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
    path["take_rate"] = path["take_rate"].map(fmt_pct)
    _table(path)

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
    _require_env()
    st.title("Pusula")
    st.caption(f"son {WINDOW_DAYS} gün · dönüşüm 1 Mayıs 2026 sonrası")
    tab_y, tab_t, tab_e = st.tabs(["Yönetici", "Temsilci", "Ekip"])
    with tab_y:
        render_yonetici()
    with tab_t:
        render_temsilci()
    with tab_e:
        render_ekip()
    st.caption(f"Veriler son guncelleme: {_fmt_event_ts(_latest_event())}")


if __name__ == "__main__":
    main()
