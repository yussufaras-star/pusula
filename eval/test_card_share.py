"""Kart görseli paylaşımı: kanal dosya adresi ve PNG denetimi."""

import pytest

from pusula import card_share

URL = "https://cliq.zoho.com/api/v2/bots/pusula/incoming?zapikey=abc"
PNG = card_share.PNG_MAGIC + b"veri"


def test_files_url_uses_channel_and_bot(monkeypatch):
    monkeypatch.setenv("CLIQ_WEBHOOK_URL", URL)
    monkeypatch.delenv("CLIQ_SHARE_CHANNEL", raising=False)
    assert card_share._files_url(card_share.share_channel()) == (
        "https://cliq.zoho.com/api/v2/channelsbyname/salessupport/files?zapikey=abc&bot_unique_name=pusula"
    )


def test_channel_from_env(monkeypatch):
    monkeypatch.setenv("CLIQ_SHARE_CHANNEL", " baska ")
    assert card_share.share_channel() == "baska"


def test_rejects_non_png_and_large():
    card_share.validate_png(PNG)
    with pytest.raises(ValueError):
        card_share.validate_png(b"GIF89a")
    with pytest.raises(ValueError):
        card_share.validate_png(PNG + b"0" * card_share.MAX_BYTES)


def test_post_returns_message_ids(monkeypatch):
    monkeypatch.setenv("CLIQ_WEBHOOK_URL", URL)
    sent = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"message_ids":["1"]}'

    def fake_urlopen(req, timeout):
        sent["url"] = req.full_url
        sent["body"] = req.data
        return Resp()

    monkeypatch.setattr(card_share.urllib.request, "urlopen", fake_urlopen)
    assert card_share.post_channel_image(PNG, "pusula-prim.png", "Prim · 15.10 12:30") == ["1"]
    assert "/channelsbyname/salessupport/files" in sent["url"]
    assert b'filename="pusula-prim.png"' in sent["body"] and PNG in sent["body"]
