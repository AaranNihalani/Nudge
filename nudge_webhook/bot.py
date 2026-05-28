from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .claude import call_json_with_retries, generate_reply
from .config import Config
from .db import connect
from .nudge_content import lender_detail_fallback, loan_cost_breakdown, recommended_lender_rows, suggest_lender_message
from .nlp import parse_borrow_intent_with_llm, persist_borrow_intent_event, validate_borrow_intent_payload
from .policy_serving import decide_policy
from .state import compute_user_state


@dataclass(frozen=True)
class InboundMessage:
    from_addr: str
    to_addr: str | None
    body: str
    twilio_message_sid: str | None
    payload: dict[str, Any]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_sqlite_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _parse_sqlite_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _normalize_sender(from_addr: str) -> str:
    raw = (from_addr or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1]
    return raw.strip()


def _is_keyword(text: str, *, keyword: str) -> bool:
    return text.strip().lower() == keyword.lower()


def _starts_with_keyword(text: str, *, keyword: str) -> bool:
    return text.strip().lower().startswith(keyword.lower())


def _extract_district_command(text: str) -> str | None:
    raw = text.strip()
    lower = raw.lower()
    for prefix in ("district", "set district", "change district"):
        if lower.startswith(prefix):
            after = lower[len(prefix) :]
            if after and after[:1].isalpha():
                continue
            remaining = raw[len(prefix) :].strip()
            if remaining.startswith(":") or remaining.startswith(","):
                remaining = remaining[1:].strip()
            return remaining or None
    return None


def _canonical_district(conn, candidate: str) -> str | None:
    raw = candidate.strip()
    if raw == "":
        return None
    row = conn.execute(
        "SELECT name FROM mfi_districts WHERE lower(name) = lower(?) LIMIT 1",
        (raw,),
    ).fetchone()
    if row is None:
        return None
    return str(row["name"])


def _districts_sample(conn, *, limit: int = 25) -> list[str]:
    return [
        str(r["name"])
        for r in conn.execute(
            "SELECT name FROM mfi_districts ORDER BY name COLLATE NOCASE ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
    ]


def _districts_query(conn, *, prefix: str | None, limit: int = 30, offset: int = 0) -> tuple[list[str], int]:
    p = (prefix or "").strip()
    if p == "":
        rows = conn.execute(
            "SELECT name FROM mfi_districts ORDER BY name COLLATE NOCASE ASC LIMIT ? OFFSET ?",
            (int(limit), int(max(0, offset))),
        ).fetchall()
        total = int(conn.execute("SELECT COUNT(*) AS c FROM mfi_districts").fetchone()["c"])
        return ([str(r["name"]) for r in rows], total)

    like = p + "%"
    rows = conn.execute(
        "SELECT name FROM mfi_districts WHERE lower(name) LIKE lower(?) ORDER BY name COLLATE NOCASE ASC LIMIT ? OFFSET ?",
        (like, int(limit), int(max(0, offset))),
    ).fetchall()
    total = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM mfi_districts WHERE lower(name) LIKE lower(?)",
            (like,),
        ).fetchone()["c"]
    )
    return ([str(r["name"]) for r in rows], total)


def _extract_districts_query(text: str) -> str | None:
    raw = text.strip()
    lower = raw.lower()
    if not lower.startswith("districts"):
        return None
    after = lower[len("districts") :]
    if after and after[:1].isalpha():
        return None
    remaining = raw[len("districts") :].strip()
    if remaining.startswith(":") or remaining.startswith(","):
        remaining = remaining[1:].strip()
    return remaining


def _is_more_command(text: str) -> bool:
    return _is_keyword(text, keyword="more") or _is_keyword(text, keyword="districts/more")


def _ensure_user_session(conn, *, user_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO user_sessions(user_id) VALUES (?)", (int(user_id),))


def _load_user_session(conn, *, user_id: int) -> dict[str, Any]:
    _ensure_user_session(conn, user_id=user_id)
    row = conn.execute(
        """
        SELECT
            districts_prefix,
            districts_offset,
            districts_page_size,
            borrow_draft_json,
            borrow_source_raw_message_id,
            borrow_model,
            language,
            lender_options_json,
            lender_options_updated_at,
            selected_lender_option_json,
            selected_lender_rank,
            selected_lender_updated_at
        FROM user_sessions
        WHERE user_id = ?
        """,
        (int(user_id),),
    ).fetchone()
    return dict(row) if row is not None else {}


def _save_language(conn, *, user_id: int, language: str) -> None:
    _ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET language = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (str(language), int(user_id)),
    )


def _language_for_session(cfg: Config, session: dict[str, Any] | None) -> str:
    lang = None
    if session and session.get("language") is not None:
        lang = str(session.get("language") or "").strip().lower()
    if lang not in {"en", "hi", "hinglish"}:
        lang = str(getattr(cfg, "default_language", "en") or "en").strip().lower()
    if lang not in {"en", "hi", "hinglish"}:
        lang = "en"
    return lang


def _support_line(cfg: Config) -> str:
    contact = str(getattr(cfg, "support_contact", "") or "").strip()
    if contact == "":
        return ""
    return f"\n\nSupport: {contact}"


def _strip_prefix_text(raw: str, prefix: str) -> str:
    remaining = raw[len(prefix) :].strip()
    while remaining.startswith(":") or remaining.startswith(","):
        remaining = remaining[1:].strip()
    return remaining


def _parse_lang_command(text: str) -> str | None:
    raw = text.strip()
    lower = raw.lower()
    for prefix in ("lang", "language"):
        if lower == prefix:
            return ""
        if lower.startswith(prefix + " ") or lower.startswith(prefix + ":") or lower.startswith(prefix + ","):
            return _strip_prefix_text(raw, prefix).strip()
    return None


def _parse_contacted(text: str) -> str | None:
    raw = text.strip()
    lower = raw.lower()
    if lower == "contacted":
        return ""
    if lower.startswith("contacted"):
        return _strip_prefix_text(raw, "contacted").strip()
    return None


def _parse_switched(text: str) -> tuple[str | None, str | None] | None:
    raw = text.strip()
    lower = raw.lower()
    if lower == "switched":
        return ("", None)
    if not lower.startswith("switched"):
        return None
    remaining = _strip_prefix_text(raw, "switched").strip()
    if remaining == "":
        return ("", None)
    low = remaining.lower()
    if low.startswith("from "):
        rest = remaining[5:].strip()
        parts = rest.split(" to ", 1)
        if len(parts) == 2:
            return (parts[0].strip() or None, parts[1].strip() or None)
    return (None, remaining.strip() or None)


def _insert_user_action(
    conn,
    *,
    user_id: int,
    raw_message_id: int | None,
    action_type: str,
    lender: str | None,
    details: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO user_actions(user_id, source_raw_message_id, action_type, lender, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            int(raw_message_id) if raw_message_id is not None else None,
            str(action_type),
            lender,
            json.dumps(details, ensure_ascii=False),
        ),
    )


def _save_district_paging_state(conn, *, user_id: int, prefix: str | None, offset: int, page_size: int) -> None:
    _ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET districts_prefix = ?,
            districts_offset = ?,
            districts_page_size = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (prefix, int(offset), int(page_size), int(user_id)),
    )


def _clear_district_paging_state(conn, *, user_id: int) -> None:
    _ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET districts_prefix = NULL,
            districts_offset = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (int(user_id),),
    )


def _save_borrow_draft(
    conn,
    *,
    user_id: int,
    payload: dict[str, Any] | None,
    source_raw_message_id: int | None,
    model: str | None,
) -> None:
    _ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET borrow_draft_json = ?,
            borrow_source_raw_message_id = ?,
            borrow_model = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            int(source_raw_message_id) if source_raw_message_id is not None else None,
            model,
            int(user_id),
        ),
    )


def _save_lender_options(conn, *, user_id: int, options: list[dict[str, Any]] | None) -> None:
    _ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET lender_options_json = ?,
            lender_options_updated_at = CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (
            json.dumps(options, ensure_ascii=False) if options else None,
            json.dumps(options, ensure_ascii=False) if options else None,
            int(user_id),
        ),
    )


def _save_selected_lender_option(conn, *, user_id: int, option: dict[str, Any] | None, rank: int | None) -> None:
    _ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET selected_lender_option_json = ?,
            selected_lender_rank = ?,
            selected_lender_updated_at = CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (
            json.dumps(option, ensure_ascii=False) if option else None,
            int(rank) if option is not None and rank is not None else None,
            json.dumps(option, ensure_ascii=False) if option else None,
            int(user_id),
        ),
    )


