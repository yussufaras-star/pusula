"""Zoho People izinleri: yalnız onaylı, tam gün, eşleşen temsilci."""

from datetime import date

from pusula.ingest.people_leaves import full_days

REPS = {"miray aksel": "r1", "ayse kar": "r2"}


def rec(name, status="Approved", days=None, unit="Days"):
    return {"Employee": name, "ApprovalStatus": status, "Unit": unit,
            "Days": days or {"15-Oct-2026": {"LeaveCount": "1.0"}}}


def test_only_approved_full_days_of_known_reps() -> None:
    records = {
        "1": rec("Miray Aksel"),
        "2": rec("Ayşe Kar", days={"12-Oct-2026": {"LeaveCount": "1.0"}, "13-Oct-2026": {"LeaveCount": "0.5"}}),
        "3": rec("Ayşe Kar", status="Rejected", days={"20-Oct-2026": {"LeaveCount": "1.0"}}),
        "4": rec("Ayşe Kar", status="Pending", days={"21-Oct-2026": {"LeaveCount": "1.0"}}),
        "5": rec("Miray Aksel", unit="Hours", days={"22-Oct-2026": {"LeaveCount": "2"}}),
        "6": rec("Yusuf Aras"),
    }
    days, unmatched = full_days(records, REPS)
    assert days == {("r1", date(2026, 10, 15)), ("r2", date(2026, 10, 12))}
    assert unmatched == {"Yusuf Aras"}
