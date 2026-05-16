from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .claude import call_json_with_retries
from .config import Config
from .db import connect
from .nudge_content import suggest_lender_message
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
        SELECT districts_prefix, districts_offset, districts_page_size, borrow_draft_json, borrow_source_raw_message_id, borrow_model, language
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


def _missing_borrow_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for k in ("amount_inr", "tenure_days", "interest_rate_apr"):
        if payload.get(k) is None:
            missing.append(k)
    return missing


def _clarifying_question(field: str) -> str:
    if field == "amount_inr":
        return "How much do you want to borrow (in INR)? Example: 5000"
    if field == "tenure_days":
        return "How long is the loan for? Example: 30 days (or 2 months)"
    return "What interest rate did they quote? Example: 5% monthly (or 60% APR)"


def _parse_amount_inr(text: str) -> float | None:
    raw = (text or "").strip().lower()
    m = re.search(r"(?:₹\s*)?(\d+(?:\.\d+)?)\s*([k])?\b", raw)
    if not m:
        return None
    val = float(m.group(1))
    if m.group(2):
        val *= 1000.0
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
        policy_enabled = bool(cfg.baseline_policy_enabled) or str(cfg.policy_mode or "").lower() in {"baseline", "rl", "auto"}
        loan_policy_enabled = bool(policy_enabled)
        loan_after_commit = (correction is not None) or has_borrow_draft or (
            policy_enabled
            and consent_status == "opted_in"
            and district is not None
            and not more_cmd
            and district_cmd is None
            and lang_cmd is None
            and contacted_cmd is None
            and switched_cmd is None
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
                reply = (
                    "You’re opted in. NudgeAI is on.\n"
                    f"District: {district}\n\n"
                    "How to use:\n"
                    "1) Tell me what loan you’re about to take (amount + time + interest).\n"
                    "2) I’ll suggest regulated lenders in your district if it looks expensive.\n\n"
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
                    "“Need 5000 for 30 days. Moneylender says 5% monthly.”"
                )
            else:
                sample = _districts_sample(conn, limit=12)
                sample_text = ", ".join(sample) if sample else ""
                extra = f"Examples: {sample_text}\n" if sample_text else ""
                reply = (
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
        elif _is_keyword(text, keyword="help"):
            support = _support_line(cfg)
            reply = (
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
                "“Need 5000 for 30 days. Interest 5% monthly.”"
                + support
            )
        elif lang_cmd is not None:
            choice = (lang_cmd or "").strip().lower()
            if choice in {"en", "hi", "hinglish"}:
                _save_language(conn, user_id=user_id, language=choice)
                reply = f"Language set to {choice}."
            else:
                reply = "Reply: LANG EN or LANG HI or LANG HINGLISH"
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
                reply = f"Noted. contacted {lender}."
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
                reply = f"Noted. switched to {to_lender}."
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
                    reply = (
                        f"district set to {chosen}\n\n"
                        "Now send your loan terms and I’ll suggest regulated alternatives if it looks expensive.\n"
                        "Example: “Need 5000 for 30 days. Interest 5% monthly.”"
                    )
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
                        reply = (
                            f"district set to {chosen}\n\n"
                            "Now send your loan terms and I’ll suggest regulated alternatives if it looks expensive.\n"
                            "Example: “Need 5000 for 30 days. Interest 5% monthly.”"
                        )
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
                            content = suggest_lender_message(conn, district=district, n=3)
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
                    reply = "Sorry — I lost track of the loan details. Please send the loan amount, tenure, and interest rate again."
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
                        "“Need <amount> for <days> days at <rate>% monthly with <lender type>”\n\n"
                        "Example:\n"
                        "“Need 5000 for 30 days at 5% monthly with moneylender.”"
                    )
                    needs_policy_decision = False
                else:
                    needs_policy_decision = bool(loan_policy_enabled)
            else:
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
                        persist_payload = result.payload
                        persist_raw_message_id = loan_raw_message_id
                        persist_model = result.model
                        needs_policy_decision = bool(loan_policy_enabled)
                    else:
                        if cfg.verbose_replies:
                            conf = result.payload.get("confidence")
                            reply = (
                                "I interpreted your message as not being about taking a loan (intent=false).\n"
                                f"confidence={conf}\n\n"
                                "If you ARE discussing a loan, reply:\n"
                                "CORRECT intent=true\n\n"
                                "Then resend the loan terms (amount + time + interest)."
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
                        echo = (
                            "Parsed loan:\n"
                            + f"- amount_inr: {amt if amt is not None else 'null'}\n"
                            + f"- tenure_days: {tenure if tenure is not None else 'null'}\n"
                            + f"- interest_rate_apr: {apr if apr is not None else 'null'}\n"
                            + "If anything is wrong, reply for example:\n"
                            + "CORRECT amount=6000\n"
                            + "CORRECT tenure=45 days\n"
                            + "CORRECT rate=5% monthly\n"
                        ).strip()
                    base_reply = (echo + "\n\n" + decision.content).strip() if echo else decision.content
                    reply = (reply_prefix + base_reply).strip() if reply_prefix else base_reply
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
                                decision.content,
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
            debug_parts.append(
                "loan="
                + f"amount={amt if amt is not None else 'null'}"
                + f",tenure_days={tenure if tenure is not None else 'null'}"
                + f",apr={apr if apr is not None else 'null'}"
            )
        if loan_missing_debug:
            debug_parts.append("missing=" + ",".join(loan_missing_debug))
        if districts_total_debug is not None:
            debug_parts.append(f"districts_total={int(districts_total_debug)}")
        debug_parts.append(f"limits={'blocked' if limits_blocked else 'ok'}")
        base = (reply or "OK").strip() or "OK"
        return (base + "\n\n" + "[status] " + " | ".join(debug_parts)).strip()

    return (reply or "OK").strip() or "OK"
