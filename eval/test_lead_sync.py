"""Lead senkronu: Zoho kaydından sahip/statü okuma."""

from pusula.ingest.lead_sync import state_from_record


def test_state_from_record() -> None:
    rec = {"id": 5, "Owner": {"id": "77", "name": "Nihat"}, "Lead_Status": " Randevu "}
    assert state_from_record(rec) == ("5", "77", "Randevu")
    assert state_from_record({"id": "6", "Owner": None, "Lead_Status": ""}) == ("6", None, None)
    assert state_from_record({"Owner": {"id": "77"}}) is None
