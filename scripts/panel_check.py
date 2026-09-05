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
        SALES_TEAM_IDS,
        ciro_monthly_by_rep,
        ciro_rep_monthly,
        ciro_team_monthly,
        ciro_ytd_by_rep,
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
        weekly_series,
        weekly_team_series,
        workload_board,
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
    _timed("ciro_ytd sales", lambda: ciro_ytd_by_rep("sales"))
    _timed("ciro_monthly sales", lambda: ciro_monthly_by_rep("sales"))
    _timed("ciro_team_monthly", lambda: ciro_team_monthly())
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
        "ciro_rep_monthly",
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
        "ciro_monthly sales",
        "ciro_team_monthly",
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
        before = _planned_block_sum(today_blocks(owner, probe_day))
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
            for b in (today_blocks(owner, probe_day).get("blocks") or [])
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
    sums_ok = _compare_before_after(None, "ekip cumartesi", sat) and sums_ok
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
        f"doluluk payda: hafta ici {GUN_SAAT:.0f} saat, "
        f"cumartesi {SAT_SAAT:.0f} saat, pazar yok"
    )
    print(f"  GUN_SAAT={GUN_SAAT} SAT_SAAT={SAT_SAAT}")
    print(
        f"is gunu/doluluk: workdays={board.get('workdays')} "
        f"doluluk={board.get('doluluk')}"
    )
    if GUN_SAAT != 9.0 or SAT_SAAT != 6.0:
        print("hata: doluluk saatleri 9/6 degil")
        return 1

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

    print("panel_check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
