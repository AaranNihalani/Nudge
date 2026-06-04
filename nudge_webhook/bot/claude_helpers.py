"""Claude-powered message humanisation helpers."""
from __future__ import annotations

import json
from typing import Any

from ..claude import generate_reply
from ..config import Config
from ..nudge_content import lender_detail_fallback
from .loan import selected_lender_fallback_reply

_NO_GREETING = "Do not start with a greeting word (Hey, Hi, Hello, Sure, Great, Of course, Absolutely, etc.)."
_USE_MD = "Use markdown formatting: **bold** for lender names and key rupee figures, - for bullet lists, --- for section breaks. Do not use emojis."


def humanize(cfg: Config, *, fallback: str, purpose: str) -> str | None:
    prompt = (
        "Rewrite the message below as a natural chatbot reply for an Indian consumer. "
        "Preserve every rupee amount, percentage, lender name, district name, numbered list item, and command exactly. "
        "Do not add facts, approvals, phone numbers, legal advice, or new commands. "
        f"{_NO_GREETING} {_USE_MD} "
        f"Keep it concise and easy to act on.\n\nPurpose: {purpose}\nMessage:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt)
    return reply.strip() or None if reply else None


def recommendation_message(
    cfg: Config,
    *,
    fallback: str,
    district: str,
    options: list[dict[str, Any]],
    amount_inr: float | None,
    tenure_days: int | None,
    current_rate: float | None,
) -> str | None:
    if not options:
        return None
    prompt = (
        "Rewrite the message below as a natural chatbot response for an Indian consumer. "
        "Preserve every numbered lender option, lender name, APR, monthly rate, rupee amount, repayment amount, interest amount, time period, and command exactly. "
        "Do not add approval claims, phone numbers, legal advice, or extra lenders. "
        f"{_NO_GREETING} {_USE_MD} "
        "Keep it concise and easy to act on.\n\n"
        f"District: {district}\nLoan amount INR: {amount_inr}\nTenure days: {tenure_days}\nQuoted APR: {current_rate}\nFacts:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt)
    return reply.strip() or None if reply else None


def lender_detail(cfg: Config, *, option: dict[str, Any], rank: int, district: str | None) -> str | None:
    lender = str(option.get("lender") or "the selected lender")
    fallback = lender_detail_fallback(option=option, rank=rank, district=district)
    prompt = (
        "Rewrite this lender explanation as a clear chatbot message for an Indian consumer. "
        "Preserve the lender name, APR, per-month rate, every rupee amount, total repayment, monthly payment, fees warning, and any contact details already present (phone/email/website). "
        "Do not claim approval. Do not invent phone numbers, email addresses, branch details, or legal/financial advice. "
        f"{_NO_GREETING} {_USE_MD} "
        "Keep it concise.\n\n"
        f"Selected lender: {lender}\nFacts:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt)
    return reply.strip() or None if reply else None


def selected_lender_conversation(
    cfg: Config,
    *,
    user_text: str,
    option: dict[str, Any],
    rank: int | None,
    fallback: str,
) -> str | None:
    prompt = (
        "You are Nudge, a chatbot helping an Indian consumer decide on a regulated lender. "
        "Respond directly to the user's latest message. "
        "If the user has already confirmed they're happy with the option or the numbers fit their budget, "
        "do NOT repeat the loan figures again — just give the single most useful next step (contact the lender, ask for EMI in writing, etc.). "
        "If the user has a new question, answer it briefly using the lender facts. "
        "Do not claim approval. Do not invent fees, phone numbers, or branch details. "
        f"{_NO_GREETING} {_USE_MD} "
        "Keep under 80 words.\n\n"
        f"User message: {user_text}\n"
        f"Option: {json.dumps(option, ensure_ascii=False)}"
    )
    reply = generate_reply(cfg, prompt)
    return reply.strip() or None if reply else None


def profile_assessment_message(cfg: Config, *, assessment: str) -> str | None:
    prompt = (
        "Rewrite the credit access profile message below as a clear, direct message for an Indian consumer. "
        "Preserve every percentage figure, the research citation, the paper URL, and all factual statements exactly. "
        "Do not add percentages, statistics, or claims beyond what is written. "
        f"{_NO_GREETING} {_USE_MD} "
        "Keep it under 150 words.\n\n"
        f"Profile message:\n{assessment}"
    )
    reply = generate_reply(cfg, prompt)
    return reply.strip() or None if reply else None
