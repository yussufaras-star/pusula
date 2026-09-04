"""Panel hazırlık çubuğu — eşikler, blok durumu, mesai dışı uyarı yok.

Kullanım:
    python scripts/panel_check.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from pusula.blocks import ISTANBUL, PLANNED_BLOCKS, card_phase
from pusula.freshness import FRESHNESS_THRESHOLDS, is_mesai
from pusula.panel_status import (
    BlockReady,
    format_block_line,
    format_impact_line,
    format_source_line,
    load_panel_readiness,
    should_warn,
)


def _phase_label(phase: str) -> str:
    if phase == "baslamadi":
        return "bekleniyor"
    if phase == "devam_ediyor":
        return "devam ediyor"
    return "tamamlandi"


def main() -> int:
    print("esikler:")
    for key, value in FRESHNESS_THRESHOLDS.items():
        print(f"  {key}: {value}")
    now = datetime.now(ISTANBUL)
    print(f"simdi: {now.isoformat(timespec='minutes')} mesai={is_mesai(now)}")
    weekend = now + timedelta(days=(5 - now.weekday()) % 7 or 7)
    weekend = weekend.replace(hour=12, minute=0, second=0, microsecond=0)
    night = now.replace(hour=21, minute=0, second=0, microsecond=0)
    print(f"hafta sonu mesai={is_mesai(weekend)}")
    print(f"gece mesai={is_mesai(night)}")
    if is_mesai(weekend) or is_mesai(night):
        print("hata: mesai disi True dondu")
        return 1

    ready = load_panel_readiness(now)
    print("bloklar:")
    print(f"  {format_block_line(ready.blocks)}")
    for item in ready.blocks:
        print(f"  {item.label} {item.state} written={item.written_at}")
    print("tazelik:")
    print(f"  {format_source_line(ready)}")
    print(f"  all_fresh={ready.all_fresh} warn={ready.warn}")
    off = load_panel_readiness(night)
    print(f"mesai disi warn={off.warn} (beklenen False)")
    if off.warn:
        print("hata: mesai disinda uyari")
        return 1
    failed = [item.label for item in ready.blocks if item.state == "calismadi"]
    print(f"calismadi: {failed if failed else '(yok)'}")

    weekday_ten = datetime(2026, 9, 3, 10, 0, tzinfo=ISTANBUL)
    weekday_noon = datetime(2026, 9, 3, 12, 0, tzinfo=ISTANBUL)
    ready_ten = load_panel_readiness(weekday_ten)
    print("10:00 senaryo:")
    print(f"  {format_block_line(ready_ten.blocks)}")
    print(f"  {format_source_line(ready_ten)}")
    print(f"  warn={ready_ten.warn} (beklenen False)")
    if ready_ten.warn:
        print("hata: 10:00 tazelik uyarisi")
        return 1
    waiting = [
        BlockReady(label="11:15", state="bekleniyor", written_at=None),
        BlockReady(label="14:15", state="bekleniyor", written_at=None),
        BlockReady(label="17:15", state="bekleniyor", written_at=None),
        BlockReady(label="18:15", state="bekleniyor", written_at=None),
    ]
    if should_warn(waiting, weekday_ten):
        print("hata: ilk blok oncesi uyari")
        return 1

    missed = [
        BlockReady(label="11:15", state="calismadi", written_at=None),
        BlockReady(label="14:15", state="bekleniyor", written_at=None),
        BlockReady(label="17:15", state="bekleniyor", written_at=None),
        BlockReady(label="18:15", state="bekleniyor", written_at=None),
    ]
    missed_warn = should_warn(missed, weekday_noon)
    print("11:15 kacmis senaryo:")
    print(f"  {format_block_line(missed)}")
    print(f"  warn={missed_warn} (beklenen True)")
    if not missed_warn:
        print("hata: 11:15 kacmis uyarisi yok")
        return 1
    ready_noon = load_panel_readiness(weekday_noon)
    print("12:00 canli (3 Eylul):")
    print(f"  {format_block_line(ready_noon.blocks)}")
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

    from pusula.blocks import hours_of
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

    def _check_hour_sums(owner: str | None, label: str) -> bool:
        data = today_blocks(owner, day)
        rows = today_hours(owner, day)
        print(f"saat vs blok ({label}, {day.isoformat()}):")
        ok = True
        for block in data.get("blocks") or []:
            key = str(block.get("key") or "")
            if key == "blok_disi":
                continue
            spec = next((b for b in PLANNED_BLOCKS if b.key == key), None)
            hour_rows = hours_for_block(rows, key)
            today = dict(block.get("today") or {})
            expected = list(hours_of(spec)) if spec is not None else []
            got = [int(r["saat"]) for r in hour_rows]
            print(f"  {key} saatler={got} beklenen={expected}")
            if got != expected:
                print("  HATA: saat listesi bloktan turemedi")
                ok = False
            for metric in ADD_KEYS:
                block_n = int(today.get(metric) or 0)
                hour_n = sum(int(r.get(metric) or 0) for r in hour_rows)
                mark = "ok" if block_n == hour_n else "HATA"
                if block_n != hour_n:
                    ok = False
                print(f"    {metric}: kart={block_n} saat={hour_n} {mark}")
            b_sure = today.get("sure_toplam")
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
        return ok

    sums_ok = _check_hour_sums(None, "ekip")
    sums_ok = _check_hour_sums(rep_id, "temsilci") and sums_ok
    if not sums_ok:
        print("hata: saat satirlari blok toplamini tutmuyor")
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
