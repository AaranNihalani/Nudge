from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .claude import call_json_with_retries
from .config import Config
from .db import connect
from .nudge_content import suggest_lender_message
from .nlp import parse_borrow_intent_with_llm, persist_borrow_intent_event
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
            remaining = raw[len(prefix) :].strip()
            if remaining.startswith(":"):
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


def _districts_query(conn, *, prefix: str | None, limit: int = 30) -> tuple[list[str], int]:
    p = (prefix or "").strip()
    if p == "":
        rows = conn.execute(
            "SELECT name FROM mfi_districts ORDER BY name COLLATE NOCASE ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        total = int(conn.execute("SELECT COUNT(*) AS c FROM mfi_districts").fetchone()["c"])
        return ([str(r["name"]) for r in rows], total)

    like = p + "%"
    rows = conn.execute(
        "SELECT name FROM mfi_districts WHERE lower(name) LIKE lower(?) ORDER BY name COLLATE NOCASE ASC LIMIT ?",
        (like, int(limit)),
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
    remaining = raw[len("districts") :].strip()
    if remaining.startswith(":"):
        remaining = remaining[1:].strip()
    return remaining or None



def _has_mfi_districts(conn) -> bool:
    row = conn.execute("SELECT 1 FROM mfi_districts LIMIT 1").fetchone()


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

    parse_after_commit = False
    parse_user_id: int | None = None
    parse_raw_message_id: int | None = None
    parse_text: str = ""
    policy_after_commit = False
    policy_user_id: int | None = None
    policy_inbound_channel = "whatsapp" if inbound.from_addr.lower().startswith("whatsapp:") else "sms"
    reply: str | None = None

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
        parse_after_commit = (
            consent_status == "opted_in"
            and district is not None
            and district_cmd is None
            and not _is_keyword(text, keyword="stop")
            and not _is_keyword(text, keyword="start")
            and not _is_keyword(text, keyword="help")
            and districts_query is None
        )
        parse_user_id = user_id
        parse_raw_message_id = inbound_raw_message_id
        parse_text = text

        if _is_keyword(text, keyword="stop"):
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
                    "- DISTRICT <name>\n"
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
                    "- DISTRICT <name>\n\n"
                    "Reply STOP anytime to opt out."
                )
        elif _is_keyword(text, keyword="help"):
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
                "To get suggestions, send a message like:\n"
                "“Need 5000 for 30 days. Interest 5% monthly.”"
            )
        elif districts_query is not None:
            sample, total = _districts_query(conn, prefix=districts_query, limit=30)
            if sample:
                shown = len(sample)
                suffix = f" (showing {shown} of {total})" if total > shown else ""
                prefix_note = f" for “{districts_query}”" if (districts_query or "").strip() != "" else ""
                reply = (
                    f"Districts{prefix_note}{suffix}:\n"
                    + ", ".join(sample)
                    + "\n\nReply: DISTRICT <name>"
                )
            else:
                reply = "No matching districts found."
        else:
            if district_cmd is not None:
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
                    policy_enabled = bool(cfg.baseline_policy_enabled) or str(cfg.policy_mode or "").lower() in {
                        "baseline",
                        "rl",
                        "auto",
                    }
                    if policy_enabled and parse_after_commit:
                        policy_after_commit = True
                        policy_user_id = user_id
                    else:
                        if not _nudge_limits_ok(conn, user_id=user_id, cfg=cfg, now=now_dt):
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

    if parse_after_commit and parse_user_id is not None:
        try:
            result = parse_borrow_intent_with_llm(
                cfg,
                text=parse_text,
                call_json=call_json_with_retries,
            )
            if result is not None:
                persist_borrow_intent_event(
                    db_path,
                    user_id=parse_user_id,
                    raw_message_id=parse_raw_message_id,
                    payload=result.payload,
                    model=result.model,
                )
        except Exception:
            pass

    if policy_after_commit and policy_user_id is not None:
        state = compute_user_state(db_path, user_id=policy_user_id, now=now_dt)
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not _nudge_limits_ok(conn, user_id=policy_user_id, cfg=cfg, now=now_dt):
                reply = (
                    "Thanks — I’ve got your message. I’ll send the next update later to keep messages low-frequency. "
                    "Reply STOP anytime to opt out."
                )
            else:
                decision = decide_policy(conn, cfg=cfg, state=state)
                reply = decision.content
                if decision.nudge_type is not None:
                    conn.execute(
                        """
                        INSERT INTO nudges(user_id, parsed_event_id, nudge_type, content, policy_name, policy_version, sent_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            policy_user_id,
                            decision.parsed_event_id,
                            decision.nudge_type,
                            decision.content,
                            decision.policy_name,
                            decision.policy_version,
                            _format_sqlite_ts(now_dt),
                        ),
                    )
            conn.execute(
                """
                INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json)
                VALUES (?, 'outbound', ?, ?, ?, ?, ?)
                """,
                (
                    policy_user_id,
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

    return (reply or "OK").strip() or "OK"
