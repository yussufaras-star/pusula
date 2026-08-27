"""Pusula operasyon paneli — Streamlit.

Üç katman: yönetici, temsilci, ekip ekranı (isimsiz).
Kimlik doğrulama yok; iç kullanım. Salt okuma.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

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


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _table(frame: pd.DataFrame) -> None:
    st.dataframe(frame, hide_index=True, use_container_width=False)


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


def _compare(label: str, cur: float | None, prev: float | None, *, pct: bool = False) -> None:
    fmt = fmt_pct if pct else fmt_num
    st.metric(label, fmt(cur), arrow(cur, prev))


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
    c1.metric("ekip ulaşma oranı", fmt_pct(dip.get("ulasma_orani")))
    c2.metric("ekip katılım oranı", fmt_pct(dip.get("katilim_orani")))

    st.subheader("Günlük iş yükü (kişi başı)")
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

    st.subheader("Görüşme süresi")
    talk = _df(_talk())
    talk_show = talk.rename(
        columns={"ortalama_sn": "ortalama sn", "medyan_sn": "medyan sn"}
    )
    _table(talk_show)

    st.subheader("Satış döngüsü (gün)")
    cycle_rep, cycle_team = _cycle()
    st.caption(
        f"operasyon: ortalama {fmt_num(cycle_team.get('ortalama_gun'))} · "
        f"medyan {fmt_num(cycle_team.get('medyan_gun'))} · n={cycle_team.get('n')}"
    )
    cyc = _df(cycle_rep)
    if not cyc.empty:
        cyc["ortalama_gun"] = cyc["ortalama_gun"].map(lambda v: fmt_num(v, 1))
        cyc["medyan_gun"] = cyc["medyan_gun"].map(lambda v: fmt_num(v, 1))
    _table(
        cyc.rename(
            columns={"ortalama_gun": "ortalama gün", "medyan_gun": "medyan gün"}
        )
    )

    st.subheader("Take rate")
    take_rep, take_team = _take()
    st.caption(
        f"operasyon genel {fmt_pct(take_team.get('genel'))} · "
        f"ulaşılanda {fmt_pct(take_team.get('ulasilanda'))}"
    )
    tframe = _df(take_rep)
    tframe["genel"] = tframe["genel"].map(fmt_pct)
    tframe["ulasilanda"] = tframe["ulasilanda"].map(fmt_pct)
    _table(tframe.rename(columns={"ulasilanda": "ulaşılanda"}))

    st.subheader("Satış kaynağı")
    src = _df(_source())
    src["take_rate"] = src["take_rate"].map(fmt_pct)
    st.markdown("Lead source")
    _table(src)
    path = _df(_path())
    path["take_rate"] = path["take_rate"].map(fmt_pct)
    st.markdown("Satışa giden yol")
    _table(path)

    st.subheader("Huni")
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
        st.markdown(title)
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
        _compare("ulaşma oranı", cur.get("ulasma_orani"), prev.get("ulasma_orani"), pct=True)
        _compare("arama / gün", cur.get("arama_gun"), prev.get("arama_gun"))
    with c2:
        _compare("ulaşılan / gün", cur.get("ulasilan_gun"), prev.get("ulasilan_gun"))
        _compare("görüşme ortalama sn", cur.get("ortalama_sn"), prev.get("ortalama_sn"))
    with c3:
        _compare("görüşme medyan sn", cur.get("medyan_sn"), prev.get("medyan_sn"))
        _compare("gelen lead", cur.get("lead"), prev.get("lead"))

    d1, d2, d3 = st.columns(3)
    with d1:
        _compare("randevu", cur.get("randevu"), prev.get("randevu"))
    with d2:
        _compare("katıldı", cur.get("katildi"), prev.get("katildi"))
    with d3:
        _compare("katılım oranı", cur.get("katilim_orani"), prev.get("katilim_orani"), pct=True)

    w1, w2, w3 = st.columns(3)
    with w1:
        _compare("randevu / gün", cur.get("randevu_gun"), prev.get("randevu_gun"))
    with w2:
        _compare("toplantı / gün", cur.get("toplanti_gun"), prev.get("toplanti_gun"))
    with w3:
        _compare("CRM kayıt / gün", cur.get("crm_gun"), prev.get("crm_gun"))
    st.caption(f"CRM kayıt tahmini {CRM_DK_PER_ARAMA} dk/arama")

    _compare(
        "ulaşılan görüşmenin randevuya dönme oranı",
        cur.get("temas_randevu_orani"),
        prev.get("temas_randevu_orani"),
        pct=True,
    )

    st.subheader("Saatlik arama ve ulaşma")
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
        st.markdown(title)
        _line_chart(frame[["hafta", key, "seri"]], key, title, is_pct=is_pct)
        src_key = key
        st.caption(f"son hafta vs 12 haftalık ortalama: {_week_delta(rows, src_key)}")


def render_ekip() -> None:
    st.caption("isim yok. operasyon geneli.")
    st.subheader("Toplantı etkisi")
    path = _df(_path())
    path["take_rate"] = path["take_rate"].map(fmt_pct)
    _table(path)

    st.subheader("Saatlik katılım oranı")
    hourly = _hourly(None)
    hframe = _df(hourly)
    kat = hframe[["saat", "randevu", "katildi", "katilim_orani"]].copy()
    kat["katılım %"] = kat["katilim_orani"].map(fmt_pct)
    _table(
        kat[["saat", "randevu", "katildi", "katılım %"]].rename(
            columns={"katildi": "katıldı"}
        )
    )

    st.subheader("Saatlik ulaşma oranı")
    ulas = hframe[["saat", "arama", "ulasilan", "ulasma_orani"]].copy()
    ulas["ulaşma %"] = ulas["ulasma_orani"].map(fmt_pct)
    _table(
        ulas[["saat", "arama", "ulasilan", "ulaşma %"]].rename(
            columns={"ulasilan": "ulaşılan"}
        )
    )

    st.subheader("Huni")
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


if __name__ == "__main__":
    main()