def _load_lender_options(session: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = session.get("lender_options_json") if session else None
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(x) for x in parsed if isinstance(x, dict)]


def _load_selected_lender_option(session: dict[str, Any] | None) -> tuple[int | None, dict[str, Any] | None]:
    raw = session.get("selected_lender_option_json") if session else None
    if not raw:
        return None, None
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    rank = None
    if session and session.get("selected_lender_rank") is not None:
        try:
            rank = int(session.get("selected_lender_rank"))
        except Exception:
            rank = None
    return rank, dict(parsed)


def _lender_option_prompt(count: int) -> str:
    if count <= 0:
        return "Send your loan amount and time first, and I’ll show local options."
    if count == 1:
        return "Reply 1 to open it."
    if count == 2:
        return "Reply 1 or 2 to open an option."
    return "Reply 1, 2, or 3 to open an option."


def _option_selection_number(text: str) -> int | None:
    lower = (text or "").strip().lower()
    if lower == "":
        return None
    m = re.fullmatch(r"(?:option|pick|choose|select|show|tell me about|explore)\s*#?\s*([1-9])", lower)
    if m is None:
        m = re.fullmatch(r"#?\s*([1-9])", lower)
    return int(m.group(1)) if m is not None else None


def _normalize_words_for_match(text: str) -> list[str]:
    raw = (text or "").lower()
    raw = re.sub(r"[^a-z0-9\s]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw == "":
        return []
    return [w for w in raw.split(" ") if w]


_LENDER_QUERY_STOPWORDS = {
    "tell",
    "me",
    "about",
    "explore",
    "pick",
    "choose",
    "select",
    "open",
    "option",
    "details",
    "detail",
    "please",
    "ok",
    "okay",
    "hi",
    "hello",
    "show",
    "for",
    "the",
    "a",
    "an",
    "on",
}


def _best_lender_option_match(text: str, *, options: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    if not options:
        return None
    words = [w for w in _normalize_words_for_match(text) if w not in _LENDER_QUERY_STOPWORDS]
    if not words:
        return None
    query_words = [w for w in words if len(w) >= 3]
    if not query_words:
        return None

    best: tuple[int, int, dict[str, Any]] | None = None  # (score, rank, option)
    for idx, option in enumerate(options, start=1):
        lender = str(option.get("lender") or "").strip()
        lender_words = set(_normalize_words_for_match(lender))
        if not lender_words:
            continue
        score = 0
        for qw in query_words:
            if qw in lender_words:
                score += 3
            elif any(qw in lw and len(qw) >= 4 for lw in lender_words):
                score += 1
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, idx, option)

    if best is None:
        return None
    score, rank, option = best
    if score < 2:
        return None
    return rank, option


def _parse_lender_option_selection(text: str, *, options: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    raw = (text or "").strip()
    if raw == "" or not options:
        return None
    lower = raw.lower()

    selected_number = _option_selection_number(lower)
    if selected_number is not None:
        idx = int(selected_number) - 1
        if 0 <= idx < len(options):
            return idx + 1, options[idx]

    for idx, option in enumerate(options, start=1):
        lender = str(option.get("lender") or "").strip().lower()
        if lender and (lower == lender or lower in {f"tell me about {lender}", f"explore {lender}", f"pick {lender}"}):
            return idx, option
    matched = _best_lender_option_match(raw, options=options)
    if matched is not None:
        return matched
    return None


def _looks_like_lender_option_selection(text: str) -> bool:
    lower = (text or "").strip().lower()
    if lower == "":
        return False
    return _option_selection_number(lower) is not None


def _with_lender_option_context(
    options: list[dict[str, Any]],
    *,
    amount_inr: float | None = None,
    tenure_days: int | None = None,
    current_rate: float | None = None,
) -> list[dict[str, Any]]:
    count = len(options)
    enriched: list[dict[str, Any]] = []
    for option in options:
        item = dict(option)
        item["option_count"] = int(count)
        if amount_inr is not None:
            item["amount_inr"] = float(amount_inr)
        if tenure_days is not None:
            item["tenure_days"] = int(tenure_days)
        if current_rate is not None:
            item["current_rate"] = float(current_rate)
        enriched.append(item)
    return enriched


def _claude_humanize_reply(cfg: Config, *, fallback: str, purpose: str) -> str | None:
    prompt = (
        "Rewrite the message below as a warm, natural WhatsApp chatbot reply for an Indian consumer. "
        "Preserve every rupee amount, percentage, lender name, district name, numbered list item, and command exactly. "
        "Do not add facts, approvals, phone numbers, legal advice, or new commands. "
        "Keep it concise and easy to act on.\n\n"
        f"Purpose: {purpose}\n"
        f"Message to rewrite:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt)
    if reply is None:
        return None
    return reply.strip() or None


def _claude_recommendation_message(
    cfg: Config,
    *,
    fallback: str,
    district: str,
    options: list[dict[str, Any]],
    amount_inr: float | None = None,
    tenure_days: int | None = None,
    current_rate: float | None = None,
) -> str | None:
    if not options:
        return None
    prompt = (
        "Rewrite the message below as a natural WhatsApp chatbot response for an Indian consumer. "
        "Preserve every numbered lender option, lender name, APR, monthly rate, rupee amount, repayment amount, interest amount, time period, and command exactly. "
        "Do not add approval claims, phone numbers, legal advice, or extra lenders. "
        "Keep it concise and easy to act on. If there is one option, do not mention options 2 or 3.\n\n"
        f"District: {district}\n"
        f"Loan amount INR: {amount_inr}\n"
        f"Tenure days: {tenure_days}\n"
        f"Quoted APR: {current_rate}\n"
        f"Facts to preserve:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt)
    if reply is None:
        return None
    return reply.strip() or None


def _claude_lender_detail(cfg: Config, *, option: dict[str, Any], rank: int, district: str | None) -> str | None:
    lender = str(option.get("lender") or "the selected lender")
    fallback = lender_detail_fallback(option=option, rank=rank, district=district)
    prompt = (
        "Rewrite this selected-lender explanation as a warm WhatsApp chatbot message for an Indian consumer. "
        "Preserve the lender name, APR, per-month rate, every rupee amount, total repayment, monthly payment, fees warning, and the question asking for the user's opinion. "
        "Do not claim approval. Do not add phone numbers or legal/financial advice. "
        "Keep it concise. If there is only one option, do not mention options 2 or 3.\n\n"
        f"Selected lender: {lender}\n"
        f"Facts to preserve:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt)
    if reply is None:
        return None
    return reply.strip() or None


def _looks_like_new_loan_message(text: str) -> bool:
    raw = (text or "").strip().lower()
    if raw == "":
        return False
    has_loan_word = any(w in raw for w in ("loan", "borrow", "need", "lend", "credit"))
    return has_loan_word and (_parse_amount_inr(raw) is not None or _parse_tenure_days(raw) is not None)


def _looks_like_loan_intent_message(text: str) -> bool:
    raw = (text or "").strip().lower()
    if raw == "":
        return False
    if any(k in raw for k in ("district", "districts", "lang", "stop", "start", "help", "more")):
        return False
    has_borrow_intent = any(w in raw for w in ("loan", "borrow", "need", "lend", "credit"))
    has_lender_cue = any(w in raw for w in ("moneylender", "money lender", "microfinance", "mfi", "nbfc", "bank"))
    return bool(has_borrow_intent or (has_lender_cue and ("need" in raw or "borrow" in raw)))


def _looks_like_loan_terms_fragment(text: str) -> bool:
    raw = (text or "").strip().lower()
    if raw == "" or re.search(r"\d", raw) is None:
        return False
    has_amount_cue = re.search(r"(₹|rs\.?|rupees?|lakh|lakhs|lac|lacs|\bk\b|\b\d{4,}\b)", raw) is not None
    has_tenure_cue = re.search(r"\b\d+\s*(day|days|d|week|weeks|w|month|months|m)\b", raw) is not None
    has_rate_cue = re.search(r"\b\d+(?:\.\d+)?\s*%?\s*(apr|annual|year|yearly|month|monthly|week|weekly|day|daily)\b", raw) is not None
    has_loan_word = any(w in raw for w in ("loan", "borrow", "need", "lend", "credit"))
    return bool(has_rate_cue or has_tenure_cue or (has_amount_cue and (has_loan_word or has_tenure_cue or " for " in raw)))


def _empty_borrow_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "intent": True,
        "confidence": 0.85,
        "amount_inr": None,
        "tenure_days": None,
        "interest_rate_apr": None,
        "lender_name": None,
        "lender_type": "unknown",
        "negotiation_stage": "asking",
    }


def _load_latest_borrow_payload(conn, *, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT event_json
        FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1
        ORDER BY parsed_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    if row is None or row["event_json"] is None:
        return None
    try:
        payload = json.loads(str(row["event_json"]))
    except Exception:
        return None
    return dict(payload) if isinstance(payload, dict) else None


def _merge_borrow_details_from_text(
    *,
    text: str,
    base_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    updated = dict(base_payload) if isinstance(base_payload, dict) else _empty_borrow_payload()
    changed = False
    amount = _parse_amount_inr(text)
    if amount is not None:
        updated["amount_inr"] = float(amount)
        changed = True
    tenure = _parse_tenure_days(text)
    if tenure is not None:
        updated["tenure_days"] = int(tenure)
        changed = True
    rate = _parse_interest_rate_apr(text)
    if rate is not None:
        updated["interest_rate_apr"] = float(rate)
        changed = True
    if not changed:
        return None
    try:
        return validate_borrow_intent_payload(updated)
    except Exception:
        return None


def _selected_lender_feedback_kind(text: str) -> str:
    raw = (text or "").strip().lower()
    positive = {"yes", "y", "ok", "okay", "manageable", "good", "looks good", "interested", "proceed", "go ahead"}
    negative = {"no", "n", "too high", "expensive", "not manageable", "can't afford", "cannot afford", "costly"}
    unsure = {"maybe", "unsure", "not sure", "confused", "explain", "how", "why", "what"}
    if raw in positive or any(p in raw for p in ("manageable", "looks good", "interested", "proceed")):
        return "positive"
    if raw in negative or any(p in raw for p in ("too high", "expensive", "not manageable", "cannot afford", "can't afford")):
        return "negative"
    if raw in unsure or any(p in raw for p in ("not sure", "explain", "how", "why", "what")):
        return "unsure"
    return "open"


def _selected_lender_cost_hint(option: dict[str, Any]) -> str:
    amount_inr = option.get("amount_inr")
    tenure_days = option.get("tenure_days")
    rate = option.get("rate_apr")
    if amount_inr is None or tenure_days is None or rate is None:
        return ""
    try:
        breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), float(rate))
    except Exception:
        return ""
    return (
        f" At {float(rate):g}% APR on INR {int(round(float(amount_inr))):,}, that is about INR "
        f"{int(round(float(breakdown['annual_interest']))):,} interest over a year and INR "
        f"{int(round(float(breakdown['monthly_interest']))):,} per month. "
        f"For {int(tenure_days)} days, estimated interest is about INR {int(round(float(breakdown['tenure_interest']))):,}, "
        f"so total repayment is about INR {int(round(float(breakdown['total_repayment']))):,} before fees."
    )


def _selected_lender_conversation_fallback(
    *,
    user_text: str,
    option: dict[str, Any],
    rank: int | None,
) -> str:
    lender = str(option.get("lender") or "this lender")
    kind = _selected_lender_feedback_kind(user_text)
    option_count = int(option.get("option_count") or 0)
    compare = ""
    if option_count > 1:
        compare = " You can also reply 1, 2, or 3 to compare another option."
    cost_hint = _selected_lender_cost_hint(option)
    if kind == "positive":
        return (
            f"That sounds promising.{cost_hint} Before deciding on {lender}, confirm the exact monthly payment, fees, penalties, documents, "
            f"and collection terms with them. If you contact them, reply CONTACTED {lender}. If you decide to switch, reply SWITCHED {lender}."
            + compare
        )
    if kind == "negative":
        return (
            f"If that monthly payment feels too high, don’t rush.{cost_hint}"
            + (
                " Send the amount and loan time if you want me to estimate the rupee cost more exactly."
                if cost_hint == ""
                else " You can compare another option or ask for a smaller amount or longer tenure."
            )
            + " "
            f"{_lender_option_prompt(option_count)}"
        )
    return (
        f"For {lender}, focus on whether the monthly payment fits your cash flow after household expenses.{cost_hint} "
        + (
            "If you send the amount and loan time, I can estimate the rupee cost for this option. "
            if cost_hint == ""
            else ""
        )
        + "Ask the lender for the exact EMI/monthly repayment, processing fees, late fees, and total repayment in writing. "
        f"Does this option feel manageable, too high, or uncertain?{compare}"
    )


def _claude_selected_lender_conversation(
    cfg: Config,
    *,
    user_text: str,
    option: dict[str, Any],
    rank: int | None,
    fallback: str,
) -> str | None:
    prompt = (
        "You are NudgeAI, a careful WhatsApp chatbot helping an Indian consumer decide whether a local regulated credit option is manageable. "
        "Respond to the user's latest message using the selected lender facts. Ask one clear follow-up or give one clear next step. "
        "Do not claim approval. Do not give legal/financial advice. Do not invent phone numbers, eligibility, fees, or branch details. "
        "Preserve any rupee amounts, APR percentages, payment amounts, and commands exactly. "
        "If the user is ready to proceed, tell them to reply CONTACTED <lender> after contacting them, or SWITCHED <lender> if they choose it. "
        "If the user is unsure or says it is expensive, help them compare affordability and suggest choosing another numbered option if available. "
        "Keep under 120 words.\n\n"
        f"User message: {user_text}\n"
        f"Selected option rank: {rank}\n"
        f"Selected option JSON: {json.dumps(option, ensure_ascii=False)}\n"
        f"Safe fallback answer to preserve:\n{fallback}"
    )
    reply = generate_reply(cfg, prompt)
    if reply is None:
        return None
    return reply.strip() or None


def _missing_borrow_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for k in ("amount_inr", "tenure_days"):
        if payload.get(k) is None:
            missing.append(k)
    return missing


def _clarifying_question(field: str) -> str:
    if field == "amount_inr":
        return "How much do you want to borrow in INR? For example: 5000"
    if field == "tenure_days":
        return "How long is the loan for? For example: 30 days or 2 months"
    return "What interest rate did they quote? For example: 5% monthly or 60% APR"


def _parse_amount_inr(text: str) -> float | None:
    raw = (text or "").strip().lower()
    m = re.search(r"(?:₹\s*)?(\d+(?:\.\d+)?)\s*(k|lakh|lakhs|lac|lacs)?\b", raw)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "k":
        val *= 1000.0
    elif unit in {"lakh", "lakhs", "lac", "lacs"}:
        val *= 100000.0
    if val <= 0:
        return None
    return float(val)


def _parse_tenure_days(text: str) -> int | None:
    raw = (text or "").strip().lower()
    m = re.search(r"(\d+)\s*(day|days|d)\b", raw)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"(\d+)\s*(week|weeks|w)\b", raw)
    if m:
        return max(1, int(m.group(1)) * 7)
    m = re.search(r"(\d+)\s*(month|months|m)\b", raw)
    if m:
        return max(1, int(m.group(1)) * 30)
    m = re.search(r"\b(\d+)\b", raw)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 3650:
            return val
    return None


def _parse_interest_rate_apr(text: str) -> float | None:
    raw = (text or "").strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", raw)
    if not m:
        return None
    rate = float(m.group(1))
    if rate <= 0:
        return None
    if "apr" in raw or "annual" in raw or "year" in raw or "yearly" in raw:
        return float(rate)
    if "month" in raw or "monthly" in raw:
        return float(rate) * 12.0
    if "week" in raw or "weekly" in raw:
        return float(rate) * 52.0
    if "day" in raw or "daily" in raw:
        return float(rate) * 365.0
    return None


def _infer_lender_type_from_text(text: str) -> str | None:
    raw = (text or "").strip().lower()
    if raw == "":
        return None
    if "moneylender" in raw or "money lender" in raw or "sahukar" in raw or "mahajan" in raw:
        return "moneylender"
    if "microfinance" in raw or re.search(r"\bmfi\b", raw):
        return "mfi"
    if re.search(r"\bnbfc\b", raw):
        return "nbfc"
    if "bank" in raw:
        return "bank"
    if "cooperative" in raw or "co-operative" in raw or "society" in raw:
        return "cooperative"
    if "friend" in raw or "family" in raw or "relative" in raw:
        return "friend_family"
    if "shopkeeper" in raw or "kirana" in raw or "store" in raw:
        return "shopkeeper"
    if "informal" in raw:
        return "informal"
    return None


def _apply_text_heuristics(payload: dict[str, Any], *, text: str) -> dict[str, Any]:
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
        inferred = _infer_lender_type_from_text(text)
        if inferred:
            next_payload["lender_type"] = inferred
    return validate_borrow_intent_payload(next_payload)


def _extract_correction(text: str) -> tuple[str, str] | None:
    raw = (text or "").strip()
    lower = raw.lower()
    prefixes = ("correct", "correction", "fix")
    prefix_used = None
    for p in prefixes:
        if lower.startswith(p):
            after = lower[len(p) :]
            if after and after[:1].isalpha():
                continue
            prefix_used = p
            break
    if prefix_used is None:
        return None
    rest = raw[len(prefix_used) :].strip()
    if rest.startswith(":") or rest.startswith(","):
        rest = rest[1:].strip()
    if rest == "":
        return None
    if "=" in rest:
        field_raw, value_raw = rest.split("=", 1)
    else:
        parts = rest.split(None, 1)
        if len(parts) < 2:
            return None
        field_raw, value_raw = parts[0], parts[1]
    field = field_raw.strip().lower().replace("-", "_")
    value = value_raw.strip()
    aliases = {
        "intent": "intent",
        "amount": "amount_inr",
        "amt": "amount_inr",
        "principal": "amount_inr",
        "tenure": "tenure_days",
        "duration": "tenure_days",
        "days": "tenure_days",
        "rate": "interest_rate_apr",
        "interest": "interest_rate_apr",
        "apr": "interest_rate_apr",
        "stage": "negotiation_stage",
        "lender": "lender_name",
        "lender_name": "lender_name",
        "lender_type": "lender_type",
    }
    mapped = aliases.get(field)
    if mapped is None:
        return None
    return mapped, value


def _apply_correction_to_payload(payload: dict[str, Any], *, field: str, value_text: str) -> dict[str, Any] | None:
    next_payload = dict(payload)
    if field == "intent":
        v = value_text.strip().lower()
        if v in {"1", "true", "yes", "y"}:
            next_payload["intent"] = True
        elif v in {"0", "false", "no", "n"}:
            next_payload["intent"] = False
        else:
            return None
        next_payload["confidence"] = float(max(float(next_payload.get("confidence") or 0.0), 0.8))
    elif field == "amount_inr":
        val = _parse_amount_inr(value_text)
        if val is None:
            return None
        next_payload["amount_inr"] = float(val)
    elif field == "tenure_days":
        val = _parse_tenure_days(value_text)
        if val is None:
            return None
        next_payload["tenure_days"] = int(val)
    elif field == "interest_rate_apr":
        val = _parse_interest_rate_apr(value_text)
        if val is None:
            m = re.search(r"(\d+(?:\.\d+)?)", value_text.strip().lower())
            if m:
                val = float(m.group(1))
        if val is None:
            return None
        next_payload["interest_rate_apr"] = float(val)
    elif field == "negotiation_stage":
        allowed = {"none", "considering", "asking", "offered", "agreed", "borrowed"}
        stage = value_text.strip().lower()
        if stage not in allowed:
            return None
        next_payload["negotiation_stage"] = stage
    elif field == "lender_type":
        allowed = {
            "informal",
            "moneylender",
            "mfi",
            "nbfc",
            "bank",
            "cooperative",
            "friend_family",
            "shopkeeper",
            "unknown",
        }
        lender_type = value_text.strip().lower()
        if lender_type not in allowed:
            return None
        next_payload["lender_type"] = lender_type
    elif field == "lender_name":
        name = value_text.strip()
        next_payload["lender_name"] = name or None
    else:
        return None
    try:
        return validate_borrow_intent_payload(next_payload)
    except Exception:
        return None



def _has_mfi_districts(conn) -> bool:
    row = conn.execute("SELECT 1 FROM mfi_districts LIMIT 1").fetchone()
    return row is not None


def _nudge_limits_ok(conn, *, user_id: int, cfg: Config, now: datetime) -> bool:
    cooldown_seconds = max(0, int(cfg.nudge_cooldown_minutes) * 60)
    if cooldown_seconds > 0:
        row = conn.execute(
            "SELECT sent_at FROM nudges WHERE user_id = ? ORDER BY sent_at DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
        if row is not None and row["sent_at"]:
            last_dt = _parse_sqlite_ts(str(row["sent_at"]))
            if now < (last_dt + timedelta(seconds=cooldown_seconds)):
                return False

    day_start = now.replace(hour=0, minute=0, second=0)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)

    day_count = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?",
            (int(user_id), _format_sqlite_ts(day_start)),
        ).fetchone()["c"]
    )
    if day_count >= int(cfg.nudge_max_per_day):
        return False

    week_count = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?",
            (int(user_id), _format_sqlite_ts(week_start)),
        ).fetchone()["c"]
    )
    if week_count >= int(cfg.nudge_max_per_week):
        return False

    return True


