from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .db import connect


BORROW_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "intent",
        "confidence",
        "amount_inr",
        "tenure_days",
        "interest_rate_apr",
        "lender_name",
        "lender_type",
        "negotiation_stage",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "intent": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "amount_inr": {"type": ["number", "null"], "minimum": 0},
        "tenure_days": {"type": ["integer", "null"], "minimum": 1},
        "interest_rate_apr": {"type": ["number", "null"], "minimum": 0},
        "lender_name": {"type": ["string", "null"], "maxLength": 128},
        "lender_type": {
            "type": "string",
            "enum": [
                "informal",
                "moneylender",
                "mfi",
                "nbfc",
                "bank",
                "cooperative",
                "friend_family",
                "shopkeeper",
                "unknown",
            ],
        },
        "negotiation_stage": {
            "type": "string",
            "enum": ["none", "considering", "asking", "offered", "agreed", "borrowed"],
        },
    },
}


class BorrowIntentValidationError(ValueError):
    pass


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def strict_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        raise BorrowIntentValidationError("response_is_not_json_object")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BorrowIntentValidationError("invalid_json") from e
    if not isinstance(parsed, dict):
        raise BorrowIntentValidationError("response_is_not_object")
    return parsed


def validate_borrow_intent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BorrowIntentValidationError("payload_not_object")

    allowed_keys = set(BORROW_INTENT_SCHEMA["properties"].keys())
    required_keys = set(BORROW_INTENT_SCHEMA["required"])
    payload_keys = set(payload.keys())

    missing = required_keys - payload_keys
    if missing:
        raise BorrowIntentValidationError(f"missing_keys:{','.join(sorted(missing))}")

    extra = payload_keys - allowed_keys
    if extra:
        raise BorrowIntentValidationError(f"extra_keys:{','.join(sorted(extra))}")

    schema_version = payload.get("schema_version")
    if not _is_int(schema_version) or schema_version != 1:
        raise BorrowIntentValidationError("schema_version_invalid")

    intent = payload.get("intent")
    if not _is_bool(intent):
        raise BorrowIntentValidationError("intent_invalid")

    confidence = payload.get("confidence")
    if not _is_number(confidence):
        raise BorrowIntentValidationError("confidence_invalid")
    confidence_f = float(confidence)
    if confidence_f < 0 or confidence_f > 1:
        raise BorrowIntentValidationError("confidence_out_of_range")

    amount_inr = payload.get("amount_inr")
    if amount_inr is not None and not _is_number(amount_inr):
        raise BorrowIntentValidationError("amount_inr_invalid")
    amount_inr_f = float(amount_inr) if amount_inr is not None else None
    if amount_inr_f is not None and amount_inr_f < 0:
        raise BorrowIntentValidationError("amount_inr_out_of_range")

    tenure_days = payload.get("tenure_days")
    if tenure_days is not None and not _is_int(tenure_days):
        raise BorrowIntentValidationError("tenure_days_invalid")
    tenure_days_i = int(tenure_days) if tenure_days is not None else None
    if tenure_days_i is not None and tenure_days_i < 1:
        raise BorrowIntentValidationError("tenure_days_out_of_range")

    interest_rate_apr = payload.get("interest_rate_apr")
    if interest_rate_apr is not None and not _is_number(interest_rate_apr):
        raise BorrowIntentValidationError("interest_rate_apr_invalid")
    interest_rate_apr_f = float(interest_rate_apr) if interest_rate_apr is not None else None
    if interest_rate_apr_f is not None and interest_rate_apr_f < 0:
        raise BorrowIntentValidationError("interest_rate_apr_out_of_range")

    lender_name = payload.get("lender_name")
    if lender_name is not None and not isinstance(lender_name, str):
        raise BorrowIntentValidationError("lender_name_invalid")
    lender_name_s = lender_name.strip() if isinstance(lender_name, str) else None
    if lender_name_s == "":
        lender_name_s = None
    if lender_name_s is not None and len(lender_name_s) > 128:
        raise BorrowIntentValidationError("lender_name_too_long")

    lender_type = payload.get("lender_type")
    allowed_lender_types = set(BORROW_INTENT_SCHEMA["properties"]["lender_type"]["enum"])
    if not isinstance(lender_type, str) or lender_type not in allowed_lender_types:
        raise BorrowIntentValidationError("lender_type_invalid")

    negotiation_stage = payload.get("negotiation_stage")
    allowed_stages = set(BORROW_INTENT_SCHEMA["properties"]["negotiation_stage"]["enum"])
    if not isinstance(negotiation_stage, str) or negotiation_stage not in allowed_stages:
        raise BorrowIntentValidationError("negotiation_stage_invalid")

    return {
        "schema_version": 1,
        "intent": bool(intent),
        "confidence": confidence_f,
        "amount_inr": amount_inr_f,
        "tenure_days": tenure_days_i,
        "interest_rate_apr": interest_rate_apr_f,
        "lender_name": lender_name_s,
        "lender_type": lender_type,
        "negotiation_stage": negotiation_stage,
    }


