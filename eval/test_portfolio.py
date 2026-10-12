"""Portföy: 24 saattir dokunulmamış yeni aday kuralı ve Cliq listesi."""

from datetime import datetime, timedelta

from pusula.metrics import TZ
from pusula.pace_nudges import PORTFOLIO_LIST_MAX, format_portfolio
from pusula.portfolio import untouched_rows

NOW = datetime(2026, 10, 13, 10, 15, tzinfo=TZ)


def ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def test_untouched_uses_latest_of_touch_and_assignment() -> None:
    rows = [
        ("1", "r1", "Hiç aranmamış eski", ago(100), None, None),
        ("2", "r1", "Yeni atanmış", ago(3), None, None),          # 24 saat dolmadı
        ("3", "r1", "Dün arandı", ago(200), ago(20), None),      # dokunuldu
        ("4", "r1", "CRM'de güncellendi", ago(200), None, ago(5)),
        ("5", "r2", "Eski temas", ago(300), ago(48), ago(30)),
        ("6", "r2", "Atanma bilinmiyor", None, None, None),
    ]
    out = untouched_rows(rows, NOW)
    assert [r["lead_id"] for r in out] == ["1", "5", "6"]
    assert out[0]["hours"] == 100.0 and out[0]["last_touch"] is None
    assert out[1]["hours"] == 30.0 and out[1]["last_touch"] == ago(30)
    assert out[2]["hours"] is None


def test_portfolio_message_lists_longest_waiting_first() -> None:
    leads = untouched_rows(
        [(str(i), "r1", f"Lead {i}", ago(30 + i), ago(30 + i) if i % 2 else None, None) for i in range(12)],
        NOW,
    )
    body = format_portfolio("Ayşe Kar", leads, NOW)
    lines = body.splitlines()
    assert lines[1].startswith('Ayşe, 24 saattir dokunulmamış 12 "Yeni Müşteri Adayı"')
    assert lines[2].startswith("- Lead 11 (son temas")
    assert lines[3].startswith("- Lead 10 (hiç temas yok, atanma")
    assert sum(line.startswith("- ") for line in lines) == PORTFOLIO_LIST_MAX
    assert "…ve 2 lead daha" in body
    assert lines[-1] == "Görüştükten sonra CRM'de statüyü güncellemeyi unutma."
