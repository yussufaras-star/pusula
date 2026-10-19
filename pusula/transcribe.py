"""Ses kaydı → konuşmacılı Türkçe metin.

Claude ses okumaz; kayıt önce bir konuşma-tanıma servisinden geçer. Sağlayıcı
ortam değişkeniyle seçilir, anahtarı olan ilk sağlayıcı varsayılandır:

- openai     OPENAI_API_KEY      /audio/transcriptions (OPENAI_BASE_URL ile
                                 Groq gibi uyumlu servisler de olur)
- elevenlabs ELEVENLABS_API_KEY  Scribe, konuşmacı ayrımı var
- deepgram   DEEPGRAM_API_KEY    konuşmacı ayrımı var

PUSULA_STT_PROVIDER sağlayıcıyı, PUSULA_STT_MODEL modeli zorlar.

Toplantı kaydı videodur: ffmpeg ile tek kanallı düşük bit hızlı mp3'e
indirilir, sağlayıcının dosya sınırını aşarsa parçalara bölünür.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

PROVIDERS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
}
DEFAULT_MODEL = {
    "openai": "gpt-4o-transcribe",
    "elevenlabs": "scribe_v1",
    "deepgram": "nova-2",
}
# OpenAI dosya sınırı 25 MB; pay bırakılır.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
SEGMENT_SEC = 20 * 60
_TIMEOUT = httpx.Timeout(600.0, connect=20.0)


class TranscribeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str
    model: str
    speakers: bool  # metin "Konuşmacı N:" satırlarına ayrılmış mı


def provider() -> str | None:
    forced = (os.environ.get("PUSULA_STT_PROVIDER") or "").strip().lower()
    if forced:
        return forced if forced in PROVIDERS and os.environ.get(PROVIDERS[forced]) else None
    return next((name for name, env in PROVIDERS.items() if os.environ.get(env)), None)


def enabled() -> bool:
    return provider() is not None


def model_for(name: str) -> str:
    return os.environ.get("PUSULA_STT_MODEL") or DEFAULT_MODEL[name]


def speaker_lines(turns: list[tuple[Any, str]]) -> str:
    """(konuşmacı, metin) dizisi → art arda aynı konuşmacı birleşmiş satırlar.

    Konuşmacı kimlikleri sağlayıcıdan sağlayıcıya değişir (0, "speaker_1",
    "A"); görünme sırasına göre 1, 2, ... diye yeniden adlandırılır.
    """
    names: dict[str, int] = {}
    lines: list[list[Any]] = []
    for speaker, text in turns:
        text = (text or "").strip()
        if not text:
            continue
        key = str(speaker)
        no = names.setdefault(key, len(names) + 1)
        if lines and lines[-1][0] == no:
            lines[-1][1] += " " + text
        else:
            lines.append([no, text])
    return "\n".join(f"Konuşmacı {no}: {text}" for no, text in lines)


def _elevenlabs_turns(body: dict[str, Any]) -> list[tuple[Any, str]]:
    turns: list[tuple[Any, str]] = []
    current: Any = None
    buf: list[str] = []
    for word in body.get("words") or []:
        if word.get("type") == "audio_event":
            continue
        speaker = word.get("speaker_id")
        if speaker != current and buf:
            turns.append((current, "".join(buf)))
            buf = []
        current = speaker
        buf.append(str(word.get("text") or ""))
    if buf:
        turns.append((current, "".join(buf)))
    return turns


def _post(url: str, **kwargs: Any) -> dict[str, Any]:
    for attempt in range(4):
        try:
            resp = httpx.post(url, timeout=_TIMEOUT, **kwargs)
        except httpx.TransportError as exc:
            if attempt == 3:
                raise TranscribeError(f"bağlantı hatası: {exc}") from exc
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 ** (attempt + 2))
            continue
        if resp.status_code >= 400:
            raise TranscribeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    raise TranscribeError("yanıt alınamadı")


def _openai(path: Path, model: str) -> tuple[str, bool]:
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    diarize = "diarize" in model
    data: dict[str, str] = {"model": model, "language": "tr"}
    if diarize:
        data["response_format"] = "diarized_json"
        data["chunking_strategy"] = "auto"
    with path.open("rb") as fh:
        body = _post(
            f"{base}/audio/transcriptions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            data=data,
            files={"file": (path.name, fh, "audio/mpeg")},
        )
    if diarize and body.get("segments"):
        return speaker_lines([(s.get("speaker"), s.get("text")) for s in body["segments"]]), True
    return str(body.get("text") or "").strip(), False


def _elevenlabs(path: Path, model: str) -> tuple[str, bool]:
    with path.open("rb") as fh:
        body = _post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
            data={"model_id": model, "language_code": "tur", "diarize": "true", "tag_audio_events": "false"},
            files={"file": (path.name, fh, "audio/mpeg")},
        )
    turns = _elevenlabs_turns(body)
    if turns and any(s is not None for s, _ in turns):
        return speaker_lines(turns), True
    return str(body.get("text") or "").strip(), False


def _deepgram(path: Path, model: str) -> tuple[str, bool]:
    body = _post(
        "https://api.deepgram.com/v1/listen",
        params={"model": model, "language": "tr", "diarize": "true", "utterances": "true",
                "smart_format": "true", "punctuate": "true"},
        headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}", "Content-Type": "audio/mpeg"},
        content=path.read_bytes(),
    )
    utterances = (body.get("results") or {}).get("utterances") or []
    if utterances:
        return speaker_lines([(u.get("speaker"), u.get("transcript")) for u in utterances]), True
    channels = (body.get("results") or {}).get("channels") or [{}]
    alt = (channels[0].get("alternatives") or [{}])[0]
    return str(alt.get("transcript") or "").strip(), False


_CALLERS = {"openai": _openai, "elevenlabs": _elevenlabs, "deepgram": _deepgram}


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise TranscribeError("ffmpeg bulunamadı (toplantı videosu sese çevrilemez)")
    return exe


def to_audio(src: Path, workdir: Path) -> list[Path]:
    """Video/ses → tek kanallı 16 kHz mp3; sınırı aşarsa 20 dk'lık parçalar."""
    out = workdir / "ses.mp3"
    subprocess.run(
        [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "32k", str(out)],
        check=True,
    )
    if out.stat().st_size <= MAX_UPLOAD_BYTES:
        return [out]
    pattern = workdir / "parca_%03d.mp3"
    subprocess.run(
        [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(out), "-f", "segment", "-segment_time",
         str(SEGMENT_SEC), "-c", "copy", str(pattern)],
        check=True,
    )
    return sorted(workdir.glob("parca_*.mp3"))


def transcribe_file(path: Path, *, video: bool = False) -> Transcript:
    name = provider()
    if name is None:
        raise TranscribeError("konuşma-tanıma anahtarı yok")
    model = model_for(name)
    call = _CALLERS[name]
    with tempfile.TemporaryDirectory(prefix="pusula-stt-") as tmp:
        parts = (
            to_audio(path, Path(tmp))
            if video or path.stat().st_size > MAX_UPLOAD_BYTES
            else [path]
        )
        texts: list[str] = []
        speakers = True
        for part in parts:
            text, diarized = call(part, model)
            texts.append(text)
            speakers = speakers and diarized
    # Parçalar ayrı tanındığı için konuşmacı numaraları parçalar arasında tutarsız olabilir.
    joined = "\n".join(t for t in texts if t).strip()
    return Transcript(text=joined, provider=name, model=model, speakers=speakers and len(parts) == 1)
