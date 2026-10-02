"""Blok saat kırılımı, payda eşiği, yıl kıyası, cumartesi — DB yok."""

from datetime import date, datetime, timedelta
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
    istanbul_sql,
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
    assert total["sonuc_girilmedi"] == 0
    assert total["toplanti_dk"] == 0.0
    assert total["toplanti_dk_hata"] == 0
    assert total["iptal_edildi"] == 0


def test_hour_table_frame_has_no_compare_marks() -> None:
    from app.panel import _hour_table_frame

    rows = [
        {
            "saat": 10,
            "arama": 5,
            "ulasilan": 2,
            "donus": 1,
            "gelen": 0,
            "ulasma_orani": 40.0,
            "lead_payda": 10,
            "lead_pay": 4,
            "sure_ort": 90.0,
            "sure_tipik": 80.0,
            "sure_toplam": 180.0,
            "randevu": 4,
            "katildi": 1,
            "katilmadi": 1,
            "iptal_edildi": 1,
            "sonuc_girilmedi": 1,
            "toplanti_dk": 90.0,
            "toplanti_dk_hata": 0,
        }
    ]
    ham = sum_hour_rows(rows)
    frame = _hour_table_frame(rows, date(2026, 9, 18))
    blob = frame.to_string()
    assert "↑" not in blob
    assert "↓" not in blob
    assert "ekip" not in blob.lower()
    last = frame.iloc[-1]
    leaves = {
        (col[-1] if isinstance(col, tuple) else str(col)): last[col]
        for col in last.index
    }
    assert leaves["saat"] == "gün toplamı"
    assert str(leaves["giden arama"]) == str(ham["arama"])
    assert str(leaves["ulaşılan görüşme"]) == str(ham["ulasilan"])
    assert str(leaves["dönüş araması"]) == str(ham["donus"])
    assert str(leaves["gelen arama"]) == str(ham["gelen"])
    assert str(leaves["toplantı"]) == str(ham["randevu"])
    assert str(leaves["katıldı"]) == str(ham["katildi"])
    assert str(leaves["katılmadı"]) == str(ham["katilmadi"])
    assert str(leaves["iptal edildi"]) == str(ham["iptal_edildi"])
    assert str(leaves["sonuç girilmedi"]) == str(ham["sonuc_girilmedi"])
    assert leaves["toplantı süresi"] == "1 sa 30 dk"
    assert " sn" not in str(leaves["görüşme süresi"])
    assert " sn" not in str(leaves["toplantı süresi"])
    kirilim = (
        ham["katildi"] + ham["katilmadi"] + ham["iptal_edildi"] + ham["sonuc_girilmedi"]
    )
    assert kirilim == ham["randevu"]
    first = frame.iloc[0]
    hour_leaves = {
        (col[-1] if isinstance(col, tuple) else str(col)): first[col]
        for col in first.index
    }
    assert " sn" in str(hour_leaves["görüşme süresi"])
    assert hour_leaves["toplantı süresi"] == "1 sa 30 dk"


