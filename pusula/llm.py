"""Claude ile kısa Türkçe metin. Sapmayı kod bulur; model yalnız cümleye döker.

ANTHROPIC_API_KEY yoksa veya çağrı başarısızsa None döner: dürtme ve panel
yapay zekâ olmadan, deterministik metinle çalışmaya devam eder.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
TIMEOUT_SEC = 40.0

# $ / milyon token: giriş, çıkış, önbellekten okuma. Önbelleğe yazma girişin 1,25 katı.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
}
CACHE_WRITE = 1.25
# Toplu (Batch) gönderimde tüm tokenlar yarı fiyat.
BATCH_DISCOUNT = 0.5


def usage_dict(model: str, usage: Any, *, batch: bool = False) -> dict[str, Any]:
    """API kullanımından kayıt: token sayıları ve $ maliyet (fiyatı bilinmeyen modelde maliyet yok)."""
    tokens = {
        "input": int(getattr(usage, "input_tokens", 0) or 0),
        "output": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_write": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }
    return {"model": model, "batch": batch, **tokens, "usd": cost_usd(model, tokens, batch=batch)}


def cost_usd(model: str, tokens: dict[str, int], *, batch: bool = False) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    inp, out, read = price
    usd = (
        tokens.get("input", 0) * inp
        + tokens.get("cache_write", 0) * inp * CACHE_WRITE
        + tokens.get("cache_read", 0) * read
        + tokens.get("output", 0) * out
    ) / 1_000_000
    return round(usd * (BATCH_DISCOUNT if batch else 1.0), 6)


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model_name() -> str:
    return os.environ.get("PUSULA_AI_MODEL") or DEFAULT_MODEL


def complete(system: str, prompt: str, *, max_tokens: int = 2000) -> str | None:
    if not enabled():
        return None
    import anthropic

    client = anthropic.Anthropic(timeout=TIMEOUT_SEC, max_retries=1)
    try:
        response = client.beta.messages.create(
            model=model_name(),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as exc:
        logger.warning("AI bağlantı hatası: %s", exc)
        return None
    except anthropic.APIStatusError as exc:
        logger.warning("AI hata %s: %s", exc.status_code, exc.message)
        return None
    if response.stop_reason != "end_turn":
        logger.warning("AI metni tamamlanmadı: %s", response.stop_reason)
        return None
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or None


def complete_json(
    system: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    max_tokens: int = 16000,
    effort: str = "medium",
    timeout: float = 300.0,
    usage: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Şemaya uyan JSON yanıt (uzun metin analizi için). Hata/eksik yanıtta None.
    `usage` verilirse çağrının token ve maliyet kaydı eklenir (başarısız yanıt da ücretlidir)."""
    if not enabled():
        return None
    import anthropic

    client = anthropic.Anthropic(timeout=timeout, max_retries=3)
    try:
        response = client.beta.messages.create(
            model=model_name(),
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as exc:
        logger.warning("AI bağlantı hatası: %s", exc)
        return None
    except anthropic.APIStatusError as exc:
        logger.warning("AI hata %s: %s", exc.status_code, exc.message)
        return None
    if usage is not None:
        usage.append(usage_dict(response.model, response.usage))
    if response.stop_reason != "end_turn":
        logger.warning("AI yanıtı tamamlanmadı: %s", response.stop_reason)
        return None
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("AI yanıtı JSON değil")
        return None
    return data if isinstance(data, dict) else None


# --- toplu (Batch) analiz --------------------------------------------------------------


def _json_request(system: str, prompt: str, schema: dict[str, Any], *, max_tokens: int, effort: str) -> dict[str, Any]:
    # Batch API sunucu tarafı yedek modeli (fallbacks) kabul etmez; ret hatalı sayılır.
    return {
        "model": model_name(),
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }


def batch_submit(
    requests: list[tuple[str, str, str, dict[str, Any]]], *, max_tokens: int = 16000, effort: str = "medium"
) -> str | None:
    """(custom_id, system, prompt, schema) listesini tek toplu iş olarak gönderir; iş kimliği."""
    if not enabled() or not requests:
        return None
    import anthropic

    client = anthropic.Anthropic(timeout=120.0, max_retries=3)
    try:
        batch = client.messages.batches.create(
            requests=[
                {"custom_id": cid, "params": _json_request(system, prompt, schema, max_tokens=max_tokens, effort=effort)}
                for cid, system, prompt, schema in requests
            ]
        )
    except anthropic.APIStatusError as exc:
        logger.warning("Toplu iş gönderilemedi %s: %s", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError as exc:
        logger.warning("Toplu iş bağlantı hatası: %s", exc)
        return None
    return batch.id


def batch_results(batch_id: str) -> dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]] | None:
    """İş bitmediyse None. Bittiyse custom_id → (JSON, kullanım, hata)."""
    import anthropic

    client = anthropic.Anthropic(timeout=120.0, max_retries=3)
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        return None
    out: dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]] = {}
    for result in client.messages.batches.results(batch_id):
        kind = result.result.type
        if kind != "succeeded":
            out[result.custom_id] = (None, None, f"toplu iş: {kind}")
            continue
        msg = result.result.message
        used = usage_dict(msg.model, msg.usage, batch=True)
        if msg.stop_reason != "end_turn":
            out[result.custom_id] = (None, used, f"yanıt tamamlanmadı: {msg.stop_reason}")
            continue
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            out[result.custom_id] = (None, used, "yanıt JSON değil")
            continue
        out[result.custom_id] = (data if isinstance(data, dict) else None, used, None)
    return out
