"""Görüşme kayıtlarının indirilmesi: telefon (Verimor) ve toplantı (Zoho Meeting).

Telefon: CRM Calls kaydındaki Voice_Recording__s phonebridge adresidir ve
OAuth ile inmez; ama sonundaki kod Verimor'un call_uuid'sidir. Verimor
POST /recording_url/ bir saatlik indirme adresi verir. Bu uç dakikada en
fazla 5 istek kabul eder; istekler arasına aralık konur.

Toplantı: Zoho Meeting {zsoid}/recordings.json (kapsam
ZohoMeeting.recording.READ) kayıtları listeler; downloadUrl aynı token'la
iner (kapsam ZohoMeeting.meetinguds.READ + ZohoFiles.files.READ). Token
ZOHO_MEETING_REFRESH_TOKEN'dır (katılım senkronuyla ortak).
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from pusula.ingest import zoho_meeting

VERIMOR = "https://api.bulutsantralim.com"
# Verimor sınırı dakikada 5 istek: 13 sn aralık güvenli.
VERIMOR_GAP_SEC = 13.0
_UUID_RE = re.compile(r"/recording/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
_TIMEOUT = httpx.Timeout(300.0, connect=20.0)
_last_verimor = 0.0


class RecordingError(RuntimeError):
    pass


def call_uuid(voice_recording_url: str | None) -> str | None:
    """CRM ses kaydı adresinden Verimor call_uuid'si."""
    if not voice_recording_url:
        return None
    match = _UUID_RE.search(voice_recording_url)
    return match.group(1).lower() if match else None


def verimor_enabled() -> bool:
    return bool(os.environ.get("VERIMOR_API_KEY"))


def _verimor_wait() -> None:
    global _last_verimor
    gap = VERIMOR_GAP_SEC - (time.monotonic() - _last_verimor)
    if gap > 0:
        time.sleep(gap)
    _last_verimor = time.monotonic()


def _stream_to(url: str, dest: Path, headers: dict[str, str] | None = None) -> Path:
    with httpx.stream("GET", url, headers=headers, timeout=_TIMEOUT, follow_redirects=True) as resp:
        if resp.status_code >= 400:
            resp.read()
            raise RecordingError(f"indirme HTTP {resp.status_code}: {resp.text[:200]}")
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
    if dest.stat().st_size == 0:
        raise RecordingError("boş dosya indi")
    return dest


def download_call(uuid: str, dest: Path) -> Path:
    """Verimor kaydını dest'e indirir (mp3)."""
    key = os.environ.get("VERIMOR_API_KEY")
    if not key:
        raise RecordingError("VERIMOR_API_KEY yok")
    for attempt in range(3):
        _verimor_wait()
        resp = httpx.post(f"{VERIMOR}/recording_url/", data={"key": key, "call_uuid": uuid}, timeout=60)
        if resp.status_code == 429:
            time.sleep(60)
            continue
        if resp.status_code != 200 or not resp.text.strip().startswith("http"):
            raise RecordingError(f"Verimor kayıt adresi alınamadı (HTTP {resp.status_code}): {resp.text[:200]}")
        return _stream_to(resp.text.strip(), dest)
    raise RecordingError("Verimor istek sınırı aşıldı")


def meeting_enabled() -> bool:
    return zoho_meeting.enabled()


def recording_start(rec: dict[str, Any]) -> datetime | None:
    return zoho_meeting._ms(rec.get("startTimeinMs") or rec.get("startTimeMillis"))


def list_meeting_recordings(since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Aralıktaki toplantı kayıtları. Yetki eksikse RecordingError."""
    zsoid = zoho_meeting._zsoid()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    index = 1
    while True:
        try:
            body = zoho_meeting._get(f"{zsoid}/recordings.json", {"index": index, "count": 100})
        except httpx.HTTPStatusError as exc:
            text = exc.response.text[:200]
            if "SCOPE" in text.upper():
                raise RecordingError(
                    "Zoho Meeting token'ında kayıt yetkisi yok (ZohoMeeting.recording.READ)"
                ) from exc
            raise RecordingError(f"Zoho Meeting kayıt listesi HTTP {exc.response.status_code}: {text}") from exc
        rows = body.get("recordings") or []
        fresh = [r for r in rows if str(r.get("recordingId") or r.get("id")) not in seen]
        for row in fresh:
            seen.add(str(row.get("recordingId") or row.get("id")))
            start = recording_start(row)
            if start is not None and since <= start < until:
                out.append(row)
        more = bool((body.get("meta") or {}).get("moreRecords"))
        # Sayfa parametresini yok sayan sürümde aynı sayfa döner: tekrar görülürse dur.
        if not fresh or not more:
            return out
        index += len(rows)


def download_meeting(rec: dict[str, Any], dest: Path) -> Path:
    url = rec.get("downloadUrl")
    if not url:
        raise RecordingError("kaydın indirme adresi yok")
    if rec.get("noAudioRecording") in (True, "true"):
        raise RecordingError("kayıtta ses yok")
    headers = {"Authorization": f"Zoho-oauthtoken {zoho_meeting._access_token()}"}
    try:
        return _stream_to(str(url), dest, headers)
    except RecordingError as exc:
        if "SCOPE" in str(exc).upper() or "401" in str(exc):
            raise RecordingError(
                "Zoho Meeting kaydı indirilemedi: token'a ZohoMeeting.meetinguds.READ ve "
                "ZohoFiles.files.READ kapsamı gerekli"
            ) from exc
        raise