def test_day_total_duration_uses_hours() -> None:
    from app.panel import _hour_table_frame
    from pusula.panel_data import (
        fmt_clock_span,
        fmt_duration,
        fmt_meet_minutes,
        parse_meet_duration_min,
    )

    assert parse_meet_duration_min("30 mins") == 30
    assert parse_meet_duration_min("1 hour") == 60
    assert parse_meet_duration_min("1 hour 30 mins") == 90
    assert parse_meet_duration_min("") is None
    assert parse_meet_duration_min(None) is None
    assert parse_meet_duration_min("abc") is None
    assert parse_meet_duration_min("30") is None

    raw_sn = 141 * 60 + 5
    assert fmt_duration(raw_sn) == "141 dk 5 sn"
    assert fmt_clock_span(raw_sn, day_total=True) == "2 sa 21 dk"
    assert fmt_clock_span(47 * 60 + 35) == "47 dk 35 sn"
    assert fmt_clock_span(48) == "48 sn"
    assert fmt_meet_minutes(30) == "30 dk"
    assert fmt_meet_minutes(90) == "1 sa 30 dk"
    assert fmt_meet_minutes(0) == "0 dk"
    assert fmt_meet_minutes(90, day_total=True) == "1 sa 30 dk"

    rows = [
        {
            "saat": 11,
            "arama": 2,
            "ulasilan": 1,
            "donus": 0,
            "gelen": 0,
            "ulasma_orani": None,
            "lead_payda": 1,
            "lead_pay": 0,
            "sure_ort": None,
            "sure_tipik": None,
            "sure_toplam": float(raw_sn),
            "randevu": 4,
            "katildi": 3,
            "katilmadi": 1,
            "sonuc_girilmedi": 0,
            "toplanti_dk": 90.0,
            "toplanti_dk_hata": 1,
        }
    ]
    ham = sum_hour_rows(rows)
    assert ham["sure_toplam"] == float(raw_sn)
    assert ham["toplanti_dk"] == 90.0
    assert ham["toplanti_dk_hata"] == 1
    frame = _hour_table_frame(rows, date(2026, 9, 18))
    hour = frame.iloc[0]
    total = frame.iloc[-1]

    def leaf(series: object, name: str) -> str:
        index = getattr(series, "index")
        for col in index:
            label = col[-1] if isinstance(col, tuple) else str(col)
            if label == name:
                return str(series[col])  # type: ignore[index]
        raise AssertionError(name)

    assert leaf(hour, "görüşme süresi") == "toplam 2 sa 21 dk"
    assert " sn" not in leaf(hour, "görüşme süresi")
    assert leaf(hour, "toplantı süresi") == "1 sa 30 dk"
    assert leaf(total, "görüşme süresi") == "toplam 2 sa 21 dk"
    assert leaf(total, "toplantı süresi") == "1 sa 30 dk"
    assert " sn" not in leaf(total, "görüşme süresi")
    assert "gerçekleşen" not in leaf(total, "toplantı süresi")


def test_hour_col_groups_arama_toplanti() -> None:
    from app.panel import HOUR_COL_GROUPS

    leaves = [leaf for _group, leaf in HOUR_COL_GROUPS]
    assert leaves == [
        "saat",
        "giden arama",
        "ulaşılan görüşme",
        "dönüş araması",
        "gelen arama",
        "ulaşma oranı",
        "görüşme süresi",
        "toplantı",
        "katıldı",
        "katılmadı",
        "iptal edildi",
        "sonuç girilmedi",
        "toplantı süresi",
    ]
    groups = [group for group, _leaf in HOUR_COL_GROUPS[1:]]
    assert groups[:6] == ["arama"] * 6
    assert groups[6:] == ["toplantı"] * 6


def test_hour_table_height_fits_every_row() -> None:
    from app.panel import _HOUR_HEADER_ROWS, _HOUR_ROW_PX, hour_table_height

    for n_rows in (7, 10):
        height = hour_table_height(n_rows)
        assert height >= (n_rows + _HOUR_HEADER_ROWS) * _HOUR_ROW_PX


def test_occupancy_hours_constants() -> None:
    from pusula.panel_data import MESAI_SAT_SAAT, MESAI_WD_SAAT

    assert GUN_SAAT == 9.0
    assert SAT_SAAT == 6.0
    assert MESAI_WD_SAAT == 8.0
    assert MESAI_SAT_SAAT == 5.0


def test_occupancy_pay_no_double_count() -> None:
    from pusula.panel_data import (
        CRM_DK_PER_GORUSME,
        CRM_SN_PER_ULASILAMAYAN,
        OLU_ZAMAN_SN,
        occupancy_pay_dk,
        _cap_doluluk,
        mesai_avail_dk,
    )

    call_sec = 600.0
    meet_dk = 30.0
    unreached = 10.0
    reached = 4.0
    arama = 14.0
    pay = occupancy_pay_dk(
        call_sec=call_sec,
        meet_dk=meet_dk,
        unreached=unreached,
        reached=reached,
        arama=arama,
    )
    expected = (
        call_sec / 60.0
        + meet_dk
        + unreached * (CRM_SN_PER_ULASILAMAYAN / 60.0)
        + reached * CRM_DK_PER_GORUSME
        + arama * (OLU_ZAMAN_SN / 60.0)
    )
    assert pay == expected
    # Ulaşılan görüşme süresi call_sec içinde; ikinci kez eklenmez.
    doubled = pay + call_sec / 60.0
    assert doubled != pay
    assert mesai_avail_dk(1, 0, 1) == 8.0 * 60.0
    assert mesai_avail_dk(0, 1, 1) == 5.0 * 60.0
    assert _cap_doluluk(80.0, detail="ok") == 80.0
    assert _cap_doluluk(140.0, detail="test asim") == 100.0


