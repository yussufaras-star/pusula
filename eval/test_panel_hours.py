"""Blok saat kırılımı, payda eşiği, yıl kıyası, cumartesi — DB yok."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from pusula.blocks import (
    BLOK_DISI,
    PLANNED_BLOCKS,
    SATURDAY_BLOCK,
    blocks_for,
    display_hours,
    hours_of,
)
from pusula.freshness import is_mesai
from pusula.panel_ciro import has_prior_year_same_month
from pusula.panel_data import (
    CONV_START,
    GUN_SAAT,
    RATE_MIN_N,
    SAT_SAAT,
    all_data_window,
    per_person_metrics,
    rate_cell,
    sum_hour_rows,
)
from pusula.panel_status import last_due_slot, next_ingest_at

_TZ = ZoneInfo("Europe/Istanbul")


def test_hours_of_planned_blocks() -> None:
    by_key = {b.key: hours_of(b) for b in PLANNED_BLOCKS}
    assert by_key["arama_09_11"] == (9, 10)
    assert by_key["toplanti_11_14"] == (11, 12, 13)
    assert by_key["arama_14_17"] == (14, 15, 16)
    assert by_key["toplanti_17_18"] == (17,)
    assert hours_of(BLOK_DISI) == ()
    assert hours_of(SATURDAY_BLOCK) == (9, 10, 11, 12, 13, 14)


def test_blocks_for_weekday_saturday_sunday() -> None:
    friday = date(2026, 9, 4)
    saturday = date(2026, 9, 5)
    sunday = date(2026, 9, 6)
    assert blocks_for(friday) == PLANNED_BLOCKS
    assert blocks_for(saturday) == (SATURDAY_BLOCK,)
    assert blocks_for(sunday) == ()
    assert SATURDAY_BLOCK.kind == "mixed"
    assert "arama" not in SATURDAY_BLOCK.label
    assert "toplanti" not in SATURDAY_BLOCK.label


def test_all_data_window_starts_at_conv() -> None:
    window = all_data_window()
    assert window.start == CONV_START.date()
    assert window.end >= window.start


def test_rate_cell_hides_small_payda() -> None:
    assert rate_cell(40.0, 4) == "veri yetersiz"
    assert rate_cell(40.0, RATE_MIN_N - 1) == "veri yetersiz"
    assert rate_cell(None, 10) == "—"
    shown = rate_cell(40.0, 5)
    assert shown.startswith("%")
    assert "40" in shown


def test_per_person_divides_counts_keeps_rates() -> None:
    raw = {
        "arama": 8,
        "ulasilan": 4,
        "ulasma_orani": 50.0,
        "sure_ort": 90.0,
        "lead_payda": 20,
    }
    avg = per_person_metrics(raw, 4)
    assert avg["arama"] == 2.0
    assert avg["ulasilan"] == 1.0
    assert avg["ulasma_orani"] == 50.0
    assert avg["sure_ort"] == 90.0
    assert avg["lead_payda"] == 20


def test_prior_year_same_month_detects_pair() -> None:
    rows = [
        {"ay": date(2025, 3, 1), "adet": 2},
        {"ay": date(2026, 3, 1), "adet": 5},
        {"ay": date(2026, 4, 1), "adet": 1},
    ]
    assert has_prior_year_same_month(rows) is True
    only_this = [{"ay": date(2026, 1, 1), "adet": 3}]
    assert has_prior_year_same_month(only_this) is False
    only_last = [{"ay": date(2025, 6, 1), "adet": 3}]
    assert has_prior_year_same_month(only_last) is False


def test_is_mesai_saturday_sunday() -> None:
    sat = datetime(2026, 9, 5, 12, 0, tzinfo=_TZ)
    sat_late = datetime(2026, 9, 5, 15, 0, tzinfo=_TZ)
    sun = datetime(2026, 9, 6, 12, 0, tzinfo=_TZ)
    night = datetime(2026, 9, 4, 21, 0, tzinfo=_TZ)
    assert is_mesai(sat) is True
    assert is_mesai(sat_late) is False
    assert is_mesai(sun) is False
    assert is_mesai(night) is False


def test_ingest_slots_skip_sunday_and_saturday_evening() -> None:
    after_sat = datetime(2026, 9, 5, 15, 30, tzinfo=_TZ)
    nxt = next_ingest_at(after_sat)
    assert nxt is not None
    assert nxt.date() == date(2026, 9, 7)
    assert nxt.hour == 9
    assert nxt.minute == 7
    due = last_due_slot(after_sat)
    assert due is not None
    assert due.hour == 15
    assert due.minute == 7


def test_display_hours_weekday_saturday_sunday() -> None:
    friday = date(2026, 9, 4)
    saturday = date(2026, 9, 5)
    sunday = date(2026, 9, 6)
    assert display_hours(friday) == (9, 10, 11, 12, 13, 14, 15, 16, 17)
    assert display_hours(saturday) == (9, 10, 11, 12, 13, 14)
    assert display_hours(sunday) == ()


def test_sum_hour_rows_counts_and_pooled_rates() -> None:
    rows = [
        {
            "arama": 3,
            "donus": 1,
            "gelen": 0,
            "ulasilan": 2,
            "randevu": 1,
            "katildi": 1,
            "katilmadi": 0,
            "sonuc_girilmedi": 0,
            "lead_payda": 4,
            "lead_pay": 2,
            "sure_toplam": 10.0,
        },
        {
            "arama": 2,
            "donus": 0,
            "gelen": 1,
            "ulasilan": 1,
            "randevu": 1,
            "katildi": 0,
            "katilmadi": 1,
            "sonuc_girilmedi": 0,
            "lead_payda": 2,
            "lead_pay": 1,
            "sure_toplam": 5.0,
        },
    ]
    total = sum_hour_rows(rows)
    assert total["arama"] == 5
    assert total["donus"] == 1
    assert total["gelen"] == 1
    assert total["ulasilan"] == 3
    assert total["randevu"] == 2
    assert total["katildi"] == 1
    assert total["sure_toplam"] == 15.0
    assert total["ulasma_orani"] == 50.0
    assert total["katilim_orani"] == 50.0


def test_occupancy_hours_constants() -> None:
    assert GUN_SAAT == 9.0
    assert SAT_SAAT == 6.0
