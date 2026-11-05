"""Verimor'da silinmiş kayıt kalıcı hata sayılır (yeniden denenmez)."""

from pathlib import Path

import pytest

from pusula import recordings


class _Resp:
    status_code = 400
    text = "cannot find recording file for call 42f47e1b"


def test_deleted_recording_raises_gone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VERIMOR_API_KEY", "x")
    monkeypatch.setattr(recordings, "_verimor_wait", lambda: None)
    monkeypatch.setattr(recordings.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(recordings.RecordingGone):
        recordings.download_call("42f47e1b-2a95-4cbe-87c7-1f780ffd6561", tmp_path / "k.mp3")