def test_istanbul_sql_wraps_timestamptz_expr() -> None:
    assert istanbul_sql("e.occurred_at") == (
        "(e.occurred_at AT TIME ZONE 'Europe/Istanbul')"
    )
    assert istanbul_sql("coalesce(closed_at, created_at)") == (
        "(coalesce(closed_at, created_at) AT TIME ZONE 'Europe/Istanbul')"
    )
    assert istanbul_sql("now()") == "(now() AT TIME ZONE 'Europe/Istanbul')"


def test_week_window_current_and_past() -> None:
    from pusula.panel_data import week_window

    today = date(2026, 10, 2)
    current = week_window(today, today=today)
    assert current.start == today - timedelta(days=today.weekday())
    assert current.end == today
    assert current.start.weekday() == 0

    past_day = date(2026, 9, 16)
    past = week_window(past_day, today=today)
    assert past.start == past_day - timedelta(days=past_day.weekday())
    assert past.end == past.start + timedelta(days=6)
    assert past.end < today

    sunday = past.start + timedelta(days=6)
    assert sunday.weekday() == 6
    from_sunday = week_window(sunday, today=today)
    assert from_sunday.start == past.start
    assert from_sunday.end == sunday


def test_activity_total_adds_phone_and_meeting_once() -> None:
    from pusula.panel_data import activity_total_sec

    assert activity_total_sec(600, 30) == 600 + 30 * 60
    assert activity_total_sec(0, 0) == 0
    assert activity_total_sec(-5, -2) == 0


def test_phone_talk_and_attended_meeting_sql() -> None:
    from pusula.panel_data import _attended_meeting_sql, _phone_talk_sql

    phone = _phone_talk_sql("e")
    assert "e.direction = 'outbound'" in phone
    assert "e.direction = 'inbound'" in phone
    assert "katildi" not in phone
    meeting = _attended_meeting_sql("e")
    assert "katildi" in meeting
    assert "katilmadi" not in meeting
    assert "iptal" not in meeting


def test_rank_activity_orders_by_combined_duration() -> None:
    from pusula.panel_data import rank_activity_rows

    rows = rank_activity_rows(
        [
            {
                "rep_id": "a",
                "temsilci": "Ayşe Kar",
                "phone_sec": 600,
                "meet_min": 30,
            },
            {
                "rep_id": "b",
                "temsilci": "Miray Aksel",
                "phone_sec": 3600,
                "meet_min": 0,
            },
            {
                "rep_id": "c",
                "temsilci": "Beytullah Aras",
                "phone_sec": 0,
                "meet_min": 0,
            },
        ]
    )
    assert [row["temsilci"] for row in rows] == [
        "Miray Aksel",
        "Ayşe Kar",
        "Beytullah Aras",
    ]
    assert [row["sira"] for row in rows] == [1, 2, 3]
    assert rows[0]["total_sec"] == 3600
    assert rows[1]["total_sec"] == 2400
    assert rows[2]["total_sec"] == 0


def test_rank_tie_breaks_by_name() -> None:
    from pusula.panel_data import rank_activity_rows

    rows = rank_activity_rows(
        [
            {"rep_id": "2", "temsilci": "Serkan", "phone_sec": 100, "meet_min": 0},
            {"rep_id": "1", "temsilci": "Abdullah", "phone_sec": 100, "meet_min": 0},
        ]
    )
    assert [row["temsilci"] for row in rows] == ["Abdullah", "Serkan"]
    assert rows[0]["sira"] == 1
    assert rows[1]["sira"] == 2


