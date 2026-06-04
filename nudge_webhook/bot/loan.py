"""Loan payload logic — parsing, correction, lender option selection."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..nlp import validate_borrow_intent_payload
from ..nudge_content import loan_cost_breakdown
from .parsers import (
    infer_lender_type,
    parse_amount_inr,
    parse_interest_rate_apr,
    parse_tenure_days,
)


@dataclass(frozen=True)
class InboundMessage:
    from_addr: str
    to_addr: str | None
    body: str
    message_sid: str | None
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Borrow payload helpers
# ---------------------------------------------------------------------------

def empty_borrow_payload() -> dict[str, Any]:
    return {
        "schema_version": 1, "intent": True, "confidence": 0.85,
        "amount_inr": None, "tenure_days": None, "interest_rate_apr": None,
        "lender_name": None, "lender_type": "unknown", "negotiation_stage": "asking",
    }


def load_latest_borrow_payload(conn, *, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT event_json FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1
        ORDER BY parsed_at DESC, id DESC LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["event_json"]))
        return dict(payload) if isinstance(payload, dict) else None
    except Exception:
        return None


def merge_borrow_details_from_text(*, text: str, base_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    updated = dict(base_payload) if isinstance(base_payload, dict) else empty_borrow_payload()
    changed = False
    for field, fn in [("amount_inr", parse_amount_inr), ("tenure_days", parse_tenure_days), ("interest_rate_apr", parse_interest_rate_apr)]:
        val = fn(text)
        if val is not None:
            updated[field] = float(val) if field != "tenure_days" else int(val)
            changed = True
    if not changed:
        return None
    try:
        return validate_borrow_intent_payload(updated)
    except Exception:
        return None


def apply_text_heuristics(payload: dict[str, Any], *, text: str) -> dict[str, Any]:
    next_payload = dict(payload)
    raw = (text or "").strip().lower()
    if next_payload.get("interest_rate_apr") is not None:
        cap_match = re.search(
            r"(less than|under|below|<|upto|up to|maximum|max)\s*(?:about\s*)?(\d+(?:\.\d+)?)\s*%?\s*(apr|annual|year|yearly|month|monthly|week|weekly|day|daily)\b",
            raw,
        )
        if cap_match:
            next_payload["interest_rate_apr"] = None
    if next_payload.get("lender_type") in {None, "", "unknown"}:
        inferred = infer_lender_type(text)
        if inferred:
            next_payload["lender_type"] = inferred
    return validate_borrow_intent_payload(next_payload)


def apply_correction(payload: dict[str, Any], *, field: str, value_text: str) -> dict[str, Any] | None:
    next_payload = dict(payload)
    try:
        if field == "intent":
            next_payload["intent"] = value_text.strip().lower() in {"1", "true", "yes", "y"}
            next_payload["confidence"] = max(float(next_payload.get("confidence") or 0.0), 0.8)
        elif field == "amount_inr":
            val = parse_amount_inr(value_text)
            if val is None:
                return None
            next_payload["amount_inr"] = float(val)
        elif field == "tenure_days":
            val = parse_tenure_days(value_text)
            if val is None:
                return None
            next_payload["tenure_days"] = int(val)
        elif field == "interest_rate_apr":
            val = parse_interest_rate_apr(value_text)
            if val is None:
                m = re.search(r"(\d+(?:\.\d+)?)", value_text)
                val = float(m.group(1)) if m else None
            if val is None:
                return None
            next_payload["interest_rate_apr"] = float(val)
        elif field == "negotiation_stage":
            stage = value_text.strip().lower()
            if stage not in {"none", "considering", "asking", "offered", "agreed", "borrowed"}:
                return None
            next_payload["negotiation_stage"] = stage
        elif field == "lender_type":
            lt = value_text.strip().lower()
            if lt not in {"informal", "moneylender", "mfi", "nbfc", "bank", "cooperative", "friend_family", "shopkeeper", "unknown"}:
                return None
            next_payload["lender_type"] = lt
        elif field == "lender_name":
            next_payload["lender_name"] = value_text.strip() or None
        else:
            return None
        return validate_borrow_intent_payload(next_payload)
    except Exception:
        return None


def missing_borrow_fields(payload: dict[str, Any]) -> list[str]:
    return [k for k in ("amount_inr", "tenure_days") if payload.get(k) is None]


def clarifying_question(field: str) -> str:
    if field == "amount_inr":
        return "How much do you want to borrow in INR? Example: 5000"
    if field == "tenure_days":
        return "How long is the loan for? Example: 30 days or 2 months"
    return "What interest rate did they quote? Example: 5% monthly or 60% APR"


def insert_user_action(conn, *, user_id: int, raw_message_id: int | None, action_type: str, lender: str | None, details: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO user_actions(user_id, source_raw_message_id, action_type, lender, details_json) VALUES (?, ?, ?, ?, ?)",
        (int(user_id), int(raw_message_id) if raw_message_id is not None else None,
         str(action_type), lender, json.dumps(details, ensure_ascii=False)),
    )


# ---------------------------------------------------------------------------
# Lender option selection
# ---------------------------------------------------------------------------

_STOPWORDS = {"tell", "me", "about", "explore", "pick", "choose", "select", "open",
              "option", "details", "detail", "please", "ok", "okay", "hi", "hello",
              "show", "for", "the", "a", "an", "on"}


def lender_option_prompt(count: int) -> str:
    if count <= 0:
        return "Send your loan amount and time first, and I'll show local options."
    if count == 1:
        return "Just send 1 to open it."
    if count == 2:
        return "Just send 1 or 2 to open an option."
    return "Just send 1, 2, or 3 to open an option."


def _option_number(text: str) -> int | None:
    lower = (text or "").strip().lower()
    m = (
        re.fullmatch(
            r"(?:option|pick|choose|select|show|tell me about|explore)\s*#?\s*([1-9])\s*[\)\]\.\,\!\'\"]?\s*",
            lower,
        )
        or re.fullmatch(r"#?\s*([1-9])\s*[\)\]\.\,\!\'\"]?\s*", lower)
    )
    return int(m.group(1)) if m else None


def _normalize_words(text: str) -> list[str]:
    raw = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    return [w for w in re.sub(r"\s+", " ", raw).strip().split() if w]


def _best_lender_match(text: str, *, options: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    words = [w for w in _normalize_words(text) if w not in _STOPWORDS and len(w) >= 3]
    if not words:
        return None
    best: tuple[int, int, dict[str, Any]] | None = None
    for idx, option in enumerate(options, start=1):
        lender_words = set(_normalize_words(str(option.get("lender") or "")))
        score = sum(3 if qw in lender_words else (1 if any(qw in lw and len(qw) >= 4 for lw in lender_words) else 0) for qw in words)
        if score >= 2 and (best is None or score > best[0]):
            best = (score, idx, option)
    return (best[1], best[2]) if best else None


def parse_lender_selection(text: str, *, options: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    raw = (text or "").strip()
    if not raw or not options:
        return None
    lower = raw.lower()
    n = _option_number(lower)
    if n is not None:
        idx = n - 1
        if 0 <= idx < len(options):
            return idx + 1, options[idx]
    for idx, option in enumerate(options, start=1):
        lender = str(option.get("lender") or "").strip().lower()
        if lender and lower in {lender, f"tell me about {lender}", f"explore {lender}", f"pick {lender}"}:
            return idx, option
    return _best_lender_match(raw, options=options)


def looks_like_lender_selection(text: str) -> bool:
    return _option_number((text or "").strip().lower()) is not None


def enrich_lender_options(options: list[dict[str, Any]], *, amount_inr: float | None, tenure_days: int | None, current_rate: float | None) -> list[dict[str, Any]]:
    count = len(options)
    enriched = []
    for option in options:
        item = dict(option)
        item["option_count"] = count
        if amount_inr is not None:
            item["amount_inr"] = float(amount_inr)
        if tenure_days is not None:
            item["tenure_days"] = int(tenure_days)
        if current_rate is not None:
            item["current_rate"] = float(current_rate)
        enriched.append(item)
    return enriched


# ---------------------------------------------------------------------------
# Selected lender conversation
# ---------------------------------------------------------------------------

def selected_lender_feedback_kind(text: str) -> str:
    raw = (text or "").strip().lower()
    if raw in {"yes", "y", "ok", "okay", "good", "looks good", "interested", "proceed"} or \
       any(p in raw for p in ("manageable", "looks good", "interested", "proceed")):
        return "positive"
    if raw in {"no", "n", "too high", "expensive", "costly"} or \
       any(p in raw for p in ("too high", "expensive", "not manageable", "cannot afford", "can't afford")):
        return "negative"
    if raw in {"maybe", "unsure", "not sure", "confused", "explain", "how", "why", "what"} or \
       any(p in raw for p in ("not sure", "explain")):
        return "unsure"
    return "open"


def selected_lender_cost_hint(option: dict[str, Any]) -> str:
    amount_inr = option.get("amount_inr")
    tenure_days = option.get("tenure_days")
    rate = option.get("rate_apr")
    if amount_inr is None or tenure_days is None or rate is None:
        return ""
    try:
        bd = loan_cost_breakdown(float(amount_inr), int(tenure_days), float(rate))
        return (
            f" At {float(rate):g}% APR on INR {int(round(float(amount_inr))):,}, that is about INR "
            f"{int(round(float(bd['annual_interest']))):,} interest over a year, "
            f"INR {int(round(float(bd['monthly_interest']))):,}/month. "
            f"For {int(tenure_days)} days: total repayment about INR {int(round(float(bd['total_repayment']))):,}."
        )
    except Exception:
        return ""


def selected_lender_fallback_reply(*, user_text: str, option: dict[str, Any], rank: int | None) -> str:
    lender = str(option.get("lender") or "this lender")
    kind = selected_lender_feedback_kind(user_text)
    option_count = int(option.get("option_count") or 0)
    compare = " You can also send 1, 2, or 3 to compare another option." if option_count > 1 else ""
    cost_hint = selected_lender_cost_hint(option)

    if kind == "positive":
        return (
            f"That sounds promising.{cost_hint} Before deciding on {lender}, confirm the exact monthly payment, "
            f"fees, penalties, and collection terms with them. If you contact them, reply CONTACTED {lender}. "
            f"If you decide to switch, reply SWITCHED {lender}.{compare}"
        )
    if kind == "negative":
        return (
            f"If that feels too high, don't rush.{cost_hint} "
            + ("Send the amount and loan time to get a rupee estimate. " if not cost_hint else "")
            + f"{lender_option_prompt(option_count)}"
        )
    return (
        f"For {lender}, focus on whether the monthly payment fits your cash flow.{cost_hint} "
        + ("If you send the amount and loan time, I can estimate the rupee cost. " if not cost_hint else "")
        + f"Ask the lender for the exact EMI, processing fees, and total repayment in writing. "
        f"Does this feel manageable, too high, or uncertain?{compare}"
    )
