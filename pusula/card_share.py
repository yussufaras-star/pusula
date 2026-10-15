"""Panel kartının görselini ekip Cliq kanalına gönderir (buton ve günlük iş)."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import uuid

from pusula.block_share import _webhook_url

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 3 * 1024 * 1024
DEFAULT_CHANNEL = "salessupport"


def share_channel() -> str:
    """Kanalın Cliq benzersiz adı (bağlantının sonundaki ad, görünen ad değil)."""
    return (os.environ.get("CLIQ_SHARE_CHANNEL") or "").strip() or DEFAULT_CHANNEL


def _files_url(channel: str) -> str:
    # Bot webhook'unun zapikey'i kanal dosya adresinde de geçer; bot kanalda üye olmalı.
    webhook = _webhook_url()
    root, _, rest = webhook.partition("/bots/")
    bot = rest.split("/", 1)[0]
    key = urllib.parse.parse_qs(urllib.parse.urlsplit(webhook).query).get("zapikey", [""])[0]
    if not bot or not key:
        raise RuntimeError("CLIQ_WEBHOOK_URL bot adı/zapikey içermiyor")
    query = urllib.parse.urlencode({"zapikey": key, "bot_unique_name": bot})
    return f"{root}/channelsbyname/{urllib.parse.quote(channel)}/files?{query}"


def validate_png(data: bytes) -> None:
    if not data.startswith(PNG_MAGIC):
        raise ValueError("Görsel PNG değil")
    if len(data) > MAX_BYTES:
        raise ValueError("Görsel 3 MB'tan büyük")


def post_channel_image(data: bytes, filename: str, comment: str, channel: str | None = None) -> list[str]:
    """PNG'yi kanala gönderir; Cliq'in mesaj numaralarını döner."""
    validate_png(data)
    boundary = uuid.uuid4().hex
    comments = json.dumps([comment], ensure_ascii=False)
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="comments"\r\n\r\n{comments}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    req = urllib.request.Request(
        _files_url(channel or share_channel()),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    try:
        ids = [str(i) for i in (json.loads(raw or b"{}").get("message_ids") or [])]
    except ValueError:
        ids = []
    if not ids:
        raise RuntimeError("Cliq görseli kanala ulaştırmadı")
    return ids
