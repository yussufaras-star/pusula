"""Doluluk plan adedi ve süre varsayımı. DB yok."""

from pusula.panel_data import (
    CRM_DK_PER_GORUSME,
    CRM_SN_PER_ULASILAMAYAN,
    MESAI_SAT_SAAT,
    MESAI_WD_SAAT,
    OLU_ZAMAN_SN,
    TOPLANTI_DK,
    WORKLOAD_ROWS,
    DolulukVarsayim,
    apply_plan_counts,
    mesai_avail_dk,
    occupancy_pay_dk,
)


def _board() -> dict[str, object]:
    plan = {
        "lead": 2.04,
        "arama": 6.14,
        "ulasilan": 2.26,
        "randevu": 1.04,
        "toplanti": 6.04,
        "crm_miss": 3.88,
        "crm_hit": 2.0,
        "olu": 6.14,
    }
    actual = {key: 1.0 for key, _label, _src in WORKLOAD_ROWS}
    rows: list[dict[str, object]] = []
    for key, label, kaynak in WORKLOAD_ROWS:
        rows.append(
            {
                "iş": label,
                "planlanan": round(plan[key], 1),
                "plan dk": 0.0,
                "gerçekleşen": 1.0,
                "gerçek dk": 3.0,
                "süre kaynağı": kaynak,
                "plan gerçekleşme": None,
            }
        )
    return {
        "rows": rows,
        "plan_saat": 0.0,
        "gercek_saat": 1.25,
        "doluluk": 42.0,
        "pay_dk": 480.0,
        "miss_sn": 20.0,
        "hit_sn": 90.0,
        "workdays": 64,
        "scale": 2.0,
        "plan_raw": plan,
        "actual_raw": actual,
        "varsayim": DolulukVarsayim(),
    }


def _shown_counts(board: dict[str, object]) -> list[float]:
    rows = board["rows"]
    assert isinstance(rows, list)
    return [float(row["planlanan"]) for row in rows]


def _counts_with(board: dict[str, object], **edits: float) -> list[float]:
    rows = board["rows"]
    assert isinstance(rows, list)
    counts: list[float] = []
    for (key, _label, _src), row in zip(WORKLOAD_ROWS, rows):
        counts.append(float(edits.get(key, row["planlanan"])))
    return counts


def _row(out: dict[str, object], key: str) -> dict[str, object]:
    rows = out["rows"]
    assert isinstance(rows, list)
    for row, (row_key, _label, _src) in zip(rows, WORKLOAD_ROWS):
        if row_key == key:
            assert isinstance(row, dict)
            return row
    raise AssertionError(key)


def test_rounded_plan_keeps_raw() -> None:
    board = _board()
    out = apply_plan_counts(board, _shown_counts(board))
    raw = out["plan_raw"]
    assert isinstance(raw, dict)
    assert raw["toplanti"] == 6.04
    assert raw["lead"] == 2.04
    assert _row(out, "toplanti")["plan dk"] == round(6.04 * TOPLANTI_DK, 1)
    assert _row(out, "toplanti")["gerçek dk"] == 3.0
    assert out["doluluk"] == 42.0
    assert out["gercek_saat"] == 1.25


def test_meeting_count_rescales_plan_minutes() -> None:
    board = _board()
    base = apply_plan_counts(board, _shown_counts(board))
    out = apply_plan_counts(board, _counts_with(board, toplanti=8.0))
    raw = out["plan_raw"]
    assert isinstance(raw, dict)
    assert raw["toplanti"] == 8.0
    assert _row(out, "toplanti")["plan dk"] == 240.0
    assert _row(out, "toplanti")["gerçek dk"] == 3.0
    assert _row(out, "toplanti")["plan gerçekleşme"] == 12.5
    assert out["doluluk"] == base["doluluk"]
    assert out["toplam_oran"] != base["toplam_oran"]


def test_crm_hit_assumption_rescales_plan_minutes() -> None:
    board = _board()
    default = apply_plan_counts(board, _shown_counts(board))
    edited = apply_plan_counts(
        board, _shown_counts(board), DolulukVarsayim(crm_dk_hit=3.0)
    )
    assert _row(default, "crm_hit")["plan dk"] == round(2.0 * CRM_DK_PER_GORUSME, 1)
    assert _row(edited, "crm_hit")["plan dk"] == 6.0
    assert _row(edited, "crm_hit")["gerçek dk"] == 3.0


def test_measured_seconds_stay_when_plan_changes() -> None:
    board = _board()
    out = apply_plan_counts(
        board,
        _counts_with(board, arama=20.0),
        DolulukVarsayim(olu_sn=40.0),
    )
    assert out["miss_sn"] == 20.0
    assert out["hit_sn"] == 90.0
    assert _row(out, "arama")["gerçek dk"] == 3.0
    ulasilan = 2.26
    plan_dk = (max(20.0 - ulasilan, 0.0) * 20.0 + ulasilan * 90.0) / 60.0
    assert _row(out, "arama")["plan dk"] == round(plan_dk, 1)
    assert _row(out, "olu")["plan dk"] == round(6.14 * (40.0 / 60.0), 1)


def test_occupancy_defaults_and_override() -> None:
    varsayim = DolulukVarsayim()
    assert varsayim.toplanti_dk == TOPLANTI_DK
    assert varsayim.crm_sn_miss == CRM_SN_PER_ULASILAMAYAN
    assert varsayim.crm_dk_hit == CRM_DK_PER_GORUSME
    assert varsayim.olu_sn == OLU_ZAMAN_SN
    assert varsayim.mesai_wd == MESAI_WD_SAAT
    assert varsayim.mesai_sat == MESAI_SAT_SAAT
    assert mesai_avail_dk(1, 0, 1) == 480.0
    custom = DolulukVarsayim(mesai_wd=7.0, mesai_sat=4.0)
    assert mesai_avail_dk(1, 1, 1, varsayim=custom) == (7.0 + 4.0) * 60.0
    pay = occupancy_pay_dk(
        call_sec=600.0,
        meet_dk=30.0,
        unreached=10.0,
        reached=4.0,
        arama=14.0,
        varsayim=DolulukVarsayim(crm_sn_miss=60.0, crm_dk_hit=3.0, olu_sn=0.0),
    )
    assert pay == 600.0 / 60.0 + 30.0 + 10.0 + 4.0 * 3.0


def test_help_points_at_workload_fields() -> None:
    from app.panel import HELP_DOLULUK, HELP_ISYUKU

    assert "İş yükü" in HELP_DOLULUK
    assert "1.5" not in HELP_DOLULUK
    assert "30 sn" not in HELP_DOLULUK
    assert "canlı plan" in HELP_ISYUKU
