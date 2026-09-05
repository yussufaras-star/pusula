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
    PLANNED_BLOCKS,
    SATURDAY_BLOCK,
    block_by_key,
    blocks_for,
    card_phase,
    hours_of,
)
from pusula.freshness import FRESHNESS_THRESHOLDS, is_mesai
from pusula.panel_status import (
    PanelReadiness,
    format_block_line,
    format_impact_line,
    format_source_line,
    last_due_slot,
    load_panel_readiness,
    next_ingest_at,
    should_warn,
)


def _phase_label(phase: str) -> str:
    if phase == "baslamadi":
        return "bekleniyor"
    if phase == "devam_ediyor":
        return "devam ediyor"
    return "tamamlandi"


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
    print("12:00 blok kart:")
    for block in PLANNED_BLOCKS:
        phase = card_phase(block, weekday_noon.date(), weekday_noon)
        print(f"  {block.label} {_phase_label(phase)}")

    at_13 = datetime(2026, 9, 3, 13, 0, tzinfo=ISTANBUL)
    print("13:00 senaryo:")
    phases_13: dict[str, str] = {}
    for block in PLANNED_BLOCKS:
        phase = card_phase(block, at_13.date(), at_13)
        phases_13[block.key] = phase
        print(f"  {block.label} {_phase_label(phase)}")
    if (
        phases_13["arama_14_17"] != "baslamadi"
        or phases_13["toplanti_17_18"] != "baslamadi"
    ):
        print("hata: 13:00 14-17 ve 17-18 bekleniyor olmali")
        return 1

    at_15 = datetime(2026, 9, 3, 15, 0, tzinfo=ISTANBUL)
    print("15:00 senaryo:")
    phases_15: dict[str, str] = {}
    for block in PLANNED_BLOCKS:
        phase = card_phase(block, at_15.date(), at_15)
        phases_15[block.key] = phase
        print(f"  {block.label} {_phase_label(phase)}")
    if phases_15["arama_14_17"] != "devam_ediyor":
        print("hata: 15:00 14-17 devam ediyor olmali")
        return 1
    if phases_15["toplanti_17_18"] != "baslamadi":
        print("hata: 15:00 17-18 bekleniyor olmali")
        return 1
    print("15:00 14-17 rozet yok (devam ediyor)")
    print(f"  etki: {format_impact_line(missed)}")
    print(f"  kacan yuva: {missed_at.strftime('%H:%M')}")

    sat_day = datetime(2026, 9, 5, 12, 0, tzinfo=ISTANBUL)
    sat_blocks = blocks_for(sat_day.date())
    print("cumartesi bloklar:")
    print(f"  keys={[b.key for b in sat_blocks]} labels={[b.label for b in sat_blocks]}")
    if sat_blocks != (SATURDAY_BLOCK,):
        print("hata: cumartesi tek karisik blok degil")
        return 1
    if SATURDAY_BLOCK.kind != "mixed":
        print("hata: cumartesi kind mixed degil")
        return 1
    if "arama" in SATURDAY_BLOCK.label or "toplanti" in SATURDAY_BLOCK.label:
        print("hata: cumartesi etiketi blok tipi yargisi tasiyor")
        return 1
    print(f"  saatler={hours_of(SATURDAY_BLOCK)}")
    if hours_of(SATURDAY_BLOCK) != (9, 10, 11, 12, 13, 14):
        print("hata: cumartesi saatleri 9-14 olmali")
        return 1
    sat_next = next_ingest_at(datetime(2026, 9, 5, 15, 30, tzinfo=ISTANBUL))
    print(f"  cumartesi 15:30 sonraki={sat_next}")
    if sat_next is None or sat_next.date().weekday() != 0:
        print("hata: cumartesi mesai sonrasi sonraki ingest pazartesi olmali")
        return 1
    sun_blocks = blocks_for(datetime(2026, 9, 6).date())
    print(f"  pazar blok={list(sun_blocks)}")
    if sun_blocks:
        print("hata: pazarda planli blok var")
        return 1
    weekday_blocks = blocks_for(datetime(2026, 9, 4).date())
    if weekday_blocks != PLANNED_BLOCKS:
        print("hata: cuma dort blok degil")
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
        all_data_window,
        funnel,
        hours_for_block,
        lead_reach_breakdown,
        path_take_rate,
        rate_cell,
        rep_snapshot,
        sales_cycle,
        source_take_rate,
        take_rate,
        talk_duration_by_rep,
        team_reach_and_join,
        today_blocks,
        today_hours,
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
    _timed("today_blocks ekip", lambda: today_blocks(None, day))
    _timed("today_hours ekip", lambda: today_hours(None, day))
    _timed("today_blocks temsilci", lambda: today_blocks(rep_id, day))
    _timed("today_hours temsilci", lambda: today_hours(rep_id, day))
    _timed(
        "today_blocks satis ekibi",
        lambda: today_blocks(None, day, owner_ids=SALES_TEAM_IDS),
    )
    _timed(
        "today_hours satis ekibi",
        lambda: today_hours(None, day, owner_ids=SALES_TEAM_IDS),
    )
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
        "today_blocks temsilci",
        "today_hours temsilci",
        "today_blocks satis ekibi",
        "today_hours satis ekibi",
        "rep_snapshot",
        "weekly_series temsilci",
        "ciro_ytd sales",
        "ciro_rep_monthly",
    )
    yon_keys = (
        "today_blocks ekip",
        "today_hours ekip",
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

    def _check_hour_sums(
        owner: str | None, label: str, probe_day: date
    ) -> bool:
        data = today_blocks(owner, probe_day)
        rows = today_hours(owner, probe_day)
        print(f"saat vs blok ({label}, {probe_day.isoformat()}):")
        ok = True
        planned_keys: list[str] = []
        for block in data.get("blocks") or []:
            key = str(block.get("key") or "")
            if key == "blok_disi":
                continue
            planned_keys.append(key)
            spec = block_by_key(key)
            hour_rows = hours_for_block(rows, key)
            today_m = dict(block.get("today") or {})
            expected = list(hours_of(spec)) if spec is not None else []
            got = [int(r["saat"]) for r in hour_rows]
            print(f"  {key} kind={block.get('kind')} saatler={got} beklenen={expected}")
            if got != expected:
                print("  HATA: saat listesi bloktan turemedi")
                ok = False
            for metric in ADD_KEYS:
                block_n = int(today_m.get(metric) or 0)
                hour_n = sum(int(r.get(metric) or 0) for r in hour_rows)
                mark = "ok" if block_n == hour_n else "HATA"
                if block_n != hour_n:
                    ok = False
                print(f"    {metric}: kart={block_n} saat={hour_n} {mark}")
            b_sure = today_m.get("sure_toplam")
            h_sure = sum(
                float(r["sure_toplam"])
                for r in hour_rows
                if r.get("sure_toplam") is not None
            )
            if b_sure is None and h_sure == 0:
                print("    sure_toplam: kart=None saat=0 ok")
            else:
                left = float(b_sure or 0)
                if abs(left - h_sure) > 0.51:
                    print(
                        f"    sure_toplam: kart={left} saat={h_sure} HATA"
                    )
                    ok = False
                else:
                    print(
                        f"    sure_toplam: kart={left} saat={h_sure} ok"
                    )
        print(f"  planli_keys={planned_keys} hist_n={data.get('hist_n')}")
        return ok

    sat = day - timedelta(days=(day.weekday() - 5) % 7)
    friday = sat - timedelta(days=1)
    sums_ok = _check_hour_sums(None, "ekip cuma", friday)
    sums_ok = _check_hour_sums(rep_id, "temsilci cuma", friday) and sums_ok
    sums_ok = _check_hour_sums(None, "ekip cumartesi", sat) and sums_ok
    if not sums_ok:
        print("hata: saat satirlari blok toplamini tutmuyor")
        return 1

    sat_data = today_blocks(None, sat)
    sat_planned = [
        b
        for b in (sat_data.get("blocks") or [])
        if str(b.get("key") or "") != "blok_disi"
    ]
    hist_n = int(sat_data.get("hist_n") or 0)
    print("cumartesi canli:")
    print(f"  gun={sat.isoformat()} adet={len(sat_planned)}")
    print(f"  keys={[b.get('key') for b in sat_planned]}")
    print(f"  labels={[b.get('label') for b in sat_planned]}")
    print(f"  kinds={[b.get('kind') for b in sat_planned]}")
    print(f"  gecmis cumartesi verisi={hist_n}")
    if sat_planned:
        print(f"  badge_ok={sat_planned[0].get('badge_ok')}")
        today_m = dict(sat_planned[0].get("today") or {})
        print(
            f"  ham arama={today_m.get('arama')} randevu={today_m.get('randevu')} "
            f"katildi={today_m.get('katildi')} ulasilan={today_m.get('ulasilan')}"
        )
    weekday_names = [
        str(b.get("label") or "")
        for b in sat_planned
        if "arama blogu" in str(b.get("label") or "")
        or "toplanti blogu" in str(b.get("label") or "")
    ]
    if weekday_names:
        print(f"hata: cumarteside hafta ici etiketi var: {weekday_names}")
        return 1
    if len(sat_planned) != 1 or sat_planned[0].get("kind") != "mixed":
        print("hata: cumartesi tek karisik blok degil")
        return 1
    if hist_n < 4 and sat_planned[0].get("badge_ok"):
        print("hata: 4'ten az cumartesi verisinde rozet acik")
        return 1
    if hist_n < 4:
        print("  rozet yok, kiyas veri yetersiz")
    else:
        print("  rozet esigi gecti")

    board = workload_board(None, 3.0, 6.0)
    print(
        f"is gunu/doluluk: workdays={board.get('workdays')} "
        f"doluluk={board.get('doluluk')}"
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

    print("panel_check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
