from __future__ import annotations

import json
import time
from typing import Any

from anthropic import Anthropic

from .config import Config


def _extract_message_text(message: Any) -> str:
    text_chunks: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_chunks.append(getattr(block, "text", ""))
    return "\n".join([t for t in text_chunks if t]).strip()


def generate_reply(config: Config, user_text: str) -> str | None:
    if not config.claude_api_key:
        return None

    try:
        client = Anthropic(api_key=config.claude_api_key, timeout=30)
        message = client.messages.create(
            model=config.claude_model,
            max_tokens=256,
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception:
        return None

    reply = _extract_message_text(message)
    return reply or None


def call_json_with_retries(
    config: Config,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 512,
    timeout_seconds: float = 20,
    attempts: int = 3,
) -> tuple[dict[str, Any], str] | None:
    if not config.claude_api_key:
        return None

    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            client = Anthropic(api_key=config.claude_api_key, timeout=float(timeout_seconds))
            message = client.messages.create(
                model=config.claude_model,
                max_tokens=int(max_tokens),
                temperature=0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text = _extract_message_text(message)
            stripped = raw_text.strip()
            if not stripped.startswith("{") or not stripped.endswith("}"):
                raise ValueError("claude_returned_non_json")
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError("claude_returned_non_object_json")
            return payload, str(config.claude_model)
        except Exception as e:
            last_error = e
            if attempt >= max(1, int(attempts)) - 1:
                break
            backoff = min(4.0, 0.5 * (2**attempt))
            time.sleep(backoff)

    _ = last_error
    return None