def test_activity_rank_table_shows_durations_and_team_total() -> None:
    from app.panel import _activity_rank_records
    from pusula.panel_data import apply_efficiency_notes, fmt_span, rank_activity_rows

    rows = apply_efficiency_notes(
        rank_activity_rows(
            [
                {
                    "rep_id": "b",
                    "temsilci": "Miray Aksel",
                    "phone_sec": 3600,
                    "meet_min": 0,
                    "talk_n": 10,
                    "meet_n": 0,
                },
                {
                    "rep_id": "a",
                    "temsilci": "Ayşe Kar",
                    "phone_sec": 600,
                    "meet_min": 30,
                    "talk_n": 4,
                    "meet_n": 1,
                },
            ]
        )
    )
    assert rows[0]["avg_sec"] == 360
    assert rows[1]["avg_sec"] == 150
    records = _activity_rank_records(rows)
    assert [row["temsilci"] for row in records] == [
        "Miray Aksel",
        "Ayşe Kar",
        "toplam",
    ]
    assert records[0]["sıra"] == "1"
    assert records[0]["telefon süresi"] == "1 sa"
    assert records[0]["toplantı süresi"] == "0 dk"
    assert records[0]["toplam süre"] == "1 sa"
    assert records[0]["ortalama görüşme"] == "6 dk"
    assert "Görüşme ortalaması ekibin üstünde" in records[0]["yorum"]
    assert "6 dk" in records[0]["yorum"]
    assert "Toplantı yok." in records[0]["yorum"]
    assert records[1]["telefon süresi"] == "10 dk"
    assert records[1]["toplantı süresi"] == "30 dk"
    assert records[1]["toplam süre"] == "40 dk"
    assert records[1]["ortalama görüşme"] == "2 dk 30 sn"
    assert "Görüşme ortalaması ekibin altında" in records[1]["yorum"]
    assert "Sürenin çoğu toplantıda." in records[1]["yorum"]
    assert "!" not in records[0]["yorum"]
    assert "!" not in records[1]["yorum"]
    assert records[2]["sıra"] == ""
    assert records[2]["telefon süresi"] == "1 sa 10 dk"
    assert records[2]["toplantı süresi"] == "30 dk"
    assert records[2]["toplam süre"] == "1 sa 40 dk"
    assert records[2]["ortalama görüşme"] == "5 dk"
    assert records[2]["yorum"] == ""
    assert "↑" not in str(records)
    assert "↓" not in str(records)
    assert fmt_span(date(2026, 9, 28), date(2026, 10, 2)) == (
        "28 Eylül – 2 Ekim 2026"
    )
    assert fmt_span(date(2026, 10, 2), date(2026, 10, 2)) == "2 Ekim 2026"


def test_efficiency_comment_skips_thin_average_and_empty_day() -> None:
    from pusula.panel_data import apply_efficiency_notes, efficiency_comment, rank_activity_rows

    team = {"avg_sec": 300.0, "total_sec": 3600.0}
    thin = efficiency_comment(
        {
            "phone_sec": 80.0,
            "meet_min": 0.0,
            "total_sec": 80.0,
            "talk_n": 2,
            "meet_n": 0,
            "avg_sec": 40.0,
        },
        team,
    )
    assert "veri yetersiz" in thin
    assert "Görüşme ortalaması" not in thin
    assert "2 görüşme" in thin

    empty = efficiency_comment(
        {
            "phone_sec": 0.0,
            "meet_min": 0.0,
            "total_sec": 0.0,
            "talk_n": 0,
            "meet_n": 0,
        },
        team,
    )
    assert empty == "Telefon ve toplantı kaydı yok."

    meeting_only = efficiency_comment(
        {
            "phone_sec": 0.0,
            "meet_min": 30.0,
            "total_sec": 1800.0,
            "talk_n": 0,
            "meet_n": 1,
        },
        team,
    )
    assert meeting_only == "Telefon görüşmesi yok. Süre toplantıdan geliyor."

    # Eşik tam %20. Ortalama 240 sn, ekip 300 sn.
    on_edge = efficiency_comment(
        {
            "phone_sec": 960.0,
            "meet_min": 0.0,
            "total_sec": 960.0,
            "talk_n": 4,
            "meet_n": 0,
            "avg_sec": 240.0,
        },
        {"avg_sec": 300.0, "total_sec": 960.0},
    )
    assert "Görüşme ortalaması ekibin altında" in on_edge
    assert "Müşteriyle geçen süre ekibe yakın" in on_edge

    inside = efficiency_comment(
        {
            "phone_sec": 1000.0,
            "meet_min": 0.0,
            "total_sec": 1000.0,
            "talk_n": 4,
            "meet_n": 0,
            "avg_sec": 250.0,
        },
        {"avg_sec": 300.0, "total_sec": 1000.0},
    )
    assert "Görüşme ortalaması ekibe yakın" in inside
    assert "Toplantı yok." in inside

    ranked = apply_efficiency_notes(
        rank_activity_rows(
            [
                {
                    "rep_id": "z",
                    "temsilci": "Boş",
                    "phone_sec": 0,
                    "meet_min": 0,
                    "talk_n": 0,
                    "meet_n": 0,
                }
            ]
        )
    )
    assert ranked[0]["yorum"] == "Telefon ve toplantı kaydı yok."
    assert ranked[0]["avg_sec"] is None
