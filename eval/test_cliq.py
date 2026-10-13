"""Cliq gönderimi: botun /message adresi ve ulaşma kontrolü."""

import json
from unittest import mock

import pytest

from pusula import block_share

URL = "https://cliq.zoho.com/api/v2/bots/pusula/incoming?zapikey=x"


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


def test_posts_to_bot_message_endpoint_with_userids(monkeypatch) -> None:
    monkeypatch.setenv("CLIQ_WEBHOOK_URL", URL)
    sent = {}

    def fake(req, timeout):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data)
        return _Resp(b'{"user_ids":["1"],"users":["1"]}')

    with mock.patch.object(block_share.urllib.request, "urlopen", fake):
        assert block_share.post_cliq("merhaba", "a@rexven.com") == ["1"]
    # /incoming webhook betiği alıcıyı yok sayıp yöneticiye iletiyordu.
    assert sent["url"] == "https://cliq.zoho.com/api/v2/bots/pusula/message?zapikey=x"
    assert sent["body"] == {"text": "merhaba", "userids": "a@rexven.com", "broadcast": False}


def test_raises_when_nobody_reached(monkeypatch) -> None:
    monkeypatch.setenv("CLIQ_WEBHOOK_URL", URL)
    with mock.patch.object(block_share.urllib.request, "urlopen", lambda req, timeout: _Resp(b'{"users":[]}')):
        with pytest.raises(RuntimeError, match="ulaşmadı"):
            block_share.post_cliq("x", "b@rexven.com")
