"""Blok saat kırılımı, payda eşiği, yıl kıyası — DB yok."""

from datetime import date

from pusula.blocks import BLOK_DISI, PLANNED_BLOCKS, hours_of
from pusula.panel_ciro import has_prior_year_same_month
from pusula.panel_data import (
    CONV_START,
    RATE_MIN_N,
    all_data_window,
    per_person_metrics,
    rate_cell,
)


def test_hours_of_planned_blocks() -> None:
    by_key = {b.key: hours_of(b) for b in PLANNED_BLOCKS}
    assert by_key["arama_09_11"] == (9, 10)
    assert by_key["toplanti_11_14"] == (11, 12, 13)
    assert by_key["arama_14_17"] == (14, 15, 16)
    assert by_key["toplanti_17_18"] == (17,)
    assert hours_of(BLOK_DISI) == ()


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
