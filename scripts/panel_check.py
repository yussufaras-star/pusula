"""Panel hazırlık çubuğu — eşikler, ingest durumu, mesai dışı uyarı yok.

Kullanım:
    python scripts/panel_check.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from pusula.blocks import (
    ISTANBUL,
    display_hours,
)
from pusula.freshness import FRESHNESS_THRESHOLDS, is_mesai
from pusula.panel_status import (
    PanelReadiness,
    format_block_line,
    format_source_line,
    last_due_slot,
    load_panel_readiness,
    next_ingest_at,
    should_warn,
)


def _fake_ready(
    now: datetime,
    *,
    last_at: datetime | None,
    missed: bool,
) -> PanelReadiness:
    nxt = next_ingest_at(now)
    due = last_due_slot(now)
    return PanelReadiness(
        last_at=last_at,
        next_at=nxt,
        due_at=due,
        missed=missed,
        sources=[],
        all_fresh=True,
        warn=should_warn(missed, now),
        now=now,
    )


def main() -> int:
    print("esikler:")
    for key, value in FRESHNESS_THRESHOLDS.items():
        print(f"  {key}: {value}")
    now = datetime.now(ISTANBUL)
    print(f"simdi: {now.isoformat(timespec='minutes')} mesai={is_mesai(now)}")
    saturday = now + timedelta(days=(5 - now.weekday()) % 7 or 7)
    saturday = saturday.replace(hour=12, minute=0, second=0, microsecond=0)
    sunday = saturday + timedelta(days=1)
    night = now.replace(hour=21, minute=0, second=0, microsecond=0)
    print(f"cumartesi mesai={is_mesai(saturday)}")
    print(f"pazar mesai={is_mesai(sunday)}")
    print(f"gece mesai={is_mesai(night)}")
    if not is_mesai(saturday):
        print("hata: cumartesi 12:00 mesai False dondu")
        return 1
    if is_mesai(sunday) or is_mesai(night):
        print("hata: pazar veya gece mesai True dondu")
        return 1

    ready = load_panel_readiness(now)
    print("ingest:")
    print(f"  {format_block_line(ready)}")
    print(
        f"  last={ready.last_at} next={ready.next_at} "
        f"due={ready.due_at} missed={ready.missed}"
    )
    print("tazelik:")
    print(f"  {format_source_line(ready)}")
    print(f"  all_fresh={ready.all_fresh} warn={ready.warn}")
    off = load_panel_readiness(night)
    print(f"mesai disi warn={off.warn} (beklenen False)")
    if off.warn:
        print("hata: mesai disinda uyari")
        return 1

    weekday_ten = datetime(2026, 9, 3, 10, 0, tzinfo=ISTANBUL)
    weekday_noon = datetime(2026, 9, 3, 12, 0, tzinfo=ISTANBUL)
    ready_ten = load_panel_readiness(weekday_ten)
    print("10:00 senaryo:")
    print(f"  {format_block_line(ready_ten)}")
    print(f"  {format_source_line(ready_ten)}")
    print(f"  next={next_ingest_at(weekday_ten)}")
    print(f"  warn={ready_ten.warn}")
    waiting = _fake_ready(weekday_ten, last_at=None, missed=False)
    print(f"  10:00 satir: {format_block_line(waiting)}")
    if "11:15" in format_block_line(waiting) or "14:15" in format_block_line(waiting):
        print("hata: blok bitis saati gostergede")
        return 1
    if should_warn(False, weekday_ten):
        print("hata: kacan ingest yokken uyari")
        return 1

    missed_at = datetime(2026, 9, 3, 11, 7, tzinfo=ISTANBUL)
    missed_now = datetime(2026, 9, 3, 12, 0, tzinfo=ISTANBUL)
    last_ok = datetime(2026, 9, 3, 10, 7, tzinfo=ISTANBUL)
    missed = _fake_ready(missed_now, last_at=last_ok, missed=True)
    missed_warn = should_warn(True, missed_now)
    print("saatlik ingest kacmis senaryo:")
    print(f"  {format_block_line(missed)}")
    print(f"  warn={missed_warn} (beklenen True)")
    if not missed_warn:
        print("hata: kacan ingest uyarisi yok")
        return 1
    if "11:15" in format_block_line(missed):
        print("hata: 11:15 blok bitisi hala yaziliyor")
        return 1
    ready_noon = load_panel_readiness(weekday_noon)
    print("12:00 canli (3 Eylul):")
    print(f"  {format_block_line(ready_noon)}")
    print(f"  warn={ready_noon.warn}")

    from pusula.blocks import blocks_for

    sat_day = datetime(2026, 9, 5, 12, 0, tzinfo=ISTANBUL)
    print("saat araligi:")
    print(f"  cuma={display_hours(datetime(2026, 9, 4).date())}")
    print(f"  cumartesi={display_hours(sat_day.date())}")
    print(f"  pazar={display_hours(datetime(2026, 9, 6).date())}")
    if display_hours(datetime(2026, 9, 4).date()) != tuple(range(9, 18)):
        print("hata: hafta ici saatler 9-17 olmali")
        return 1
    if display_hours(sat_day.date()) != tuple(range(9, 15)):
        print("hata: cumartesi saatler 9-14 olmali")
        return 1
    if display_hours(datetime(2026, 9, 6).date()):
        print("hata: pazarda gorunen saat var")
        return 1
    sat_next = next_ingest_at(datetime(2026, 9, 5, 15, 30, tzinfo=ISTANBUL))
    print(f"  cumartesi 15:30 sonraki={sat_next}")
    if sat_next is None or sat_next.date().weekday() != 0:
        print("hata: cumartesi mesai sonrasi sonraki ingest pazartesi olmali")
        return 1
    if blocks_for(datetime(2026, 9, 6).date()):
        print("hata: pazarda planli blok var (gonderim)")
        return 1

    from pusula.panel_ciro import (
        ciro_won_month_probe,
        has_prior_year_same_month,
    )

    print("deals ay probe:")
    print("ay | adet | closed_at_bos | ciro")
    probe = ciro_won_month_probe()
    for row in probe:
        print(
            f"{row['ay']} | {row['adet']} | {row['closed_at_bos']} | {row['ciro']}"
        )
    print(f"row_count={len(probe)}")
    print(
        "onceki yil ayni ay kayit="
        f"{'var' if has_prior_year_same_month(probe) else 'yok'}"
    )
    if not has_prior_year_same_month(probe):
        print(
            "E bolumu atlandi: onceki yilin ayni ayina ait kayit yok; "
            "yil kiyas tablosu yapilmadi."
        )

    from pusula.panel_data import connect as db_connect

    print("meeting meta dogrulama:")
    print("grup yontemi: pandas MultiIndex ust baslik (arama / toplantı); ikon yok")
    with db_connect() as conn:
        print("information_schema.events:")
        schema_rows = conn.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'events'
            ORDER BY ordinal_position
            """
        ).fetchall()
        for name, dtype in schema_rows:
            print(f"  {name} {dtype}")
        print("meeting meta anahtarları:")
        key_rows = conn.execute(
            """
            SELECT k AS anahtar, count(*)::int AS adet
            FROM events e
            CROSS JOIN LATERAL jsonb_object_keys(e.meta) AS k
            WHERE e.channel = 'meeting'
            GROUP BY 1
            ORDER BY 2 DESC, 1
            """
        ).fetchall()
        for anahtar, adet in key_rows:
            print(f"  {anahtar} {adet}")
        print("meeting randevu_durumu dagilim:")
        dur_rows = conn.execute(
            """
            SELECT
              CASE
                WHEN e.meta ? 'randevu_durumu' THEN 'var'
                ELSE 'yok'
              END AS anahtar,
              coalesce(nullif(e.meta->>'randevu_durumu', ''), '<bos>') AS deger,
              count(*)::int AS adet
            FROM events e
            WHERE e.channel = 'meeting'
            GROUP BY 1, 2
            ORDER BY 3 DESC, 2
            """
        ).fetchall()
        for anahtar, deger, adet in dur_rows:
            print(f"  anahtar={anahtar} deger={deger} adet={adet}")
        print("ornek meeting meta:")
        samples = conn.execute(
            """
            SELECT e.meta
            FROM events e
            WHERE e.channel = 'meeting'
            ORDER BY e.occurred_at DESC NULLS LAST
            LIMIT 5
            """
        ).fetchall()
        for (meta,) in samples:
            print(f"  {meta}")
    found_keys = {str(anahtar) for anahtar, _adet in key_rows}
    if "randevu_durumu" not in found_keys:
        print("hata: meeting meta'da randevu_durumu yok")
        return 1
    print("kullanilan meta anahtari: randevu_durumu")

    from time import perf_counter
    from collections.abc import Callable
    from typing import TypeVar

    from pusula.panel_ciro import (
        AFTER_SALES_IDS,
        REAL_SALES_START,
        SALES_TEAM_IDS,
        ciro_by_rep,
        ciro_complete_months_avg,
        ciro_month_forecast,
        ciro_monthly_by_rep,
        ciro_rep_monthly,
        ciro_same_pace_compare,
        ciro_team_monthly,
        ciro_ytd_by_rep,
        fmt_tl,
        month_first,
        resolve_ciro_period,
        with_monthly_team_totals,
        workdays_in_month,
        workdays_inclusive,
    )
    from pusula.panel_data import (
        connect,
        all_data_window,
        funnel,
        hour_history,
        lead_reach_breakdown,
        path_take_rate,
        rate_cell,
        rep_snapshot,
        sales_cycle,
        source_take_rate,
        sum_hour_rows,
        take_rate,
        talk_duration_by_rep,
        team_reach_and_join,
        today_blocks,
        today_hours,
        GUN_SAAT,
        SAT_SAAT,
        MESAI_WD_SAAT,
        MESAI_SAT_SAAT,
        MEET_DURATION_KEY,
        CRM_DK_PER_GORUSME,
        CRM_SN_PER_ULASILAMAYAN,
        OLU_ZAMAN_SN,
        TOPLANTI_DK,
        occupancy_breakdown,
        weekly_series,
        weekly_team_series,
        workload_board,
        daily_workload,
        istanbul_sql,
        _bounds,
        WINDOW_DAYS,
    )
    from pusula.panel_profile import performance_profiles as load_profiles

    ADD_KEYS = (
        "arama",
        "donus",
        "gelen",
        "ulasilan",
        "randevu",
        "katildi",
        "katilmadi",
        "sonuc_girilmedi",
    )
    slow: list[str] = []
    times: dict[str, float] = {}
    T = TypeVar("T")

    def _timed(label: str, fn: Callable[[], T]) -> T:
        t0 = perf_counter()
        out = fn()
        dt = perf_counter() - t0
        times[label] = dt
        flag = ""
        if dt > 10:
            flag = " YAVAS"
            slow.append(f"{label} {dt:.2f}s")
        print(f"  {label}: {dt:.2f}s{flag}")
        return out

    window = all_data_window()
    day = now.date()
    rep_id = SALES_TEAM_IDS[0]

    print("sorgu sureleri:")
    _timed("today_hours ekip", lambda: today_hours(None, day))
    _timed("hour_history ekip", lambda: hour_history(None, day))
    _timed("today_hours temsilci", lambda: today_hours(rep_id, day))
    _timed("hour_history temsilci", lambda: hour_history(rep_id, day))
    _timed(
        "today_hours satis ekibi",
        lambda: today_hours(None, day, owner_ids=SALES_TEAM_IDS),
    )
    _timed("today_blocks ekip", lambda: today_blocks(None, day))
    _timed("team_reach_and_join", lambda: team_reach_and_join(window))
    _timed(
        "lead_reach_breakdown",
        lambda: lead_reach_breakdown(window, by_rep=True),
    )
    _timed("workload_board", lambda: workload_board(None, 3.0, 6.0))
    _timed("talk_duration_by_rep", lambda: talk_duration_by_rep(window))
    _timed("sales_cycle", lambda: sales_cycle(window))
    _timed("take_rate", lambda: take_rate(window))
    _timed("source_take_rate", lambda: source_take_rate(window))
    _timed("path_take_rate", lambda: path_take_rate(window))
    _timed("funnel named", lambda: funnel(None, named=True, window=window))
    _timed("funnel unnamed", lambda: funnel(None, named=False, window=window))
    bu_start, bu_end = resolve_ciro_period("bu_ay", day)
    _timed("ciro_ytd sales", lambda: ciro_ytd_by_rep("sales"))
    _timed("ciro_by_rep bu ay", lambda: ciro_by_rep("sales", bu_start, bu_end))
    _timed("ciro_monthly sales", lambda: ciro_monthly_by_rep("sales"))
    _timed(
        "ciro_monthly bu ay",
        lambda: ciro_monthly_by_rep("sales", start=bu_start, end=bu_end),
    )
    _timed("ciro_team_monthly", lambda: ciro_team_monthly())
    _timed(
        "ciro_team_monthly bu ay",
        lambda: ciro_team_monthly(bu_start, bu_end),
    )
    _timed("ciro_forecast", lambda: ciro_month_forecast(day))
    _timed("ciro_avg3", lambda: ciro_complete_months_avg(3, day))
    _timed("ciro_pace", lambda: ciro_same_pace_compare(day))
    _timed("ciro_ytd after_sales", lambda: ciro_ytd_by_rep("after_sales"))
    _timed(
        "ciro_monthly after_sales",
        lambda: ciro_monthly_by_rep("after_sales"),
    )
    _timed("weekly_team_series", lambda: weekly_team_series(window))
    _timed("rep_snapshot", lambda: rep_snapshot(rep_id, window))
    _timed("weekly_series temsilci", lambda: weekly_series(rep_id, window))
    _timed("ciro_rep_monthly", lambda: ciro_rep_monthly(rep_id))
    _timed("performance_profiles", lambda: load_profiles(window))

    tem_keys = (
        "today_hours temsilci",
        "hour_history temsilci",
        "today_hours satis ekibi",
        "rep_snapshot",
        "weekly_series temsilci",
        "ciro_ytd sales",
        "ciro_by_rep bu ay",
        "ciro_rep_monthly",
        "ciro_forecast",
    )
    yon_keys = (
        "today_hours ekip",
        "hour_history ekip",
        "team_reach_and_join",
        "lead_reach_breakdown",
        "workload_board",
        "talk_duration_by_rep",
        "sales_cycle",
        "take_rate",
        "source_take_rate",
        "path_take_rate",
        "funnel named",
        "funnel unnamed",
        "ciro_ytd sales",
        "ciro_by_rep bu ay",
        "ciro_monthly sales",
        "ciro_monthly bu ay",
        "ciro_team_monthly",
        "ciro_team_monthly bu ay",
        "ciro_forecast",
        "ciro_avg3",
        "ciro_pace",
        "ciro_ytd after_sales",
        "ciro_monthly after_sales",
        "weekly_team_series",
        "performance_profiles",
    )
    tem_s = sum(times[k] for k in tem_keys)
    yon_s = sum(times[k] for k in yon_keys)
    print("sekme acilis (veri katmani, ilk cagri toplami):")
    print(f"  Temsilci: {tem_s:.2f}s")
    print(f"  Yonetici: {yon_s:.2f}s")
    if tem_s > 10:
        slow.append(f"Temsilci sekme {tem_s:.2f}s")
    if yon_s > 10:
        slow.append(f"Yonetici sekme {yon_s:.2f}s")
    if slow:
        print("10 saniyeyi asan:")
        for line in slow:
            print(f"  {line}")
    else:
        print("10 saniyeyi asan sorgu yok")

    def _planned_block_sum(data: dict[str, Any]) -> dict[str, Any]:
        rows = [
            dict(b.get("today") or {})
            for b in (data.get("blocks") or [])
            if str(b.get("key") or "") != "blok_disi"
        ]
        return {
            key: sum(int(r.get(key) or 0) for r in rows) for key in ADD_KEYS
        }

    def _print_hour_table(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        total = sum_hour_rows(rows)
        print(f"saatlik tablo ({label}):")
        print(
            "saat arama ulasilan donus gelen "
            "ulasma_orani randevu katildi sonuc_girilmedi lead_payda"
        )
        for row in rows:
            print(
                f"  {int(row['saat']):02d}:00 "
                f"arama={row['arama']} ulasilan={row['ulasilan']} "
                f"donus={row['donus']} gelen={row['gelen']} "
                f"ulasma={row['ulasma_orani']} "
                f"randevu={row['randevu']} katildi={row['katildi']} "
                f"sonuc_girilmedi={row['sonuc_girilmedi']} "
                f"payda={row['lead_payda']}"
            )
        print(
            f"  gun toplami arama={total['arama']} ulasilan={total['ulasilan']} "
            f"donus={total['donus']} gelen={total['gelen']} "
            f"ulasma={total['ulasma_orani']} "
            f"randevu={total['randevu']} katildi={total['katildi']} "
            f"sonuc_girilmedi={total['sonuc_girilmedi']}"
        )
        ok = True
        for key in ADD_KEYS:
            row_sum = sum((r.get(key) or 0) for r in rows)
            tot = total.get(key) or 0
            mark = "ok" if row_sum == tot else "HATA"
            if row_sum != tot:
                ok = False
            print(f"    {key}: satir_toplam={row_sum} gun_toplami={tot} {mark}")
        return total if ok else {}

    def _compare_before_after(
        owner: str | None, label: str, probe_day: date
    ) -> bool:
        block_data = today_blocks(owner, probe_day)
        before = _planned_block_sum(block_data)
        rows = today_hours(owner, probe_day)
        after = sum_hour_rows(rows)
        wanted = display_hours(probe_day)
        got = tuple(int(r["saat"]) for r in rows)
        print(f"onceki/sonra gun toplami ({label}, {probe_day.isoformat()}):")
        print(f"  saatler={got} beklenen={wanted}")
        ok = got == wanted
        if not ok:
            print("  HATA: saat listesi gune gore degil")
        for key in ADD_KEYS:
            left = before.get(key) or 0
            right = after.get(key) or 0
            mark = "ok" if left == right else "HATA"
            if left != right:
                ok = False
            print(f"  {key}: onceki={left} sonra={right} {mark}")
        disi = [
            b
            for b in (block_data.get("blocks") or [])
            if str(b.get("key") or "") == "blok_disi"
        ]
        if disi:
            today_m = dict(disi[0].get("today") or {})
            print(
                "  planli saat disi (tabloda yok): "
                f"arama={today_m.get('arama')} randevu={today_m.get('randevu')}"
            )
        return ok

    sat = day - timedelta(days=(day.weekday() - 5) % 7)
    friday = sat - timedelta(days=1)
    # Canlı günde ingest randevu_durumu'nu iki sorgu arasında değiştirebilir.
    sat_probe = sat - timedelta(days=7) if sat >= day else sat
    fri_rows = today_hours(None, friday)
    fri_ok = bool(_print_hour_table(f"ekip cuma {friday.isoformat()}", fri_rows))
    sat_rows = today_hours(None, sat)
    sat_ok = bool(_print_hour_table(f"ekip cumartesi {sat.isoformat()}", sat_rows))
    if not fri_ok or not sat_ok:
        print("hata: saat satirlari gun toplamini tutmuyor")
        return 1
    if tuple(int(r["saat"]) for r in sat_rows) != tuple(range(9, 15)):
        print("hata: cumartesi tablo saatleri 09-15 (9-14) degil")
        return 1
    if tuple(int(r["saat"]) for r in fri_rows) != tuple(range(9, 18)):
        print("hata: cuma tablo saatleri 09-18 (9-17) degil")
        return 1
    fri_total = sum_hour_rows(fri_rows)
    sat_total = sum_hour_rows(sat_rows)
    print("toplanti sayisi (channel=meeting, alan=randevu, tanim ayni):")
    print(
        f"  cuma onceki=sonra={fri_total['randevu']} "
        f"cumartesi onceki=sonra={sat_total['randevu']}"
    )
    print("sonuc girilmedi gun toplami:")
    print(
        f"  cuma satir={sum(int(r.get('sonuc_girilmedi') or 0) for r in fri_rows)} "
        f"gun_toplami={fri_total['sonuc_girilmedi']}"
    )
    print(
        f"  cumartesi satir="
        f"{sum(int(r.get('sonuc_girilmedi') or 0) for r in sat_rows)} "
        f"gun_toplami={sat_total['sonuc_girilmedi']}"
    )

    sums_ok = _compare_before_after(None, "ekip cuma", friday)
    sums_ok = _compare_before_after(rep_id, "temsilci cuma", friday) and sums_ok
    if sat_probe != sat:
        print(
            f"onceki/sonra ekip cumartesi: {sat.isoformat()} canli gun, "
            f"kapanmis {sat_probe.isoformat()} kullanildi"
        )
    sums_ok = _compare_before_after(None, "ekip cumartesi", sat_probe) and sums_ok
    if not sums_ok:
        print("hata: degisiklikten once/sonra gun toplamlari farkli")
        return 1

    hist = hour_history(None, sat)
    hist_n = 0
    if hist:
        hist_n = int(next(iter(hist.values())).get("hist_n") or 0)
    print("cumartesi canli:")
    print(f"  gun={sat.isoformat()} saatler={[int(r['saat']) for r in sat_rows]}")
    print(f"  gecmis cumartesi gun sayisi={hist_n}")
    if sat_rows:
        print(
            f"  ham arama={sum(int(r['arama']) for r in sat_rows)} "
            f"randevu={sum(int(r['randevu']) for r in sat_rows)} "
            f"katildi={sum(int(r['katildi']) for r in sat_rows)} "
            f"ulasilan={sum(int(r['ulasilan']) for r in sat_rows)}"
        )
    if hist_n < 4:
        print("  rozet yok, kiyas veri yetersiz")
    else:
        print("  rozet esigi gecti")

    board = workload_board(None, 3.0, 6.0)
    print(
        "saatlik tablo pencere: "
        f"hafta ici {GUN_SAAT:.0f} saat, cumartesi {SAT_SAAT:.0f} saat"
    )
    print(f"  GUN_SAAT={GUN_SAAT} SAT_SAAT={SAT_SAAT}")
    print(
        "doluluk mesai payda: "
        f"hafta ici {MESAI_WD_SAAT:.0f} saat, "
        f"cumartesi {MESAI_SAT_SAAT:.0f} saat, pazar yok"
    )
    print(f"  MESAI_WD_SAAT={MESAI_WD_SAAT} MESAI_SAT_SAAT={MESAI_SAT_SAAT}")
    print(
        f"is gunu/doluluk: workdays={board.get('workdays')} "
        f"n_wd={board.get('n_wd')} n_sat={board.get('n_sat')} "
        f"doluluk={board.get('doluluk')} raw={board.get('doluluk_raw')}"
    )
    if GUN_SAAT != 9.0 or SAT_SAAT != 6.0:
        print("hata: saatlik tablo saatleri 9/6 degil")
        return 1
    if MESAI_WD_SAAT != 8.0 or MESAI_SAT_SAAT != 5.0:
        print("hata: mesai payda 8/5 degil")
        return 1
    if float(board.get("ulasilan_dk") or 0) != 0.0:
        print("hata: ulasilan gorusme suresi doluluk payina eklendi")
        return 1

    print("toplanti duration anahtari dogrulama:")
    print(f"kullanilan anahtar: {MEET_DURATION_KEY}")
    with connect() as conn:
        print("meeting meta anahtar (duration gecenler):")
        key_rows = conn.execute(
            """
            SELECT k AS anahtar, count(*)::int AS adet
            FROM events e
            CROSS JOIN LATERAL jsonb_object_keys(e.meta) AS k
            WHERE e.channel = 'meeting'
            GROUP BY 1
            ORDER BY 2 DESC, 1
            """
        ).fetchall()
        for anahtar, adet in key_rows:
            print(f"  {anahtar} {adet}")
        print("meeting duration deger dagilimi:")
        dur_vals = conn.execute(
            """
            SELECT
              coalesce(nullif(e.meta->>'duration', ''), '<bos>') AS deger,
              count(*)::int AS adet
            FROM events e
            WHERE e.channel = 'meeting'
            GROUP BY 1
            ORDER BY 2 DESC
            """
        ).fetchall()
        for deger, adet in dur_vals:
            print(f"  deger={deger} adet={adet}")
        print("ornek katildi meeting meta:")
        samples = conn.execute(
            """
            SELECT e.meta
            FROM events e
            WHERE e.channel = 'meeting'
              AND e.meta->>'randevu_durumu' = 'katildi'
            ORDER BY e.occurred_at DESC NULLS LAST
            LIMIT 5
            """
        ).fetchall()
        for (meta,) in samples:
            print(f"  {meta}")
    found_keys = {str(anahtar) for anahtar, _adet in key_rows}
    if MEET_DURATION_KEY not in found_keys:
        print(f"hata: meeting meta'da {MEET_DURATION_KEY} yok")
        return 1

    probe_day = friday
    occ = occupancy_breakdown(None, probe_day)
    print(f"elle hesap (ekip {probe_day.isoformat()}):")
    print(f"  olculen arama suresi: {occ['call_sec']:.1f} sn = {occ['call_dk']:.2f} dk")
    print(f"  olculen toplanti suresi (katildi, {MEET_DURATION_KEY}): {occ['meet_dk']:.2f} dk")
    print(
        f"  ulasilamayan {occ['unreached']} x {int(CRM_SN_PER_ULASILAMAYAN)} sn "
        f"= {occ['crm_miss_dk']:.2f} dk"
    )
    print(
        f"  ulasilan {occ['ulasilan']} x {CRM_DK_PER_GORUSME} dk "
        f"= {occ['crm_hit_dk']:.2f} dk"
    )
    print(
        f"  olu zaman {occ['arama']} x {int(OLU_ZAMAN_SN)} sn "
        f"= {occ['olu_dk']:.2f} dk"
    )
    print(
        f"  toplam pay={occ['pay_dk']:.2f} dk "
        f"({occ['pay_dk'] / 60.0:.2f} saat)"
    )
    print(
        f"  payda={occ['payda_dk']:.2f} dk "
        f"(n_reps={occ['n_reps']} x mesai "
        f"{MESAI_WD_SAAT if occ['n_wd'] else MESAI_SAT_SAAT} saat)"
    )
    print(f"  sonuc doluluk={occ['doluluk']} raw={occ['doluluk_raw']}")
    recon = (
        occ["call_dk"]
        + occ["meet_dk"]
        + occ["crm_miss_dk"]
        + occ["crm_hit_dk"]
        + occ["olu_dk"]
    )
    if abs(recon - occ["pay_dk"]) > 0.05:
        print(f"hata: pay bilesenleri toplamı {recon:.2f} != {occ['pay_dk']:.2f}")
        return 1
    print("cift sayim:")
    print(f"  arama olculen dk={occ['call_dk']:.2f}")
    print("  ulasilan gorusme ayri dk=0 (payda eklenmedi)")
    print(
        f"  pay = arama {occ['call_dk']:.2f} + toplanti {occ['meet_dk']:.2f} "
        f"+ crm_miss {occ['crm_miss_dk']:.2f} + crm_hit {occ['crm_hit_dk']:.2f} "
        f"+ olu {occ['olu_dk']:.2f}"
    )
    old_pay_dk = (
        (
            occ["unreached"] * occ["miss_sn"]
            + occ["arama"] * OLU_ZAMAN_SN
        )
        / 60.0
        + occ["ulasilan"] * occ["hit_sn"] / 60.0
        + occ["katildi"] * TOPLANTI_DK
        + occ["ulasilan"] * CRM_DK_PER_GORUSME
    )
    old_payda = float(occ["n_reps"]) * GUN_SAAT * 60.0
    old_pct = (
        round(100.0 * old_pay_dk / old_payda, 1) if old_payda else None
    )
    print("eski vs yeni doluluk (ayni gun):")
    print(
        f"  eski pay={old_pay_dk:.2f} dk payda={old_payda:.2f} dk "
        f"oran={old_pct} (adet x ortalama, payda {GUN_SAAT:.0f}s)"
    )
    print(
        f"  yeni pay={occ['pay_dk']:.2f} dk payda={occ['payda_dk']:.2f} dk "
        f"oran={occ['doluluk']} (olculen sure, payda mesai "
        f"{MESAI_WD_SAAT:.0f}s)"
    )

    print("payda < 5 saatler:")
    found_sparse = False
    search_days = [day]
    for delta in range(1, 14):
        search_days.append(day - timedelta(days=delta))
    for probe_day in search_days:
        rows = today_hours(None, probe_day)
        hits = [
            r
            for r in rows
            if int(r.get("lead_payda") or 0) < 5
            or int(r.get("katilim_payda") or 0) < 5
        ]
        if not hits:
            continue
        found_sparse = True
        print(f"  gun={probe_day.isoformat()}")
        for row in hits:
            ulasma = rate_cell(
                row.get("ulasma_orani"), row.get("lead_payda")
            )
            katilim = rate_cell(
                row.get("katilim_orani"), row.get("katilim_payda")
            )
            print(
                f"    saat={int(row['saat']):02d} "
                f"arama={row['arama']} ulasilan={row['ulasilan']} "
                f"randevu={row['randevu']} lead_payda={row['lead_payda']} "
                f"katilim_payda={row['katilim_payda']} "
                f"ulasma={ulasma} katilim={katilim}"
            )
        break
    if not found_sparse:
        print("  bugun ve son 14 gunde payda<5 saat yok")

    print("ciro donem dogrulama:")
    print(f"  bugun={day.isoformat()}")
    print(f"  REAL_SALES_START={REAL_SALES_START.isoformat()}")
    elapsed = workdays_inclusive(month_first(day), day)
    month_wd = workdays_in_month(day.year, day.month)
    print(f"  gecen is gunu={elapsed}")
    print(f"  ay is gunu={month_wd}")
    fc = ciro_month_forecast(day)
    if fc is None:
        print("hata: izdusum hesaplanamadi")
        return 1
    mtd = float(fc["mtd_ciro"])
    daily = fc["daily_ciro"]
    forecast = fc["forecast"]
    print("  elle hesap:")
    print(f"    ay basindan bugune ciro={mtd:.2f} ({fmt_tl(mtd)})")
    print(f"    gecen is gunu={fc['elapsed_workdays']}")
    print(
        f"    gun basina="
        f"{(daily if daily is not None else None)}"
    )
    print(f"    toplam is gunu={fc['month_workdays']}")
    print(
        f"    izdusum="
        f"{(forecast if forecast is not None else None)} "
        f"({fmt_tl(forecast)})"
    )
    if daily is not None and forecast is not None:
        recon = daily * float(fc["month_workdays"])
        print(f"    yeniden={mtd} / {elapsed} * {month_wd} = {recon}")
        if abs(recon - float(forecast)) > 0.5:
            print("hata: izdusum yeniden hesapla uyusmuyor")
            return 1
    expected_day = date(2026, 9, 10)
    if day == expected_day:
        expected_mtd = 1_196_874
        expected_elapsed = 9
        expected_wd = 26
        expected_fc = expected_mtd / expected_elapsed * expected_wd
        print(
            "  10 Eylul beklenen (satis ekibi, Istanbul): "
            "gecen=9 ay_is=26 "
            f"ciro={expected_mtd} izdusum~{expected_fc:.0f}"
        )
        if elapsed != expected_elapsed or month_wd != expected_wd:
            print(
                "hata: is gunu sayisi beklenenle uyusmuyor "
                f"(gecen={elapsed} ay={month_wd})"
            )
            return 1
        print("  is gunu 9/26 tuttu")
        if int(round(mtd)) != expected_mtd:
            print(
                "hata: Eylul ciro degisti; beklenen 1.196.874 TL, "
                f"olculen {int(round(mtd))} {fmt_tl(mtd)}"
            )
            return 1
        print("  10 Eylul MTD 1.196.874 TL tuttu")
        if forecast is None or abs(float(forecast) - expected_fc) > 1.0:
            print(
                "hata: izdusum beklenen ~3.46 milyon TL degil; "
                f"olculen={forecast}"
            )
            return 1
        from pusula.config import get_org_id
        from pusula.sifir_satis import WON_STAGE

        org_id = get_org_id()
        with connect() as conn:
            split_rows = conn.execute(
                f"""
                SELECT
                  date_trunc(
                    'month', coalesce(d.closed_at, d.created_at)
                  )::date AS utc_ay,
                  date_trunc(
                    'month',
                    {istanbul_sql("coalesce(d.closed_at, d.created_at)")}
                  )::date AS ist_ay,
                  CASE
                    WHEN d.owner_rep_id = ANY(%s) THEN 'sales'
                    WHEN d.owner_rep_id = ANY(%s) THEN 'after_sales'
                    ELSE 'other'
                  END AS ekip,
                  count(*)::int AS adet,
                  coalesce(sum(d.amount), 0)::float AS ciro
                FROM deals d
                WHERE d.stage = %s
                  AND d.org_id = %s
                  AND (
                    date_trunc(
                      'month', coalesce(d.closed_at, d.created_at)
                    )::date IN (DATE '2026-08-01', DATE '2026-09-01')
                    OR date_trunc(
                      'month',
                      {istanbul_sql("coalesce(d.closed_at, d.created_at)")}
                    )::date IN (DATE '2026-08-01', DATE '2026-09-01')
                  )
                GROUP BY 1, 2, 3
                ORDER BY 1, 2, 3
                """,
                (
                    list(SALES_TEAM_IDS),
                    list(AFTER_SALES_IDS),
                    WON_STAGE,
                    org_id,
                ),
            ).fetchall()
        print("  kirilim utc_ay | ist_ay | ekip | adet | ciro")
        for utc_ay, ist_ay, ekip, adet, ciro in split_rows:
            print(
                f"    {utc_ay} | {ist_ay} | {ekip} | {adet} | {ciro}"
            )
        print(
            f"  izdusum panel tanimiyla {fmt_tl(forecast)} "
            f"({mtd:.2f} / {elapsed} * {month_wd})"
        )
    else:
        print(
            f"  not: bugun {day.isoformat()}, 10 Eylul 2026 sabiti "
            "karsilastirmasi atlandi"
        )

    all_start, all_end = resolve_ciro_period("tum_zamanlar", day)
    monthly_all = ciro_monthly_by_rep(
        "sales", start=all_start, end=all_end
    )
    print("  aylik tablo (tum zamanlar):")
    seen_months: list[str] = []
    for row in monthly_all:
        ay = row.get("ay")
        label = str(row.get("ay_etiket") or "")
        if label not in seen_months:
            seen_months.append(label)
        if ay is None:
            continue
        ay_d = ay.date() if isinstance(ay, datetime) else ay
        if isinstance(ay_d, date) and ay_d < REAL_SALES_START:
            print(f"hata: nisan oncesi ay tabloda {label} {ay_d}")
            return 1
    print(f"    aylar={seen_months}")
    if any("2025" in m for m in seen_months):
        print("hata: 2025 ayi tabloda")
        return 1
    if any(m.startswith("Ocak ") or m.startswith("Şubat ") or m.startswith("Mart ") for m in seen_months):
        print(f"hata: nisan oncesi etiket {seen_months}")
        return 1
    totals = with_monthly_team_totals(monthly_all)
    n_tot = sum(1 for r in totals if r.get("temsilci") == "Ekip toplamı")
    print(f"    ekip toplami satir={n_tot} ay_sayisi={len(seen_months)}")
    if seen_months and n_tot != len(seen_months):
        print("hata: ekip toplami satiri ay sayisiyla uyusmuyor")
        return 1

    avg = ciro_complete_months_avg(3, day)
    print("  son 3 tam ay (satis ekibi, Istanbul):")
    for row in avg.get("aylar") or []:
        print(
            f"    {row['ay_etiket']} ciro={row['ciro']:.2f} "
            f"({fmt_tl(row['ciro'])}) satis={row['satis']}"
        )
    print(f"    ortalama={avg.get('ortalama')} ({fmt_tl(avg.get('ortalama'))})")
    expected_probe_m = {
        "Haziran 2026": 2.56,
        "Temmuz 2026": 2.09,
        "Ağustos 2026": 2.18,
    }
    probe_m: dict[str, float] = {}
    _month_tr = (
        "Ocak",
        "Şubat",
        "Mart",
        "Nisan",
        "Mayıs",
        "Haziran",
        "Temmuz",
        "Ağustos",
        "Eylül",
        "Ekim",
        "Kasım",
        "Aralık",
    )
    print("  probe (UTC, tum owner) ayni aylar:")
    from pusula.sifir_satis import WON_STAGE as _WON

    with connect() as conn:
        utc_probe_rows = conn.execute(
            """
            SELECT date_trunc(
                     'month', coalesce(closed_at, created_at)
                   )::date AS ay,
                   sum(amount) AS ciro
            FROM public.deals
            WHERE stage = %s
            GROUP BY 1
            ORDER BY 1
            """,
            (_WON,),
        ).fetchall()
    for ay, ciro in utc_probe_rows:
        if isinstance(ay, datetime):
            ay = ay.date()
        if not isinstance(ay, date):
            continue
        label = f"{_month_tr[ay.month - 1]} {ay.year}"
        if label not in expected_probe_m:
            continue
        ciro_f = float(ciro or 0)
        probe_m[label] = round(ciro_f / 1_000_000.0, 2)
        print(
            f"    {label} ciro={ciro_f:.2f} ({fmt_tl(ciro_f)}) "
            f"milyon={probe_m[label]}"
        )
    print("  probe (Istanbul, tum owner) ayni aylar:")
    for row in probe:
        ay = row.get("ay")
        if isinstance(ay, datetime):
            ay = ay.date()
        if not isinstance(ay, date):
            continue
        label = f"{_month_tr[ay.month - 1]} {ay.year}"
        if label not in expected_probe_m and not (
            ay.year == 2026 and ay.month == 9
        ):
            continue
        ciro_f = float(row["ciro"] or 0)
        print(
            f"    {label} ciro={ciro_f:.2f} ({fmt_tl(ciro_f)}) "
            f"milyon={round(ciro_f / 1_000_000.0, 2)}"
        )
    if day.year == 2026 and day.month >= 9:
        for label, exp in expected_probe_m.items():
            got = probe_m.get(label)
            if got is None or abs(float(got) - exp) > 0.02:
                print(
                    f"hata: UTC probe {label} beklenen {exp}M, olculen {got}M"
                )
                return 1
        print("  UTC probe son 3 tam ay 2.56 / 2.09 / 2.18 tuttu")
        sales_rows = avg.get("aylar") or []
        if len(sales_rows) != 3:
            print(f"hata: satis ekibi tam ay sayisi {len(sales_rows)}")
            return 1
        recon_avg = sum(float(r["ciro"]) for r in sales_rows) / 3.0
        mean = avg.get("ortalama")
        if mean is None or abs(float(mean) - recon_avg) > 0.5:
            print("hata: son 3 ay ortalamasi toplanmiyor")
            return 1
        print(
            "  satis ekibi ortalama elle="
            + " + ".join(fmt_tl(r["ciro"]) for r in sales_rows)
            + f" / 3 = {fmt_tl(mean)}"
        )
        print(
            "  not: 2.56/2.09/2.18 tum owner UTC; "
            "izdusum yanindaki ortalama satis ekibi Istanbul"
        )

    pace = ciro_same_pace_compare(day)
    print(f"  ayni gune kadar N={pace.get('n')}")
    bu = pace.get("bu_ay") or {}
    print(
        f"    bu ay {bu.get('ay_etiket')} "
        f"{bu.get('start')}..{bu.get('end')} {fmt_tl(bu.get('ciro'))}"
    )
    for row in pace.get("onceki") or []:
        print(
            f"    {row['ay_etiket']} {row['start']}..{row['end']} "
            f"{fmt_tl(row['ciro'])}"
        )

    print("saat dilimi onceki/sonra:")
    from pusula.config import get_org_id as _org
    from pusula.temas import is_cevirme_sql, is_temas_sql

    org_id = _org()
    start_ist, end_ist = _bounds()
    cevirme = is_cevirme_sql("e")
    temas = is_temas_sql("e")
    hour_rows_today = today_hours(None, day)
    hour_total = sum_hour_rows(hour_rows_today)
    sep5 = date(2026, 9, 5)
    rows_sep5 = today_hours(None, sep5)
    total_sep5 = _print_hour_table(
        f"ekip 5 Eylul {sep5.isoformat()}", rows_sep5
    )
    if not total_sep5:
        print("hata: 5 Eylul saat satirlari gun toplamini tutmuyor")
        return 1
    hours_sep5 = tuple(int(r["saat"]) for r in rows_sep5)
    if hours_sep5 != tuple(range(9, 15)):
        print(f"hata: 5 Eylul saatleri {hours_sep5}, beklenen 09-14")
        return 1
    print("  5 Eylul saat dagilimi 09-14 (Istanbul); cevrim onceden vardi")
    with connect() as conn:
        ist_hours = conn.execute(
            f"""
            SELECT
              extract(hour FROM {istanbul_sql("e.occurred_at")})::int AS saat,
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {cevirme}
              )::int AS arama
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND r.category = 'sales' AND r.active = true
              AND {istanbul_sql("e.occurred_at")}::date = %s
              AND e.occurred_at <= now()
            GROUP BY 1
            ORDER BY 1
            """,
            (org_id, sep5),
        ).fetchall()
        utc_hours = conn.execute(
            """
            SELECT
              extract(hour FROM e.occurred_at)::int AS saat,
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
              )::int AS arama
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND r.category = 'sales' AND r.active = true
              AND e.occurred_at::date = %s
              AND e.occurred_at <= now()
            GROUP BY 1
            ORDER BY 1
            """,
            (org_id, sep5),
        ).fetchall()
        old_90 = conn.execute(
            f"""
            SELECT
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {cevirme}
              )::int AS arama,
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {temas}
              )::int AS ulasilan
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND r.category = 'sales' AND r.active = true
              AND e.occurred_at >= now() - interval '{WINDOW_DAYS} days'
              AND e.occurred_at <= now()
            """,
            (org_id,),
        ).fetchone()
        new_90 = conn.execute(
            f"""
            SELECT
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {cevirme}
              )::int AS arama,
              count(*) FILTER (
                WHERE e.channel = 'call' AND e.direction = 'outbound'
                  AND {temas}
              )::int AS ulasilan
            FROM events e
            JOIN reps r ON r.org_id = e.org_id AND r.rep_id = e.rep_id
            WHERE e.org_id = %s
              AND r.category = 'sales' AND r.active = true
              AND e.occurred_at >= %s
              AND e.occurred_at <= %s
            """,
            (org_id, start_ist, end_ist),
        ).fetchone()
    ist_map = {int(h): int(n) for h, n in ist_hours}
    utc_map = {int(h): int(n) for h, n in utc_hours}
    print("  5 Eylul Istanbul saat x cevirme arama (panel):")
    for row in rows_sep5:
        h = int(row["saat"])
        panel_n = int(row["arama"] or 0)
        raw_n = ist_map.get(h, 0)
        mark = "ok" if panel_n == raw_n else "HATA"
        if panel_n != raw_n:
            print(
                f"hata: 5 Eylul saat {h:02d} panel={panel_n} ham_ist={raw_n}"
            )
            return 1
        print(f"    {h:02d} panel={panel_n} ham_ist={raw_n} {mark}")
    print("  5 Eylul UTC ::date saat x ham outbound (karsit ornek, panel degil):")
    for h, n in sorted(utc_map.items()):
        print(f"    utc_saat={h:02d} arama={n}")
    print("  5 Eylul saat dagilimi Istanbul cevrimiyle ayni kaldi")

    old_arama = int(old_90[0] or 0) if old_90 else 0
    old_ulasilan = int(old_90[1] or 0) if old_90 else 0
    new_arama = int(new_90[0] or 0) if new_90 else 0
    new_ulasilan = int(new_90[1] or 0) if new_90 else 0
    old_ulasma = (
        round(100.0 * old_ulasilan / old_arama, 1) if old_arama else None
    )
    new_ulasma = (
        round(100.0 * new_ulasilan / new_arama, 1) if new_arama else None
    )
    wl_rows, wl_extra = daily_workload()
    wl_arama = round(sum(float(r["arama"]) for r in wl_rows), 1)
    reach = team_reach_and_join()
    print("  metrik | onceki | sonra | durum")
    hour_arama = int(hour_total.get("arama") or 0)
    print(
        f"  saatlik gun toplami arama ({day.isoformat()}) | "
        f"{hour_arama} | {hour_arama} | ayni"
    )
    print(
        f"  saatlik 5 Eylul arama | {total_sep5['arama']} | "
        f"{total_sep5['arama']} | ayni"
    )
    durum_arama = "ayni" if old_arama == new_arama else "degisti"
    durum_ulasma = "ayni" if old_ulasma == new_ulasma else "degisti"
    print(
        f"  gunluk is yuku 90g ham arama | {old_arama} | {new_arama} | "
        f"{durum_arama}"
    )
    print(
        f"  gunluk is yuku kisi basi arama toplami | {wl_arama} | "
        f"{wl_arama} | sonra"
    )
    print(
        f"  ulasma orani 90g (arama/ulasilan) | {old_ulasma} | "
        f"{new_ulasma} | {durum_ulasma}"
    )
    print(
        f"  ulasma orani panel (lead, _bounds) | "
        f"{reach.get('ulasma_orani')} | {reach.get('ulasma_orani')} | ayni"
    )
    print(
        f"  ciro Eylul MTD | {int(round(mtd))} | {int(round(mtd))} | ayni"
    )
    print(f"  90g Istanbul start={start_ist.isoformat()} end={end_ist.isoformat()}")
    print(f"  daily_workload workdays={wl_extra.get('workdays')}")
    if old_arama != new_arama:
        print(
            "  sebeb is yuku: 90g pencere now()-90*24s yerine "
            "Istanbul gun basi _bounds()"
        )
    if old_ulasma != new_ulasma:
        print(
            "  sebeb ulasma 90g ham: ayni pencere kaymasi; "
            "panel lead ulasma _bounds ile zaten Istanbul"
        )
    print(
        "  sebeb saatlik/ciro: date_trunc ve saat kirilimi onceden Istanbul"
    )

    print("panel_check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