@dataclass(frozen=True)
class BorrowIntentParseResult:
    payload: dict[str, Any]
    model: str


def _heuristic_borrow_payload(text: str) -> dict[str, Any]:
    from .bot.parsers import (
        infer_lender_type,
        looks_like_loan_intent_message,
        looks_like_loan_terms_fragment,
        looks_like_new_loan_message,
        parse_amount_inr,
        parse_interest_rate_apr,
        parse_tenure_days,
    )

    raw = (text or "").strip()
    low = raw.lower()
    intent = bool(
        looks_like_new_loan_message(raw)
        or looks_like_loan_intent_message(raw)
        or looks_like_loan_terms_fragment(raw)
    )
    lender_type = infer_lender_type(raw) or "unknown"
    amount_inr = parse_amount_inr(raw)
    tenure_days = parse_tenure_days(raw)
    interest_rate_apr = parse_interest_rate_apr(raw)

    stage = "none"
    if any(p in low for p in ("already borrowed", "i borrowed", "we borrowed", "have borrowed", "took a loan", "took loan")):
        stage = "borrowed"
    elif any(p in low for p in ("agreed", "accepted", "finalised", "finalized")):
        stage = "agreed"
    elif any(p in low for p in ("offered", "offer", "quote", "quoted")):
        stage = "offered"
    elif intent:
        stage = "asking"

    confidence = 0.2
    if intent:
        confidence = 0.55
        if amount_inr is not None and tenure_days is not None:
            confidence = 0.78
        elif amount_inr is not None or tenure_days is not None or interest_rate_apr is not None:
            confidence = 0.68
        if lender_type != "unknown":
            confidence = max(confidence, 0.7)

    return validate_borrow_intent_payload({
        "schema_version": 1,
        "intent": intent,
        "confidence": confidence,
        "amount_inr": float(amount_inr) if amount_inr is not None else None,
        "tenure_days": int(tenure_days) if tenure_days is not None else None,
        "interest_rate_apr": float(interest_rate_apr) if interest_rate_apr is not None else None,
        "lender_name": None,
        "lender_type": lender_type,
        "negotiation_stage": stage,
    })


def persist_borrow_intent_event(
    db_path: str,
    *,
    user_id: int,
    raw_message_id: int | None,
    payload: dict[str, Any],
    model: str | None,
) -> int:
    validated = validate_borrow_intent_payload(payload)
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            INSERT INTO parsed_events(
                user_id,
                raw_message_id,
                event_type,
                event_json,
                confidence,
                model,
                intent,
                amount_inr,
                tenure_days,
                interest_rate_apr,
                lender_name,
                lender_type,
                negotiation_stage
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(raw_message_id) if raw_message_id is not None else None,
                "borrow_intent",
                json.dumps(validated, ensure_ascii=False),
                float(validated["confidence"]),
                model,
                1 if validated["intent"] else 0,
                validated["amount_inr"],
                validated["tenure_days"],
                validated["interest_rate_apr"],
                validated["lender_name"],
                validated["lender_type"],
                validated["negotiation_stage"],
            ),
        )
        event_id = int(cursor.lastrowid)
        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parse_borrow_intent_with_llm(
    cfg: Config,
    *,
    text: str,
    call_json: Callable[[Config, str, str], tuple[dict[str, Any], str] | None],
) -> BorrowIntentParseResult | None:
    schema = json.dumps(BORROW_INTENT_SCHEMA, ensure_ascii=False)
    system_prompt = (
        "You extract borrowing intent and terms from WhatsApp/SMS messages.\n"
        "Return ONLY valid JSON (no markdown, no code fences) matching this JSON Schema exactly:\n"
        f"{schema}\n"
        "Rules:\n"
        "- Set intent=true if the user is planning to borrow, negotiating a loan, or has borrowed.\n"
        "- Set intent=false if unrelated.\n"
        "- confidence must be 0..1.\n"
        "- amount_inr is the principal amount in INR if stated, else null.\n"
        "- tenure_days is total days if stated (convert weeks/months to days), else null.\n"
        "- interest_rate_apr is APR percent if inferable; if the message gives monthly percent, multiply by 12.\n"
        "- If the message says the rate is a cap (e.g. 'less than 50% APR', 'under 3% monthly', 'max 24% APR'), set interest_rate_apr=null.\n"
        "- lender_type must be one of the allowed enum values; use unknown if unclear.\n"
        "- negotiation_stage: none/considering/asking/offered/agreed/borrowed.\n"
        "- Do not add extra keys.\n"
    )
    user_prompt = f"Message:\n{text.strip()}\n\nJSON:"
    result = call_json(cfg, system_prompt, user_prompt)
    if result is None:
        return BorrowIntentParseResult(payload=_heuristic_borrow_payload(text), model="heuristic-fallback")
    payload_raw, model = result
    payload = validate_borrow_intent_payload(payload_raw)
    return BorrowIntentParseResult(payload=payload, model=model)