def process_twilio_inbound(cfg: Config, *, db_path: str, inbound: InboundMessage, now: datetime | None = None) -> str:
    now_dt = now or _now_utc()
    from_norm = _normalize_sender(inbound.from_addr)

    parse_saved = False
    parse_attempted = False
    loan_payload_debug: dict[str, Any] | None = None
    loan_missing_debug: list[str] = []
    loan_after_commit = False
    loan_user_id: int | None = None
    loan_raw_message_id: int | None = None
    loan_text: str = ""
    loan_district: str | None = None
    loan_policy_enabled = False
    assistant_recommendation_enabled = False
    policy_inbound_channel = "whatsapp" if inbound.from_addr.lower().startswith("whatsapp:") else "sms"
    reply: str | None = None
    debug_parts: list[str] = []
    decision_action: str | None = None
    decision_policy: str | None = None
    limits_blocked = False
    districts_total_debug: int | None = None
    parse_error_debug: str | None = None
    correction: tuple[str, str] | None = None

    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, consent_status, district FROM users WHERE phone_e164 = ?",
            (from_norm,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users(phone_e164, consent_status) VALUES (?, 'unknown')",
                (from_norm,),
            )
            row = conn.execute(
                "SELECT id, consent_status, district FROM users WHERE phone_e164 = ?",
                (from_norm,),
            ).fetchone()

        user_id = int(row["id"])
        consent_status = str(row["consent_status"])
        district = str(row["district"]) if row["district"] is not None else None

        inbound_cursor = conn.execute(
            """
            INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, twilio_message_sid, payload_json)
            VALUES (?, 'inbound', ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                "whatsapp" if inbound.from_addr.lower().startswith("whatsapp:") else "sms",
                inbound.from_addr,
                inbound.to_addr,
                inbound.body,
                inbound.twilio_message_sid,
                json.dumps(inbound.payload, ensure_ascii=False),
            ),
        )
        inbound_raw_message_id = int(inbound_cursor.lastrowid)

        text = inbound.body.strip()
        district_cmd = _extract_district_command(text)
        districts_query = _extract_districts_query(text)
        more_cmd = _is_more_command(text)
        correction = _extract_correction(text)
        lang_cmd = _parse_lang_command(text)
        contacted_cmd = _parse_contacted(text)
        switched_cmd = _parse_switched(text)

        session = _load_user_session(conn, user_id=user_id)
        has_borrow_draft = bool(session.get("borrow_draft_json"))
        lender_options = _load_lender_options(session)
        selected_lender_rank, selected_lender_option = _load_selected_lender_option(session)
        option_selection = _parse_lender_option_selection(text, options=lender_options)
        option_selection_request = option_selection is not None or _looks_like_lender_option_selection(text)
        loan_terms_fragment = _looks_like_loan_terms_fragment(text)
        selected_lender_context_update = (
            selected_lender_option is not None
            and correction is None
            and district_cmd is None
            and districts_query is None
            and not more_cmd
            and lang_cmd is None
            and contacted_cmd is None
            and switched_cmd is None
            and not option_selection_request
            and loan_terms_fragment
        )
        selected_lender_followup = (
            selected_lender_option is not None
            and not has_borrow_draft
            and correction is None
            and district_cmd is None
            and districts_query is None
            and not more_cmd
            and lang_cmd is None
            and contacted_cmd is None
            and switched_cmd is None
            and not option_selection_request
            and not _is_keyword(text, keyword="stop")
            and not _is_keyword(text, keyword="start")
            and not _is_keyword(text, keyword="help")
            and not _looks_like_new_loan_message(text)
            and not selected_lender_context_update
        )
        policy_enabled = bool(cfg.baseline_policy_enabled) or str(cfg.policy_mode or "").lower() in {"baseline", "rl", "auto"}
        loan_policy_enabled = bool(policy_enabled)
        assistant_recommendation_enabled = consent_status == "opted_in" and district is not None
        loan_after_commit = (correction is not None) or has_borrow_draft or selected_lender_context_update or (
            (_looks_like_new_loan_message(text) or _looks_like_loan_intent_message(text) or loan_terms_fragment)
            and consent_status == "opted_in"
            and district is not None
            and not more_cmd
            and district_cmd is None
            and lang_cmd is None
            and contacted_cmd is None
            and switched_cmd is None
            and not option_selection_request
            and not selected_lender_followup
            and not _is_keyword(text, keyword="stop")
            and not _is_keyword(text, keyword="start")
            and not _is_keyword(text, keyword="help")
            and districts_query is None
        )
        loan_user_id = user_id
        loan_raw_message_id = inbound_raw_message_id
        loan_text = text
        loan_district = district

        if _is_keyword(text, keyword="stop"):
            _clear_district_paging_state(conn, user_id=user_id)
            _save_borrow_draft(conn, user_id=user_id, payload=None, source_raw_message_id=None, model=None)
            _save_lender_options(conn, user_id=user_id, options=None)
            _save_selected_lender_option(conn, user_id=user_id, option=None, rank=None)
            conn.execute(
                """
                UPDATE users
                SET consent_status = 'opted_out',
                    consent_updated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (user_id,),
            )
            reply = "You’re opted out. Reply START anytime to opt back in."
        elif _is_keyword(text, keyword="start"):
            _clear_district_paging_state(conn, user_id=user_id)
            conn.execute(
                """
                UPDATE users
                SET consent_status = 'opted_in',
                    consent_updated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (user_id,),
            )
            if district:
                fallback = (
                    "You’re opted in. NudgeAI is on.\n"
                    f"District: {district}\n\n"
                    "How to use:\n"
                    "1) Tell me what loan you’re about to take (amount + time).\n"
                    "2) I’ll suggest regulated lenders in your district. If you know the interest rate, include it too.\n\n"
                    "Commands:\n"
                    "- DISTRICTS (or DISTRICTS <prefix>)\n"
                    "- MORE\n"
                    "- DISTRICT <name>\n"
                    "- LANG EN / LANG HI / LANG HINGLISH\n"
                    "- CORRECT <field>=<value>\n"
                    "- CONTACTED <lender>\n"
                    "- SWITCHED <lender> (or SWITCHED FROM <old> TO <new>)\n"
                    "- HELP\n"
                    "- STOP\n\n"
                    "Example message:\n"
                    "“Need 5000 for 30 days with moneylender.”"
                )
                reply = _claude_humanize_reply(cfg, fallback=fallback, purpose="welcome a returning user and explain how to use the chatbot") or fallback
            else:
                sample = _districts_sample(conn, limit=12)
                sample_text = ", ".join(sample) if sample else ""
                extra = f"Examples: {sample_text}\n" if sample_text else ""
                fallback = (
                    "You’re opted in. NudgeAI is on.\n\n"
                    "To personalise suggestions, reply with your district name.\n"
                    + extra
                    + "You can also type:\n"
                    "- DISTRICTS (to see more)\n"
                    "- MORE\n"
                    "- DISTRICT <name>\n\n"
                    "Language:\n"
                    "- LANG EN / LANG HI / LANG HINGLISH\n\n"
                    "Reply STOP anytime to opt out."
                )
                reply = _claude_humanize_reply(cfg, fallback=fallback, purpose="welcome a new user and help them set their district") or fallback
        elif _is_keyword(text, keyword="help"):
            support = _support_line(cfg)
            fallback = (
                "NudgeAI help\n\n"
                "What I do:\n"
                "- If you’re about to take a high-interest loan, I point you to cheaper regulated alternatives in your district.\n"
                "- I keep messages low-frequency to avoid spam.\n\n"
                "Commands:\n"
                "- START\n"
                "- STOP\n"
                "- DISTRICT <name>\n"
                "- DISTRICTS (or DISTRICTS <prefix>)\n\n"
                "- MORE (to keep listing districts)\n\n"
                "- CORRECT <field>=<value>\n\n"
                "- CONTACTED <lender>\n"
                "- SWITCHED <lender> (or SWITCHED FROM <old> TO <new>)\n\n"
                "- LANG EN / LANG HI / LANG HINGLISH\n\n"
                "To get suggestions, send a message like:\n"
                "“Need 5000 for 30 days with moneylender.”"
                + support
            )
            reply = _claude_humanize_reply(cfg, fallback=fallback, purpose="help the user understand commands and what the chatbot can do") or fallback
        elif lang_cmd is not None:
            choice = (lang_cmd or "").strip().lower()
            if choice in {"en", "hi", "hinglish"}:
                _save_language(conn, user_id=user_id, language=choice)
                reply = f"Okay, I’ll reply in {choice}."
            else:
                reply = "Reply with: LANG EN or LANG HI or LANG HINGLISH"
        elif option_selection is not None:
            rank, option = option_selection
            _save_selected_lender_option(conn, user_id=user_id, option=option, rank=rank)
            reply = _claude_lender_detail(cfg, option=option, rank=rank, district=district) or lender_detail_fallback(
                option=option,
                rank=rank,
                district=district,
            )
        elif option_selection_request:
            if lender_options:
                reply = f"I found {len(lender_options)} option{'s' if len(lender_options) != 1 else ''}. {_lender_option_prompt(len(lender_options))}"
            else:
                reply = "I don’t have recent lender options to open yet. Send your loan amount and time first, and I’ll show local options."
        elif selected_lender_followup and selected_lender_option is not None:
            fallback = _selected_lender_conversation_fallback(
                user_text=text,
                option=selected_lender_option,
                rank=selected_lender_rank,
            )
            reply = _claude_selected_lender_conversation(
                cfg,
                user_text=text,
                option=selected_lender_option,
                rank=selected_lender_rank,
                fallback=fallback,
            ) or fallback
            _insert_user_action(
                conn,
                user_id=user_id,
                raw_message_id=inbound_raw_message_id,
                action_type="lender_option_feedback",
                lender=str(selected_lender_option.get("lender") or ""),
                details={
                    "message": text,
                    "feedback_kind": _selected_lender_feedback_kind(text),
                    "selected_rank": selected_lender_rank,
                    "selected_option": selected_lender_option,
                },
            )
        elif contacted_cmd is not None:
            lender = (contacted_cmd or "").strip()
            if lender == "":
                reply = "Reply: CONTACTED <lender name>"
            else:
                _insert_user_action(
                    conn,
                    user_id=user_id,
                    raw_message_id=inbound_raw_message_id,
                    action_type="contacted",
                    lender=lender,
                    details={"lender": lender, "district": district},
                )
                _save_selected_lender_option(conn, user_id=user_id, option=None, rank=None)
                reply = f"Thanks, I’ve noted that you contacted {lender}."
        elif switched_cmd is not None:
            from_lender, to_lender = switched_cmd
            if (to_lender or "").strip() == "":
                reply = "Reply: SWITCHED <new lender> (or SWITCHED FROM <old> TO <new>)"
            else:
                _insert_user_action(
                    conn,
                    user_id=user_id,
                    raw_message_id=inbound_raw_message_id,
                    action_type="switched",
                    lender=str(to_lender),
                    details={"from_lender": from_lender, "to_lender": to_lender, "district": district},
                )
                conn.execute(
                    """
                    INSERT INTO self_reported_switches(user_id, source_raw_message_id, from_lender, to_lender, notes)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(user_id),
                        int(inbound_raw_message_id),
                        from_lender,
                        str(to_lender),
                        "whatsapp_command",
                    ),
                )
                _save_selected_lender_option(conn, user_id=user_id, option=None, rank=None)
                reply = f"Thanks, I’ve noted that you switched to {to_lender}."
        elif districts_query is not None:
            page_size = int(session.get("districts_page_size") or 30)
            sample, total = _districts_query(conn, prefix=districts_query, limit=page_size, offset=0)
            if sample:
                shown = len(sample)
                suffix = f" (showing {shown} of {total})" if total > shown else ""
                prefix_note = f" for “{districts_query}”" if (districts_query or "").strip() != "" else ""
                more_note = "\n\nReply MORE for more." if total > shown else ""
                reply = (
                    f"Districts{prefix_note}{suffix}:\n"
                    + ", ".join(sample)
                    + "\n\nReply: DISTRICT <name>"
                    + more_note
                )
                _save_district_paging_state(
                    conn,
                    user_id=user_id,
                    prefix=(districts_query or "").strip() or None,
                    offset=int(shown),
                    page_size=int(page_size),
                )
            else:
                districts_total_debug = int(total)
                if int(total) <= 0:
                    reply = (
                        "No districts are loaded for this deployment yet.\n"
                        "If you’re the admin, load your MFI dataset into this deployment’s DB, then try DISTRICTS again."
                    )
                else:
                    reply = "No matching districts found. Try: DISTRICTS <prefix>"
                _clear_district_paging_state(conn, user_id=user_id)
        elif more_cmd:
            prefix = (session.get("districts_prefix") or None) if session else None
            offset = int(session.get("districts_offset") or 0)
            page_size = int(session.get("districts_page_size") or 30)
            if offset <= 0:
                reply = "Reply DISTRICTS to list districts first."
            else:
                sample, total = _districts_query(conn, prefix=prefix, limit=page_size, offset=offset)
                if not sample:
                    reply = "No more districts. Reply DISTRICTS to start again."
                    _clear_district_paging_state(conn, user_id=user_id)
                else:
                    shown = len(sample)
                    next_offset = offset + shown
                    suffix = f" (showing {next_offset} of {total})" if total > next_offset else ""
                    prefix_note = f" for “{prefix}”" if (prefix or "").strip() != "" else ""
                    more_note = "\n\nReply MORE for more." if total > next_offset else ""
                    reply = (
                        f"Districts{prefix_note}{suffix}:\n"
                        + ", ".join(sample)
                        + "\n\nReply: DISTRICT <name>"
                        + more_note
                    )
                    _save_district_paging_state(conn, user_id=user_id, prefix=prefix, offset=next_offset, page_size=page_size)
        else:
            if district_cmd is not None:
                _clear_district_paging_state(conn, user_id=user_id)
                candidate = district_cmd
                canonical = _canonical_district(conn, candidate)
                if canonical is None and _has_mfi_districts(conn):
                    sample = _districts_sample(conn, limit=15)
                    reply = (
                        "I couldn’t match that district. Reply with an exact district name"
                        + (f" (examples: {', '.join(sample)})" if sample else "")
                        + ". You can also try: DISTRICTS <prefix>"
                    )
                else:
                    chosen = canonical or candidate.strip()
                    conn.execute(
                        "UPDATE users SET district = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (chosen, user_id),
                    )
                    _save_lender_options(conn, user_id=user_id, options=None)
                    _save_selected_lender_option(conn, user_id=user_id, option=None, rank=None)
                    fallback = (
                        f"district set to {chosen}\n\n"
                        "Now send your loan amount and time, and I’ll suggest regulated alternatives.\n"
                        "Example: “Need 5000 for 30 days with moneylender.”"
                    )
                    reply = _claude_humanize_reply(cfg, fallback=fallback, purpose="confirm the user's district and invite them to send loan details") or fallback
            else:
                if consent_status != "opted_in":
                    reply = "To get nudges, reply START to opt in. Reply STOP to opt out."
                elif not district:
                    _clear_district_paging_state(conn, user_id=user_id)
                    canonical = _canonical_district(conn, text)
                    if canonical is None and _has_mfi_districts(conn):
                        sample = _districts_sample(conn, limit=15)
                        reply = (
                            "I couldn’t match that district. Reply with an exact district name"
                            + (f" (examples: {', '.join(sample)})" if sample else "")
                            + ". You can also try: DISTRICTS <prefix>"
                        )
                    else:
                        chosen = canonical or text.strip()
                        conn.execute(
                            "UPDATE users SET district = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (chosen, user_id),
                        )
                        _save_lender_options(conn, user_id=user_id, options=None)
                        _save_selected_lender_option(conn, user_id=user_id, option=None, rank=None)
                        fallback = (
                            f"district set to {chosen}\n\n"
                            "Now send your loan amount and time, and I’ll suggest regulated alternatives.\n"
                            "Example: “Need 5000 for 30 days with moneylender.”"
                        )
                        reply = _claude_humanize_reply(cfg, fallback=fallback, purpose="confirm the user's district and invite them to send loan details") or fallback
                        district = chosen
                else:
                    if not loan_after_commit:
                        if not _nudge_limits_ok(conn, user_id=user_id, cfg=cfg, now=now_dt):
                            limits_blocked = True
                            reply = (
                                "Thanks — I’ve got your message. I’ll send the next update later to keep messages low-frequency. "
                                "Reply STOP anytime to opt out."
                            )
                        else:
                            options = recommended_lender_rows(conn, district=district, n=3)
                            options = _with_lender_option_context(options)
                            fallback_content = suggest_lender_message(conn, district=district, n=3)
                            content = _claude_recommendation_message(
                                cfg,
                                fallback=fallback_content,
                                district=district,
                                options=options,
                            ) or fallback_content
                            _save_lender_options(conn, user_id=user_id, options=options)
                            _save_selected_lender_option(conn, user_id=user_id, option=None, rank=None)
                            conn.execute(
                                """
                                INSERT INTO nudges(user_id, nudge_type, content, policy_name, policy_version, sent_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    user_id,
                                    "suggest_lender",
                                    content,
                                    "safe-default",
                                    "v1",
                                    _format_sqlite_ts(now_dt),
                                ),
                            )
                            reply = content

        if reply is not None:
            conn.execute(
                """
                INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json)
                VALUES (?, 'outbound', ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    policy_inbound_channel,
                    inbound.to_addr or "",
                    inbound.from_addr,
                    reply,
                    json.dumps({"generated_at": _format_sqlite_ts(now_dt)}, ensure_ascii=False),
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if loan_after_commit and loan_user_id is not None and loan_raw_message_id is not None:
        borrow_draft: dict[str, Any] | None = None
        borrow_source_raw_message_id: int | None = None
        borrow_model: str | None = None
        needs_llm_parse = False
        needs_policy_decision = False
        needs_assistant_recommendation = False
        selected_option_refresh_after_parse = False
        clear_draft = False
        persist_payload: dict[str, Any] | None = None
        persist_raw_message_id: int | None = None
        persist_model: str | None = None
        reply_prefix = ""

        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            session = _load_user_session(conn, user_id=loan_user_id)
            borrow_source_raw_message_id = (
                int(session["borrow_source_raw_message_id"]) if session.get("borrow_source_raw_message_id") is not None else None
            )
            borrow_model = str(session["borrow_model"]) if session.get("borrow_model") is not None else None
            draft_raw = session.get("borrow_draft_json")
            if draft_raw:
                try:
                    draft_obj = json.loads(str(draft_raw))
                    borrow_draft = draft_obj if isinstance(draft_obj, dict) else None
                except Exception:
                    borrow_draft = None

            if correction is not None:
                field, value_text = correction
                if borrow_draft is not None:
                    next_payload = _apply_correction_to_payload(borrow_draft, field=field, value_text=value_text)
                    if next_payload is None:
                        reply = "Sorry — I couldn’t understand that correction. Example: CORRECT rate=5% monthly"
                    else:
                        loan_payload_debug = next_payload
                        loan_missing_debug = _missing_borrow_fields(next_payload)
                        _save_borrow_draft(
                            conn,
                            user_id=loan_user_id,
                            payload=next_payload,
                            source_raw_message_id=borrow_source_raw_message_id,
                            model=borrow_model,
                        )
                        if loan_missing_debug:
                            reply = _clarifying_question(loan_missing_debug[0])
                        else:
                            persist_payload = next_payload
                            persist_raw_message_id = borrow_source_raw_message_id or loan_raw_message_id
                            persist_model = borrow_model
                            clear_draft = True
                            needs_policy_decision = bool(loan_policy_enabled)
                            needs_assistant_recommendation = bool(
                                assistant_recommendation_enabled and not loan_policy_enabled
                            )
                            selected_option_refresh_after_parse = selected_lender_option is not None
                            reply_prefix = "Updated. "
                else:
                    row = conn.execute(
                        """
                        SELECT id, event_json
                        FROM parsed_events
                        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1
                        ORDER BY parsed_at DESC, id DESC
                        LIMIT 1
                        """,
                        (int(loan_user_id),),
                    ).fetchone()
                    if row is None:
                        reply = "I don’t have any recent loan details to correct. Send your loan terms first."
                    else:
                        event_id = int(row["id"])
                        try:
                            payload_existing = json.loads(str(row["event_json"]))
                        except Exception:
                            payload_existing = None
                        if not isinstance(payload_existing, dict):
                            reply = "Sorry — I couldn’t load the previous loan details. Send your loan terms again."
                        else:
                            next_payload = _apply_correction_to_payload(payload_existing, field=field, value_text=value_text)
                            if next_payload is None:
                                reply = "Sorry — I couldn’t understand that correction. Example: CORRECT rate=5% monthly"
                            else:
                                conn.execute(
                                    """
                                    UPDATE parsed_events
                                    SET event_json = ?,
                                        confidence = ?,
                                        intent = ?,
                                        amount_inr = ?,
                                        tenure_days = ?,
                                        interest_rate_apr = ?,
                                        lender_name = ?,
                                        lender_type = ?,
                                        negotiation_stage = ?
                                    WHERE id = ?
                                    """,
                                    (
                                        json.dumps(next_payload, ensure_ascii=False),
                                        float(next_payload["confidence"]),
                                        1 if next_payload["intent"] else 0,
                                        next_payload["amount_inr"],
                                        next_payload["tenure_days"],
                                        next_payload["interest_rate_apr"],
                                        next_payload["lender_name"],
                                        next_payload["lender_type"],
                                        next_payload["negotiation_stage"],
                                        int(event_id),
                                    ),
                                )
                                parse_saved = True
                                loan_payload_debug = next_payload
                                loan_missing_debug = _missing_borrow_fields(next_payload)
                                needs_policy_decision = bool(loan_policy_enabled)
                                needs_assistant_recommendation = bool(
                                    assistant_recommendation_enabled and not loan_policy_enabled
                                )
                                selected_option_refresh_after_parse = selected_lender_option is not None
                                reply_prefix = "Updated. "
                                reply = None
            elif borrow_draft is not None:
                draft_validated = None
                try:
                    draft_validated = validate_borrow_intent_payload(borrow_draft)
                except Exception:
                    draft_validated = None
                if draft_validated is None:
                    _save_borrow_draft(conn, user_id=loan_user_id, payload=None, source_raw_message_id=None, model=None)
                    reply = "Sorry — I lost track of the loan details. Please send the loan amount and tenure again."
                else:
                    updated = dict(draft_validated)
                    if updated.get("amount_inr") is None:
                        maybe = _parse_amount_inr(loan_text)
                        if maybe is not None:
                            updated["amount_inr"] = float(maybe)
                    if updated.get("tenure_days") is None:
                        maybe = _parse_tenure_days(loan_text)
                        if maybe is not None:
                            updated["tenure_days"] = int(maybe)
                    if updated.get("interest_rate_apr") is None:
                        maybe = _parse_interest_rate_apr(loan_text)
                        if maybe is not None:
                            updated["interest_rate_apr"] = float(maybe)

                    updated_validated = None
                    try:
                        updated_validated = validate_borrow_intent_payload(updated)
                    except Exception:
                        updated_validated = None

                    if updated_validated is None:
                        reply = "Sorry — I couldn’t understand that. Please reply with the missing loan detail."
                    else:
                        try:
                            updated_validated = _apply_text_heuristics(updated_validated, text=loan_text)
                        except Exception:
                            updated_validated = updated_validated
                        loan_payload_debug = updated_validated
                        loan_missing_debug = _missing_borrow_fields(updated_validated)
                        if loan_missing_debug:
                            _save_borrow_draft(
                                conn,
                                user_id=loan_user_id,
                                payload=updated_validated,
                                source_raw_message_id=borrow_source_raw_message_id,
                                model=borrow_model,
                            )
                            reply = _clarifying_question(loan_missing_debug[0])
                        else:
                            persist_payload = updated_validated
                            persist_raw_message_id = borrow_source_raw_message_id or loan_raw_message_id
                            persist_model = borrow_model
                            clear_draft = True
                            needs_policy_decision = bool(loan_policy_enabled)
                            needs_assistant_recommendation = bool(
                                assistant_recommendation_enabled and not loan_policy_enabled
                            )
                            selected_option_refresh_after_parse = selected_lender_context_update
            elif selected_lender_context_update and selected_lender_option is not None:
                base_payload = _load_latest_borrow_payload(conn, user_id=loan_user_id) or _empty_borrow_payload()
                updated_payload = _merge_borrow_details_from_text(text=loan_text, base_payload=base_payload)
                if updated_payload is None:
                    reply = "Send the loan amount and time like: 5000 for 30 days."
                else:
                    loan_payload_debug = updated_payload
                    loan_missing_debug = _missing_borrow_fields(updated_payload)
                    if loan_missing_debug:
                        _save_borrow_draft(
                            conn,
                            user_id=loan_user_id,
                            payload=updated_payload,
                            source_raw_message_id=loan_raw_message_id,
                            model="selected-option-fragment",
                        )
                        reply = _clarifying_question(loan_missing_debug[0])
                    else:
                        persist_payload = updated_payload
                        persist_raw_message_id = loan_raw_message_id
                        persist_model = "selected-option-fragment"
                        needs_policy_decision = bool(loan_policy_enabled)
                        needs_assistant_recommendation = bool(
                            assistant_recommendation_enabled and not loan_policy_enabled
                        )
                        selected_option_refresh_after_parse = True
            else:
                needs_llm_parse = True

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if needs_llm_parse:
            try:
                def _call_json_fast(cfg2: Config, system_prompt: str, user_prompt: str):
                    try:
                        return call_json_with_retries(
                            cfg2,
                            system_prompt,
                            user_prompt,
                            timeout_seconds=float(getattr(cfg2, "claude_timeout_seconds", 8.0)),
                            attempts=int(getattr(cfg2, "claude_attempts", 1)),
                        )
                    except TypeError:
                        return call_json_with_retries(cfg2, system_prompt, user_prompt)

                result = parse_borrow_intent_with_llm(cfg, text=loan_text, call_json=_call_json_fast)
                parse_attempted = True
            except Exception:
                try:
                    import traceback

                    parse_error_debug = traceback.format_exc(limit=1).strip()
                except Exception:
                    parse_error_debug = "llm_parse_exception"
                result = None
                parse_attempted = True

            if result is None:
                if cfg.verbose_replies:
                    err = (parse_error_debug or "llm_parse_failed").replace("\n", " ")
                    reply = (
                        "I tried to extract loan details but couldn’t parse the Claude response.\n"
                        f"error={err}\n\n"
                        "Try sending in this format:\n"
                        "“Need <amount> for <days> days with <lender type>”\n\n"
                        "Example:\n"
                        "“Need 5000 for 30 days with moneylender.”"
                    )
                    needs_policy_decision = False
                else:
                    needs_policy_decision = bool(loan_policy_enabled)
                    if assistant_recommendation_enabled and not loan_policy_enabled:
                        fallback = (
                            "I couldn’t extract the loan details clearly. "
                            "Send the amount and time in one message, for example: Need 5000 for 30 days."
                        )
                        reply = _claude_humanize_reply(
                            cfg,
                            fallback=fallback,
                            purpose="ask the user to restate the loan amount and time clearly",
                        ) or fallback
            else:
                try:
                    loan_payload_debug = _apply_text_heuristics(result.payload, text=loan_text)
                except Exception:
                    loan_payload_debug = result.payload
                loan_missing_debug = _missing_borrow_fields(result.payload)
                if bool(result.payload.get("intent")) and loan_missing_debug:
                    conn = connect(db_path)
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        _save_borrow_draft(
                            conn,
                            user_id=loan_user_id,
                            payload=result.payload,
                            source_raw_message_id=loan_raw_message_id,
                            model=result.model,
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                    finally:
                        conn.close()
                    reply = _clarifying_question(loan_missing_debug[0])
                else:
                    if bool(result.payload.get("intent")):
                        persist_payload = loan_payload_debug
                        persist_raw_message_id = loan_raw_message_id
                        persist_model = result.model
                        needs_policy_decision = bool(loan_policy_enabled)
                        needs_assistant_recommendation = bool(
                            assistant_recommendation_enabled and not loan_policy_enabled
                        )
                        selected_option_refresh_after_parse = selected_lender_context_update
                    else:
                        if cfg.verbose_replies:
                            conf = result.payload.get("confidence")
                            reply = (
                                "I interpreted your message as not being about taking a loan (intent=false).\n"
                                f"confidence={conf}\n\n"
                                "If you ARE discussing a loan, reply:\n"
                                "CORRECT intent=true\n\n"
                                "Then resend the loan terms (amount + time)."
                            )
                            needs_policy_decision = False
                        else:
                            needs_policy_decision = bool(loan_policy_enabled)

        if persist_payload is not None and persist_raw_message_id is not None:
            try:
                persist_borrow_intent_event(
                    db_path,
                    user_id=loan_user_id,
                    raw_message_id=int(persist_raw_message_id),
                    payload=persist_payload,
                    model=persist_model,
                )
                parse_saved = True
            except Exception:
                parse_saved = False

        if clear_draft and parse_saved:
            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                _save_borrow_draft(conn, user_id=loan_user_id, payload=None, source_raw_message_id=None, model=None)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        if reply is None and parse_saved and selected_option_refresh_after_parse and selected_lender_option is not None:
            amount_inr = persist_payload.get("amount_inr") if persist_payload is not None else None
            tenure_days = persist_payload.get("tenure_days") if persist_payload is not None else None
            current_rate = persist_payload.get("interest_rate_apr") if persist_payload is not None else None
            refreshed_option = dict(selected_lender_option)
            if amount_inr is not None:
                refreshed_option["amount_inr"] = float(amount_inr)
            if tenure_days is not None:
                refreshed_option["tenure_days"] = int(tenure_days)
            if current_rate is not None:
                refreshed_option["current_rate"] = float(current_rate)
            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                _save_selected_lender_option(
                    conn,
                    user_id=loan_user_id,
                    option=refreshed_option,
                    rank=selected_lender_rank,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            detail = _claude_lender_detail(
                cfg,
                option=refreshed_option,
                rank=selected_lender_rank or 1,
                district=loan_district,
            ) or lender_detail_fallback(
                option=refreshed_option,
                rank=selected_lender_rank or 1,
                district=loan_district,
            )
            reply = (reply_prefix + detail).strip() if reply_prefix else detail

        if reply is None and needs_assistant_recommendation and loan_district is not None and persist_payload is not None:
            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                current_rate = (
                    float(persist_payload["interest_rate_apr"])
                    if persist_payload.get("interest_rate_apr") is not None
                    else None
                )
                options = recommended_lender_rows(conn, district=loan_district, current_rate=current_rate, n=3)
                options = _with_lender_option_context(
                    options,
                    amount_inr=float(persist_payload["amount_inr"]) if persist_payload.get("amount_inr") is not None else None,
                    tenure_days=int(persist_payload["tenure_days"]) if persist_payload.get("tenure_days") is not None else None,
                    current_rate=current_rate,
                )
                fallback_content = suggest_lender_message(
                    conn,
                    district=loan_district,
                    current_rate=current_rate,
                    amount_inr=float(persist_payload["amount_inr"]) if persist_payload.get("amount_inr") is not None else None,
                    tenure_days=int(persist_payload["tenure_days"]) if persist_payload.get("tenure_days") is not None else None,
                    n=3,
                )
                content = _claude_recommendation_message(
                    cfg,
                    fallback=fallback_content,
                    district=loan_district,
                    options=options,
                    amount_inr=float(persist_payload["amount_inr"]) if persist_payload.get("amount_inr") is not None else None,
                    tenure_days=int(persist_payload["tenure_days"]) if persist_payload.get("tenure_days") is not None else None,
                    current_rate=current_rate,
                ) or fallback_content
                _save_lender_options(conn, user_id=loan_user_id, options=options)
                _save_selected_lender_option(conn, user_id=loan_user_id, option=None, rank=None)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            reply = (reply_prefix + content).strip() if reply_prefix else content

        if reply is None and needs_policy_decision:
            state = compute_user_state(db_path, user_id=loan_user_id, now=now_dt)
            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                if not _nudge_limits_ok(conn, user_id=loan_user_id, cfg=cfg, now=now_dt):
                    limits_blocked = True
                    base = (
                        "Thanks — I’ve got your message. I’ll send the next update later to keep messages low-frequency. "
                        "Reply STOP anytime to opt out."
                    )
                    reply = (reply_prefix + base).strip() if reply_prefix else base
                else:
                    decision = decide_policy(conn, cfg=cfg, state=state)
                    decision_action = str(decision.action)
                    decision_policy = str(decision.policy_name)
                    echo = ""
                    if cfg.verbose_replies and loan_payload_debug is not None:
                        amt = loan_payload_debug.get("amount_inr")
                        tenure = loan_payload_debug.get("tenure_days")
                        apr = loan_payload_debug.get("interest_rate_apr")
                        lender_type = loan_payload_debug.get("lender_type")
                        lender_name = loan_payload_debug.get("lender_name")
                        stage = loan_payload_debug.get("negotiation_stage")
                        echo = (
                            "Parsed loan:\n"
                            + f"- amount_inr: {amt if amt is not None else 'null'}\n"
                            + f"- tenure_days: {tenure if tenure is not None else 'null'}\n"
                            + f"- interest_rate_apr: {apr if apr is not None else 'null'}\n"
                            + f"- lender_type: {lender_type if lender_type is not None else 'null'}\n"
                            + f"- lender_name: {lender_name if lender_name is not None else 'null'}\n"
                            + f"- negotiation_stage: {stage if stage is not None else 'null'}\n"
                            + "If anything is wrong, reply for example:\n"
                            + "CORRECT amount=6000\n"
                            + "CORRECT tenure=45 days\n"
                            + "CORRECT rate=5% monthly\n"
                            + "CORRECT lender_type=moneylender\n"
                        ).strip()
                    base_reply = (echo + "\n\n" + decision.content).strip() if echo else decision.content
                    reply = (reply_prefix + base_reply).strip() if reply_prefix else base_reply
                    nudge_content_to_store = decision.content
                    if decision.nudge_type in {"alert", "suggest_lender"} and state.district:
                        current_rate = float(state.implied_apr) if state.implied_apr is not None else None
                        options = recommended_lender_rows(conn, district=state.district, current_rate=current_rate, n=3)
                        options = _with_lender_option_context(
                            options,
                            amount_inr=state.borrow.amount_inr,
                            tenure_days=state.borrow.tenure_days,
                            current_rate=current_rate,
                        )
                        _save_lender_options(conn, user_id=loan_user_id, options=options)
                        _save_selected_lender_option(conn, user_id=loan_user_id, option=None, rank=None)
                        generated = _claude_recommendation_message(
                            cfg,
                            fallback=decision.content,
                            district=state.district,
                            options=options,
                            amount_inr=state.borrow.amount_inr,
                            tenure_days=state.borrow.tenure_days,
                            current_rate=current_rate,
                        )
                        if generated is not None:
                            nudge_content_to_store = generated
                            generated_reply = (echo + "\n\n" + generated).strip() if echo else generated
                            reply = (reply_prefix + generated_reply).strip() if reply_prefix else generated_reply
                    if decision.nudge_type is not None:
                        conn.execute(
                            """
                            INSERT INTO nudges(user_id, parsed_event_id, nudge_type, content, policy_name, policy_version, sent_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                loan_user_id,
                                decision.parsed_event_id,
                                decision.nudge_type,
                                nudge_content_to_store,
                                decision.policy_name,
                                decision.policy_version,
                                _format_sqlite_ts(now_dt),
                            ),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        if reply is None:
            reply = reply_prefix.strip() or "OK"

        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json)
                VALUES (?, 'outbound', ?, ?, ?, ?, ?)
                """,
                (
                    loan_user_id,
                    policy_inbound_channel,
                    inbound.to_addr or "",
                    inbound.from_addr,
                    reply,
                    json.dumps({"generated_at": _format_sqlite_ts(now_dt)}, ensure_ascii=False),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    if cfg.verbose_replies:
        mode = str(cfg.policy_mode or "").strip().lower() or ("baseline" if cfg.baseline_policy_enabled else "off")
        debug_parts.append(f"policy={mode}")
        if decision_policy:
            debug_parts.append(f"decision={decision_action or 'wait'}")
            debug_parts.append(f"engine={decision_policy}")
        debug_parts.append(f"parsed={'yes' if parse_saved else ('attempted' if parse_attempted else 'no')}")
        if parse_error_debug:
            debug_parts.append("parse_error=" + parse_error_debug.replace("\n", " ")[:220])
        if loan_payload_debug is not None:
            intent = loan_payload_debug.get("intent")
            conf = loan_payload_debug.get("confidence")
            debug_parts.append(f"intent={intent}")
            debug_parts.append(f"confidence={conf}")
            amt = loan_payload_debug.get("amount_inr")
            tenure = loan_payload_debug.get("tenure_days")
            apr = loan_payload_debug.get("interest_rate_apr")
            loan_debug = "loan=" + f"amount={amt if amt is not None else 'null'}" + f",tenure_days={tenure if tenure is not None else 'null'}"
            if apr is not None:
                loan_debug += f",apr={apr}"
            debug_parts.append(loan_debug)
        if loan_missing_debug:
            debug_parts.append("missing=" + ",".join(loan_missing_debug))
        if districts_total_debug is not None:
            debug_parts.append(f"districts_total={int(districts_total_debug)}")
        debug_parts.append(f"limits={'blocked' if limits_blocked else 'ok'}")
        base = (reply or "OK").strip() or "OK"
        return (base + "\n\n" + "[status] " + " | ".join(debug_parts)).strip()

    return (reply or "OK").strip() or "OK"
