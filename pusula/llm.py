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
) -> dict[str, Any] | None:
    """Şemaya uyan JSON yanıt (uzun metin analizi için). Hata/eksik yanıtta None."""
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
