"""Canlı ingest zamanlaması ve CRM işlem çıkarımı (veritabanı gerektirmez)."""

from datetime import datetime, timedelta

from pusula.ingest.crm_actions import actions_from_record, parse_zoho_time
from pusula.live_ingest import is_active, slow_due
from pusula.metrics import TZ

REPS = {"rep-1", "rep-2"}
SINCE = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def test_active_window_covers_shift_with_margins() -> None:
    assert is_active(at(12, 8, 30))  # pazartesi
    assert is_active(at(12, 18, 45))
    assert not is_active(at(12, 8, 29))
    assert not is_active(at(12, 19, 0))
    assert is_active(at(10, 15, 30))  # cumartesi
    assert not is_active(at(10, 16, 0))
    assert not is_active(at(11, 12, 0))  # pazar


def test_slow_sources_run_every_quarter_hour() -> None:
    now = at(12, 11, 0)
    assert slow_due(now, None)
    assert not slow_due(now, now - timedelta(minutes=5))
    assert slow_due(now, now - timedelta(minutes=15))


def test_parse_zoho_time_to_istanbul() -> None:
    parsed = parse_zoho_time("2026-10-12T07:30:00+00:00")
    assert parsed == at(12, 10, 30)
    assert parse_zoho_time("") is None
    assert parse_zoho_time(None) is None


def test_note_created_by_sales_rep_is_action() -> None:
    record = {
        "id": "n1",
        "Created_Time": "2026-10-12T10:15:00+03:00",
        "Created_By": {"id": "rep-1"},
        "Modified_Time": "2026-10-12T10:15:00+03:00",
        "Modified_By": {"id": "rep-1"},
    }
    actions = actions_from_record("Notes", record, since=SINCE, sales_reps=REPS)

    assert [(a.action, a.actor_rep_id) for a in actions] == [("created", "rep-1")]


def test_modification_by_other_rep_is_attributed_to_modifier() -> None:
    record = {
        "id": "l1",
        "Created_Time": "2026-09-01T10:00:00+03:00",
        "Created_By": {"id": "rep-1"},
        "Modified_Time": "2026-10-12T10:40:00+03:00",
        "Modified_By": {"id": "rep-2"},
    }
    actions = actions_from_record("Leads", record, since=SINCE, sales_reps=REPS)

    assert [(a.action, a.actor_rep_id, a.acted_at) for a in actions] == [
        ("modified", "rep-2", at(12, 10, 40))
    ]


def test_automation_and_non_sales_users_are_ignored() -> None:
    record = {
        "id": "l2",
        "Created_Time": "2026-10-12T10:20:00+03:00",
        "Created_By": {"id": "zoho-automation"},
        "Modified_Time": "2026-10-12T10:25:00+03:00",
        "Modified_By": {"id": "manager-9"},
    }
    assert actions_from_record("Leads", record, since=SINCE, sales_reps=REPS) == []


def test_actions_before_window_are_ignored() -> None:
    record = {
        "id": "t1",
        "Created_Time": "2026-10-12T09:00:00+03:00",
        "Created_By": {"id": "rep-1"},
        "Modified_Time": "2026-10-12T09:30:00+03:00",
        "Modified_By": {"id": "rep-1"},
    }
    assert actions_from_record("Tasks", record, since=SINCE, sales_reps=REPS) == []


def test_whatsapp_step_is_on_unless_disabled(monkeypatch) -> None:
    from pusula.live_ingest import _steps

    monkeypatch.delenv("PUSULA_WHATSAPP_INGEST", raising=False)
    names = [name for name, _ in _steps(at(12, 11), run_slow=True)]
    assert names == ["aramalar", "whatsapp", "crm_islemleri", "randevular", "leadler", "kisiler"]

    monkeypatch.setenv("PUSULA_WHATSAPP_INGEST", "0")
    names = [name for name, _ in _steps(at(12, 11), run_slow=False)]
    assert names == ["aramalar", "crm_islemleri"]
