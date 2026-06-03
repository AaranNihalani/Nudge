"""Claude API integration — NLP parsing and message humanisation."""
from __future__ import annotations

import json
import time
from typing import Any

try:
    from anthropic import Anthropic
except Exception:
    Anthropic = None

from .config import Config

# ---------------------------------------------------------------------------
# AIDIS research context injected into Claude's system prompt
# This ensures Claude gives contextually informed responses when discussing
# loan situations with users.
# Source: Nihalani, A. (2025). Understanding Financial Inclusion: Patterns and
# Determinants of Formal Borrowing in India. SSRN 6006354.
# ---------------------------------------------------------------------------
_AIDIS_CONTEXT = """
Key facts from AIDIS 2019 (~480,000 nationally representative Indian loan records):
- Informal loans average 18.65% interest vs 10.52% for formal loans — informal is nearly double.
- 50.2% of all loans in India are from informal sources; moneylenders account for 20.1%.
- Average formal loan (₹2,25,774) is 3x larger than the average informal loan (₹73,756).
- Medical emergencies drive 39.8% of informal borrowing — speed and accessibility push people away from formal channels.
- Moneylenders still provide 22% of rural loans despite decades of policy reform.
- SC households are 4.7–6.8 percentage points less likely to access formal credit than OBC households.
- Muslim households are 8.5–10.7 percentage points less likely to access formal credit than Hindu households.
- Urban residents are 2.3–4.1 percentage points more likely to access formal credit than rural residents.
- Income (MPCE) is positively associated with formal credit: +1.13 pp per ₹1,000 monthly per-capita expenditure.

When helping users, reference these patterns naturally where relevant. Always cite AIDIS 2019 when quoting statistics.
""".strip()


def _extract_text(message: Any) -> str:
    return "\n".join(
        getattr(block, "text", "")
        for block in (getattr(message, "content", []) or [])
        if getattr(block, "type", None) == "text"
    ).strip()


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no_json_object:snippet={raw[:200]}")
    payload = json.loads(raw[start: end + 1])
    if not isinstance(payload, dict):
        raise ValueError("json_not_object")
    return payload


def generate_reply(cfg: Config, user_text: str, *, system: str | None = None) -> str | None:
    if not cfg.claude_api_key or Anthropic is None:
        return None

    attempts = max(1, int(cfg.claude_attempts))
    timeout = float(cfg.claude_timeout_seconds)
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            client = Anthropic(api_key=cfg.claude_api_key, timeout=timeout)
            kwargs: dict[str, Any] = {
                "model": cfg.claude_model,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": user_text}],
            }
            if system:
                kwargs["system"] = system
            message = client.messages.create(**kwargs)
            reply = _extract_text(message)
            return reply or None
        except Exception as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(min(4.0, 0.5 * (2 ** attempt)))

    if cfg.debug_claude and last_error:
        raise last_error
    return None


def call_json_with_retries(
    cfg: Config,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 512,
    timeout_seconds: float = 20.0,
    attempts: int = 3,
) -> tuple[dict[str, Any], str] | None:
    if not cfg.claude_api_key or Anthropic is None:
        return None

    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            client = Anthropic(api_key=cfg.claude_api_key, timeout=float(timeout_seconds))
            message = client.messages.create(
                model=cfg.claude_model,
                max_tokens=int(max_tokens),
                temperature=0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text = _extract_text(message)
            payload = _extract_json(raw_text)
            return payload, str(cfg.claude_model)
        except Exception as e:
            last_error = e
            if attempt < max(1, int(attempts)) - 1:
                time.sleep(min(4.0, 0.5 * (2 ** attempt)))

    if cfg.debug_claude and last_error:
        raise last_error
    return None
