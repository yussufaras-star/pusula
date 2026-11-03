from datetime import datetime

from pusula.ingest.base import ISTANBUL, RawRecord
from pusula.ingest.crm_whatsapp import CrmWhatsappIngester

REP = "6847331000013134001"


def _ingester() -> CrmWhatsappIngester:
    ing = CrmWhatsappIngester()
    ing._reps = {REP: ("sales", True)}
    return ing


def _raw(**fields) -> RawRecord:
    payload = {
        "id": "1",
        "conversation_status__s": "Replied",
        "message_service__s": "WHATSAPP",
        "message_time__s": "2026-11-03T10:00:00+03:00",
        "record_owner__s": {"id": REP},
        **fields,
    }
    return RawRecord(source_ref="1@x", occurred_at=datetime(2026, 11, 3, 10, tzinfo=ISTANBUL), payload=payload)


def test_temsilcinin_yanitladigi_mesaj_sayilir():
    event = _ingester().to_event(_raw(replied_by__s={"id": REP}))
    assert event is not None and event.rep_id == REP


def test_yanitlayani_olmayan_otomasyon_mesaji_sayilmaz():
    ing = _ingester()
    assert ing.to_event(_raw()) is None
    assert ing.last_skip_reason == "otomasyon"
