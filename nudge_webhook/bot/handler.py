"""Main inbound message handler — orchestrates the full conversation flow."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Config
from ..db import connect
from ..nlp import parse_borrow_intent_with_llm, persist_borrow_intent_event, validate_borrow_intent_payload
from ..nudge_content import lender_detail_fallback, recommended_lender_rows, suggest_lender_message
from ..policy import PolicyDecision, decide_policy
from ..state import compute_user_state, format_ts
from . import claude_helpers as ch
from .helpers import format_ts as fmt_ts, is_keyword, now_utc, normalize_sender
from .loan import (
    InboundMessage,
    apply_correction,
    apply_text_heuristics,
    clarifying_question,
    empty_borrow_payload,
    enrich_lender_options,
    insert_user_action,
    load_latest_borrow_payload,
    looks_like_lender_selection,
    lender_option_prompt,
    merge_borrow_details_from_text,
    missing_borrow_fields,
    parse_lender_selection,
    selected_lender_fallback_reply,
    selected_lender_feedback_kind,
)
from .parsers import (
    extract_correction,
    extract_district_command,
    extract_districts_query,
    is_more_command,
    looks_like_loan_intent_message,
    looks_like_loan_terms_fragment,
    looks_like_new_loan_message,
    parse_contacted,
    parse_switched,
    parse_update_profile_command,
)
from .profile import (
    build_aidis_assessment,
    INVALID_ANSWER,
    is_skip,
    mark_profile_complete,
    next_step,
    parse_profile_answer,
    profile_intro_message,
    profile_question,
    save_profile_field,
)
from .session import (
    clear_district_paging,
    ensure_user_session,
    load_lender_options,
    load_selected_lender,
    load_user_session,
    save_borrow_draft,
    save_district_paging,
    save_lender_options,
    save_profile_step,
    save_selected_lender,
)


def _has_mfi_districts(conn) -> bool:
    return conn.execute("SELECT 1 FROM mfi_districts LIMIT 1").fetchone() is not None


def _districts_sample(conn, *, limit: int = 15) -> list[str]:
    return [str(r["name"]) for r in conn.execute(
        "SELECT name FROM mfi_districts ORDER BY name ASC LIMIT ?", (int(limit),)
    ).fetchall()]


def _districts_query(conn, *, prefix: str | None, limit: int = 30, offset: int = 0) -> tuple[list[str], int]:
    p = (prefix or "").strip()
    if not p:
        rows = conn.execute(
            "SELECT name FROM mfi_districts ORDER BY name ASC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        total = int(conn.execute("SELECT COUNT(*) AS c FROM mfi_districts").fetchone()["c"])
    else:
        like = p + "%"
        rows = conn.execute(
            "SELECT name FROM mfi_districts WHERE lower(name) LIKE lower(?) ORDER BY name ASC LIMIT ? OFFSET ?",
            (like, limit, offset),
        ).fetchall()
        total = int(conn.execute(
            "SELECT COUNT(*) AS c FROM mfi_districts WHERE lower(name) LIKE lower(?)", (like,)
        ).fetchone()["c"])
    return [str(r["name"]) for r in rows], total


def _canonical_district(conn, candidate: str) -> str | None:
    row = conn.execute(
        "SELECT name FROM mfi_districts WHERE lower(name) = lower(?) LIMIT 1", (candidate.strip(),)
    ).fetchone()
    return str(row["name"]) if row else None


def _nudge_limits_ok(conn, *, user_id: int, cfg: Config, now: datetime) -> bool:
    cooldown_secs = max(0, int(cfg.nudge_cooldown_minutes) * 60)
    if cooldown_secs > 0:
        row = conn.execute(
            "SELECT sent_at FROM nudges WHERE user_id = ? ORDER BY sent_at DESC LIMIT 1", (int(user_id),)
        ).fetchone()
        if row and row["sent_at"]:
            from ..state import parse_ts
            last_dt = parse_ts(str(row["sent_at"]))
            if now < (last_dt + timedelta(seconds=cooldown_secs)):
                return False
    now_ts = fmt_ts(now.astimezone(timezone.utc).replace(microsecond=0))
    day_start = fmt_ts(now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    week_start = fmt_ts((now.astimezone(timezone.utc) - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0))
    day_count = int(conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?", (int(user_id), day_start)
    ).fetchone()["c"])
    if day_count >= int(cfg.nudge_max_per_day):
        return False
    week_count = int(conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?", (int(user_id), week_start)
    ).fetchone()["c"])
    return week_count < int(cfg.nudge_max_per_week)


def _welcome_back_message(conn, *, user_id: int, district: str, cfg: Config) -> str:
    """Summary message for returning users."""
    last_loan = conn.execute(
        """
        SELECT amount_inr, lender_type, tenure_days, interest_rate_apr
        FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1
        ORDER BY parsed_at DESC, id DESC LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()

    loan_line = ""
    if last_loan:
        parts = []
        if last_loan["amount_inr"]:
            parts.append(f"₹{int(last_loan['amount_inr']):,}")
        if last_loan["lender_type"] and last_loan["lender_type"] != "unknown":
            parts.append(f"from {last_loan['lender_type'].replace('_', ' ')}")
        if last_loan["tenure_days"]:
            parts.append(f"for {last_loan['tenure_days']} days")
        if parts:
            loan_line = f"\nLast loan: {' '.join(parts)}."

    fallback = (
        f"Welcome back! District: {district}.{loan_line}\n\n"
        "Tell me about a loan you're considering, or reply HELP.\n"
        "Reply UPDATE PROFILE to update your household details."
    )
    return ch.humanize(cfg, fallback=fallback, purpose="welcome a returning user with a summary of their account") or fallback


def process_inbound(cfg: Config, *, db_path: str, inbound: InboundMessage, now: datetime | None = None) -> str:
    now_dt = now or now_utc()
    from_norm = normalize_sender(inbound.from_addr)

    # State variables for the loan processing phase (runs after main transaction commits)
    loan_after_commit = False
    loan_user_id: int | None = None
    loan_raw_message_id: int | None = None
    loan_text = ""
    loan_district: str | None = None
    loan_policy_enabled = False
    assistant_rec_enabled = False
    correction: tuple[str, str] | None = None
    selected_lender_context_update = False
    selected_lender_option: dict[str, Any] | None = None
    selected_lender_rank: int | None = None
    lender_options: list[dict[str, Any]] = []

    reply: str | None = None
    policy_channel = "web"

    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")

        # ── Get or create user ──────────────────────────────────────────────
        row = conn.execute(
            "SELECT id, consent_status, district, caste, religion, mpce_inr, household_size, land_acres, urban, profile_complete FROM users WHERE phone_e164 = ?",
            (from_norm,),
        ).fetchone()
        if row is None:
            conn.execute("INSERT INTO users(phone_e164, consent_status) VALUES (?, 'unknown')", (from_norm,))
            row = conn.execute(
                "SELECT id, consent_status, district, caste, religion, mpce_inr, household_size, land_acres, urban, profile_complete FROM users WHERE phone_e164 = ?",
                (from_norm,),
            ).fetchone()

        user_id = int(row["id"])
        consent_status = str(row["consent_status"])
        district = str(row["district"]) if row["district"] is not None else None
        profile_complete = bool(int(row["profile_complete"] or 0))

        # ── Store inbound message ───────────────────────────────────────────
        inbound_cursor = conn.execute(
            "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, twilio_message_sid, payload_json) VALUES (?, 'inbound', 'web', ?, ?, ?, ?, ?)",
            (user_id, inbound.from_addr, inbound.to_addr, inbound.body,
             inbound.message_sid, json.dumps(inbound.payload, ensure_ascii=False)),
        )
        inbound_raw_message_id = int(inbound_cursor.lastrowid)

        text = inbound.body.strip()

        # ── Parse command signals ───────────────────────────────────────────
        district_cmd = extract_district_command(text)
        districts_q = extract_districts_query(text)
        more_cmd = is_more_command(text)
        correction = extract_correction(text)
        contacted_cmd = parse_contacted(text)
        switched_cmd = parse_switched(text)
        update_profile_cmd = parse_update_profile_command(text)

        session = load_user_session(conn, user_id=user_id)
        profile_step = session.get("profile_step")
        lender_options = load_lender_options(session)
        selected_lender_rank, selected_lender_option = load_selected_lender(session)

        option_selection = parse_lender_selection(text, options=lender_options)
        option_selection_req = option_selection is not None or looks_like_lender_selection(text)
        loan_terms_frag = looks_like_loan_terms_fragment(text)

        selected_lender_context_update = (
            selected_lender_option is not None and correction is None and district_cmd is None
            and districts_q is None and not more_cmd and contacted_cmd is None
            and switched_cmd is None and not option_selection_req and loan_terms_frag
        )
        selected_lender_followup = (
            selected_lender_option is not None and not session.get("borrow_draft_json")
            and correction is None and district_cmd is None and districts_q is None
            and not more_cmd and contacted_cmd is None and switched_cmd is None
            and not option_selection_req and not is_keyword(text, keyword="stop")
            and not is_keyword(text, keyword="start") and not is_keyword(text, keyword="help")
            and not looks_like_new_loan_message(text) and not selected_lender_context_update
        )

        policy_enabled = bool(cfg.baseline_policy_enabled) or cfg.policy_mode in {"baseline", "auto"}
        loan_policy_enabled = bool(policy_enabled)
        assistant_rec_enabled = consent_status == "opted_in" and district is not None

        loan_after_commit = (correction is not None) or bool(session.get("borrow_draft_json")) or selected_lender_context_update or (
            (looks_like_new_loan_message(text) or looks_like_loan_intent_message(text) or loan_terms_frag)
            and consent_status == "opted_in" and district is not None and not more_cmd
            and district_cmd is None and contacted_cmd is None and switched_cmd is None
            and not option_selection_req and not selected_lender_followup
            and not is_keyword(text, keyword="stop") and not is_keyword(text, keyword="start")
            and not is_keyword(text, keyword="help") and districts_q is None
        )
        loan_user_id = user_id
        loan_raw_message_id = inbound_raw_message_id
        loan_text = text
        loan_district = district

        # ── Command routing ─────────────────────────────────────────────────

        if update_profile_cmd and consent_status == "opted_in":
            save_profile_step(conn, user_id=user_id, step="intro")
            conn.execute("UPDATE users SET profile_complete = 0 WHERE id = ?", (user_id,))
            reply = profile_intro_message()

        elif is_keyword(text, keyword="stop"):
            clear_district_paging(conn, user_id=user_id)
            save_borrow_draft(conn, user_id=user_id, payload=None, source_raw_message_id=None, model=None)
            save_lender_options(conn, user_id=user_id, options=None)
            save_selected_lender(conn, user_id=user_id, option=None, rank=None)
            conn.execute(
                "UPDATE users SET consent_status = 'opted_out', consent_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,),
            )
            reply = "You're opted out. Reply START anytime to opt back in."

        elif is_keyword(text, keyword="start"):
            clear_district_paging(conn, user_id=user_id)
            conn.execute(
                "UPDATE users SET consent_status = 'opted_in', consent_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,),
            )
            if district and profile_complete:
                # Returning user — show summary
                reply = _welcome_back_message(conn, user_id=user_id, district=district, cfg=cfg)
            elif district:
                # Has district but no profile yet — offer profile questions
                if not profile_step:
                    save_profile_step(conn, user_id=user_id, step="intro")
                    reply = (
                        f"You're opted in. District: {district}.\n\n"
                        + profile_intro_message()
                    )
                else:
                    fallback = (
                        f"You're opted in. District: {district}.\n\n"
                        "Tell me about a loan or reply HELP."
                    )
                    reply = ch.humanize(cfg, fallback=fallback, purpose="welcome returning user") or fallback
            else:
                sample = _districts_sample(conn)
                sample_text = ", ".join(sample) if sample else ""
                fallback = (
                    "You're opted in. Nudge is on.\n\n"
                    "To personalise suggestions, reply with your district name.\n"
                    + (f"Examples: {sample_text}\n" if sample_text else "")
                    + "You can also type DISTRICTS to browse.\n\nReply STOP anytime to opt out."
                )
                reply = ch.humanize(cfg, fallback=fallback, purpose="welcome new user and help them set their district") or fallback

        elif is_keyword(text, keyword="help"):
            fallback = (
                "Nudge help\n\n"
                "What I do: If you're about to take a high-interest loan, I point you to cheaper regulated alternatives in your district.\n\n"
                "Commands:\n"
                "- START / STOP\n"
                "- DISTRICT <name>\n"
                "- DISTRICTS (or DISTRICTS <prefix>)\n"
                "- MORE\n"
                "- CORRECT <field>=<value>\n"
                "- CONTACTED <lender>\n"
                "- SWITCHED <lender>\n"
                "- UPDATE PROFILE\n\n"
                "Example: \"Need 5000 for 30 days with moneylender.\""
            )
            reply = ch.humanize(cfg, fallback=fallback, purpose="help the user understand commands") or fallback

        # ── Profile intro — only intercepts explicit YES / NO / SKIP ──────────
        elif (
            consent_status == "opted_in" and profile_step == "intro" and not loan_after_commit
            and text.strip().lower() in {"yes", "y", "sure", "ok", "okay", "no", "n",
                                         "skip", "s", "don't know", "dont know", "dk", "idk"}
        ):
            t = text.strip().lower()
            if t in {"yes", "y", "sure", "ok", "okay"}:
                save_profile_step(conn, user_id=user_id, step="caste")
                reply = profile_question("caste")
            else:
                save_profile_step(conn, user_id=user_id, step="done")
                fallback = "No problem — skipped. Tell me about your loan or reply HELP."
                reply = ch.humanize(cfg, fallback=fallback, purpose="acknowledge profile skip") or fallback

        # ── Profile active collection (caste / religion / mpce / etc.) ─────────
        elif consent_status == "opted_in" and profile_step and profile_step not in {"done", "intro"} and not loan_after_commit:
            column, value = parse_profile_answer(profile_step, text)
            if value is INVALID_ANSWER:
                q = profile_question(profile_step) or "Please reply in the expected format."
                reply = "That last answer looks unusual. Please try again.\n\n" + q
            elif value is not None:
                save_profile_field(conn, user_id=user_id, column=column, value=value)
            if reply is None:
                nxt = next_step(profile_step)
                if nxt == "done":
                    save_profile_step(conn, user_id=user_id, step="done")
                    mark_profile_complete(conn, user_id=user_id)
                    updated_row = conn.execute(
                        "SELECT caste, religion, mpce_inr, household_size, land_acres, urban FROM users WHERE id = ?",
                        (user_id,),
                    ).fetchone()
                    assessment = build_aidis_assessment(
                        caste=updated_row["caste"] if updated_row else None,
                        religion=updated_row["religion"] if updated_row else None,
                        mpce_inr=float(updated_row["mpce_inr"]) if updated_row and updated_row["mpce_inr"] else None,
                        household_size=int(updated_row["household_size"]) if updated_row and updated_row["household_size"] else None,
                        land_acres=float(updated_row["land_acres"]) if updated_row and updated_row["land_acres"] else None,
                        urban=int(updated_row["urban"]) if updated_row and updated_row["urban"] is not None else None,
                    )
                    _example = "Example: \"Need ₹5,000 for 30 days at 5% monthly from a moneylender.\""
                    if assessment:
                        reply = assessment + f"\n\nNow tell me about the loan you're considering.\n{_example}"
                    else:
                        reply = f"Thanks! Now tell me about the loan you're considering.\n{_example}"
                else:
                    save_profile_step(conn, user_id=user_id, step=nxt)
                    reply = profile_question(nxt) or "Thanks. Tell me about your loan."

        elif option_selection is not None:
            rank, option = option_selection
            save_selected_lender(conn, user_id=user_id, option=option, rank=rank)
            reply = ch.lender_detail(cfg, option=option, rank=rank, district=district) or lender_detail_fallback(option=option, rank=rank, district=district)

        elif option_selection_req:
            if lender_options:
                reply = f"I found {len(lender_options)} option{'s' if len(lender_options) != 1 else ''}. {lender_option_prompt(len(lender_options))}"
            else:
                reply = "I don't have recent lender options yet. Send your loan amount and time first."

        elif selected_lender_followup and selected_lender_option is not None:
            fallback = selected_lender_fallback_reply(user_text=text, option=selected_lender_option, rank=selected_lender_rank)
            reply = ch.selected_lender_conversation(cfg, user_text=text, option=selected_lender_option, rank=selected_lender_rank, fallback=fallback) or fallback
            insert_user_action(conn, user_id=user_id, raw_message_id=inbound_raw_message_id,
                               action_type="lender_option_feedback",
                               lender=str(selected_lender_option.get("lender") or ""),
                               details={"message": text, "feedback_kind": selected_lender_feedback_kind(text)})

        elif contacted_cmd is not None:
            lender = (contacted_cmd or "").strip()
            if not lender:
                reply = "Reply: CONTACTED <lender name>"
            else:
                insert_user_action(conn, user_id=user_id, raw_message_id=inbound_raw_message_id,
                                   action_type="contacted", lender=lender, details={"district": district})
                save_selected_lender(conn, user_id=user_id, option=None, rank=None)
                reply = f"Thanks, I've noted that you contacted {lender}."

        elif switched_cmd is not None:
            from_lender, to_lender = switched_cmd
            if not (to_lender or "").strip():
                reply = "Reply: SWITCHED <new lender> (or SWITCHED FROM <old> TO <new>)"
            else:
                insert_user_action(conn, user_id=user_id, raw_message_id=inbound_raw_message_id,
                                   action_type="switched", lender=str(to_lender),
                                   details={"from_lender": from_lender, "to_lender": to_lender})
                conn.execute(
                    "INSERT INTO self_reported_switches(user_id, source_raw_message_id, from_lender, to_lender, notes) VALUES (?, ?, ?, ?, ?)",
                    (user_id, inbound_raw_message_id, from_lender, str(to_lender), "web_command"),
                )
                save_selected_lender(conn, user_id=user_id, option=None, rank=None)
                reply = f"Thanks, I've noted that you switched to {to_lender}."

        elif districts_q is not None:
            page_size = int(session.get("districts_page_size") or 30)
            sample, total = _districts_query(conn, prefix=districts_q, limit=page_size, offset=0)
            if sample:
                shown = len(sample)
                suffix = f" (showing {shown} of {total})" if total > shown else ""
                prefix_note = f" for \"{districts_q}\"" if (districts_q or "").strip() else ""
                more_note = "\n\nReply MORE for more." if total > shown else ""
                reply = f"Districts{prefix_note}{suffix}:\n" + ", ".join(sample) + "\n\nReply: DISTRICT <name>" + more_note
                save_district_paging(conn, user_id=user_id, prefix=(districts_q or "").strip() or None, offset=shown, page_size=page_size)
            else:
                reply = "No matching districts found. Try: DISTRICTS <prefix>" if total > 0 else "No districts loaded yet."
                clear_district_paging(conn, user_id=user_id)

        elif more_cmd:
            prefix = session.get("districts_prefix") or None
            offset = int(session.get("districts_offset") or 0)
            page_size = int(session.get("districts_page_size") or 30)
            if offset <= 0:
                reply = "Reply DISTRICTS to list districts first."
            else:
                sample, total = _districts_query(conn, prefix=prefix, limit=page_size, offset=offset)
                if not sample:
                    reply = "No more districts. Reply DISTRICTS to start again."
                    clear_district_paging(conn, user_id=user_id)
                else:
                    shown = len(sample)
                    next_offset = offset + shown
                    more_note = "\n\nReply MORE for more." if total > next_offset else ""
                    prefix_note = f" for \"{prefix}\"" if (prefix or "").strip() else ""
                    reply = f"Districts{prefix_note}:\n" + ", ".join(sample) + "\n\nReply: DISTRICT <name>" + more_note
                    save_district_paging(conn, user_id=user_id, prefix=prefix, offset=next_offset, page_size=page_size)

        else:
            # ── District setting / general handling ─────────────────────────
            if district_cmd is not None:
                clear_district_paging(conn, user_id=user_id)
                canonical = _canonical_district(conn, district_cmd)
                if canonical is None and _has_mfi_districts(conn):
                    sample = _districts_sample(conn)
                    reply = ("I couldn't match that district. Reply with an exact district name"
                             + (f" (examples: {', '.join(sample)})" if sample else "")
                             + ". Try: DISTRICTS <prefix>")
                else:
                    chosen = canonical or district_cmd.strip()
                    conn.execute("UPDATE users SET district = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chosen, user_id))
                    save_lender_options(conn, user_id=user_id, options=None)
                    save_selected_lender(conn, user_id=user_id, option=None, rank=None)
                    district = chosen
                    if not profile_step:
                        save_profile_step(conn, user_id=user_id, step="intro")
                        fallback = f"district set to {chosen}.\n\n" + profile_intro_message()
                    else:
                        fallback = f"district set to {chosen}.\n\nNow send your loan amount and time. Example: \"Need 5000 for 30 days with moneylender.\""
                    reply = ch.humanize(cfg, fallback=fallback, purpose="confirm district and invite next step") or fallback

            elif consent_status != "opted_in":
                reply = "To get nudges, reply START to opt in. Reply STOP to opt out."

            elif not district:
                clear_district_paging(conn, user_id=user_id)
                canonical = _canonical_district(conn, text)
                if canonical is None and _has_mfi_districts(conn):
                    sample = _districts_sample(conn)
                    reply = ("I couldn't match that district. Reply with an exact district name"
                             + (f" (examples: {', '.join(sample)})" if sample else "")
                             + ". Try: DISTRICTS <prefix>")
                else:
                    chosen = canonical or text.strip()
                    conn.execute("UPDATE users SET district = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chosen, user_id))
                    save_lender_options(conn, user_id=user_id, options=None)
                    save_selected_lender(conn, user_id=user_id, option=None, rank=None)
                    district = chosen
                    save_profile_step(conn, user_id=user_id, step="intro")
                    fallback = f"district set to {chosen}.\n\n" + profile_intro_message()
                    reply = ch.humanize(cfg, fallback=fallback, purpose="confirm district and start profile") or fallback

            else:
                # Has district, opted in — but no actionable command and not a loan message
                if not loan_after_commit:
                    if not _nudge_limits_ok(conn, user_id=user_id, cfg=cfg, now=now_dt):
                        if lender_options:
                            reply = (
                                "I’m not sure what you meant.\n\n"
                                f"If you meant to pick an option: {lender_option_prompt(len(lender_options))}\n"
                                "If you meant a new loan: send the amount, time (days/months), and the rate (APR or %/month) if you have it.\n\n"
                                "Reply STOP anytime to opt out."
                            )
                        else:
                            reply = (
                                "I’m not sure what you meant. If you’re asking about a loan, send the amount, time (days/months), and the rate "
                                "(APR or %/month) if you have it. Reply HELP for an example.\n\n"
                                "Reply STOP anytime to opt out."
                            )
                    elif loan_policy_enabled:
                        # Use policy engine to decide what to send
                        state = compute_user_state(db_path, user_id=user_id, now=now_dt)
                        decision = decide_policy(conn, state=state, cfg=cfg)
                        if decision.action != "wait" and decision.nudge_type:
                            conn.execute(
                                "INSERT INTO nudges(user_id, nudge_type, content, policy_name, policy_version, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
                                (user_id, decision.nudge_type, decision.content, decision.policy_name, decision.policy_version, fmt_ts(now_dt)),
                            )
                            save_lender_options(conn, user_id=user_id, options=None)
                            reply = decision.content
                        else:
                            reply = decision.content
                    else:
                        # Safe-default: show top lender options without policy engine
                        options = recommended_lender_rows(conn, district=district, n=3)
                        options = enrich_lender_options(options, amount_inr=None, tenure_days=None, current_rate=None)
                        fallback_content = suggest_lender_message(conn, district=district, n=3)
                        content = ch.recommendation_message(
                            cfg, fallback=fallback_content, district=district, options=options,
                            amount_inr=None, tenure_days=None, current_rate=None
                        ) or fallback_content
                        save_lender_options(conn, user_id=user_id, options=options)
                        save_selected_lender(conn, user_id=user_id, option=None, rank=None)
                        conn.execute(
                            "INSERT INTO nudges(user_id, nudge_type, content, policy_name, policy_version, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (user_id, "suggest_lender", content, "safe-default", "v1", fmt_ts(now_dt)),
                        )
                        reply = content

        # ── Store outbound reply ────────────────────────────────────────────
        if reply is not None:
            conn.execute(
                "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json) VALUES (?, 'outbound', ?, ?, ?, ?, ?)",
                (user_id, policy_channel, inbound.to_addr or "", inbound.from_addr, reply,
                 json.dumps({"generated_at": fmt_ts(now_dt)}, ensure_ascii=False)),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # ── Loan processing (after main transaction) ────────────────────────────
    if not (loan_after_commit and loan_user_id is not None and loan_raw_message_id is not None):
        return reply or ""

    reply = _process_loan(
        cfg=cfg, db_path=db_path, now_dt=now_dt,
        user_id=loan_user_id, raw_message_id=loan_raw_message_id,
        text=loan_text, district=loan_district, correction=correction,
        selected_lender_option=selected_lender_option, selected_lender_rank=selected_lender_rank,
        selected_lender_context_update=selected_lender_context_update,
        loan_policy_enabled=loan_policy_enabled, assistant_rec_enabled=assistant_rec_enabled,
        policy_channel=policy_channel, inbound=inbound,
    ) or reply or ""
    return reply


def _process_loan(
    cfg: Config, *, db_path: str, now_dt: datetime,
    user_id: int, raw_message_id: int, text: str, district: str | None,
    correction: tuple[str, str] | None,
    selected_lender_option: dict[str, Any] | None, selected_lender_rank: int | None,
    selected_lender_context_update: bool,
    loan_policy_enabled: bool, assistant_rec_enabled: bool,
    policy_channel: str, inbound: InboundMessage,
) -> str | None:
    """Handle loan parsing and policy decision in a separate transaction."""
    reply: str | None = None
    persist_payload: dict[str, Any] | None = None
    persist_raw_message_id: int | None = None
    persist_model: str | None = None
    clear_draft = False
    needs_llm_parse = False
    needs_policy = False
    needs_assistant_rec = False
    selected_refresh = False
    reply_prefix = ""

    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        session = load_user_session(conn, user_id=user_id)
        borrow_source_id = int(session["borrow_source_raw_message_id"]) if session.get("borrow_source_raw_message_id") else None
        borrow_model = str(session["borrow_model"]) if session.get("borrow_model") else None
        borrow_draft: dict[str, Any] | None = None
        if session.get("borrow_draft_json"):
            try:
                obj = json.loads(str(session["borrow_draft_json"]))
                borrow_draft = dict(obj) if isinstance(obj, dict) else None
            except Exception:
                pass

        if correction is not None:
            field, value_text = correction
            base = borrow_draft or load_latest_borrow_payload(conn, user_id=user_id)
            if base is None:
                reply = "I don't have any recent loan details to correct. Send your loan terms first."
            else:
                next_payload = apply_correction(base, field=field, value_text=value_text)
                if next_payload is None:
                    reply = "Sorry — I couldn't understand that correction. Example: CORRECT rate=5% monthly"
                else:
                    missing = missing_borrow_fields(next_payload)
                    if missing:
                        save_borrow_draft(conn, user_id=user_id, payload=next_payload,
                                          source_raw_message_id=borrow_source_id, model=borrow_model)
                        reply = clarifying_question(missing[0])
                    else:
                        persist_payload = next_payload
                        persist_raw_message_id = borrow_source_id or raw_message_id
                        persist_model = borrow_model
                        clear_draft = True
                        needs_policy = bool(loan_policy_enabled)
                        needs_assistant_rec = bool(assistant_rec_enabled and not loan_policy_enabled)
                        selected_refresh = selected_lender_option is not None
                        reply_prefix = "Updated. "

        elif borrow_draft is not None:
            try:
                draft_v = validate_borrow_intent_payload(borrow_draft)
            except Exception:
                draft_v = None
            if draft_v is None:
                save_borrow_draft(conn, user_id=user_id, payload=None, source_raw_message_id=None, model=None)
                reply = "Sorry — I lost track of the loan details. Please send the loan amount and tenure again."
            else:
                updated = dict(draft_v)
                for field, fn in [("amount_inr", lambda t: parse_amount_inr_safe(t)), ("tenure_days", lambda t: parse_tenure_days_safe(t)), ("interest_rate_apr", lambda t: parse_rate_safe(t))]:
                    if updated.get(field) is None:
                        val = fn(text)
                        if val is not None:
                            updated[field] = val
                try:
                    updated_v = validate_borrow_intent_payload(updated)
                except Exception:
                    updated_v = None
                if updated_v is None:
                    reply = "Sorry — I couldn't understand that. Please reply with the missing loan detail."
                else:
                    try:
                        updated_v = apply_text_heuristics(updated_v, text=text)
                    except Exception:
                        pass
                    missing = missing_borrow_fields(updated_v)
                    if missing:
                        save_borrow_draft(conn, user_id=user_id, payload=updated_v,
                                          source_raw_message_id=borrow_source_id, model=borrow_model)
                        reply = clarifying_question(missing[0])
                    else:
                        persist_payload = updated_v
                        persist_raw_message_id = borrow_source_id or raw_message_id
                        persist_model = borrow_model
                        clear_draft = True
                        needs_policy = bool(loan_policy_enabled)
                        needs_assistant_rec = bool(assistant_rec_enabled and not loan_policy_enabled)
                        selected_refresh = selected_lender_context_update

        elif selected_lender_context_update and selected_lender_option is not None:
            base = load_latest_borrow_payload(conn, user_id=user_id) or empty_borrow_payload()
            # Only merge amount and tenure — do NOT parse rate from a cost-update message
            # (e.g. "5 lakh for 30 days" would otherwise misparse "5/days" as 1825% APR)
            from .parsers import parse_amount_inr as _pamt, parse_tenure_days as _pten
            updated = dict(base)
            changed = False
            amt = _pamt(text)
            if amt is not None:
                updated["amount_inr"] = float(amt)
                changed = True
            ten = _pten(text)
            if ten is not None:
                updated["tenure_days"] = int(ten)
                changed = True
            if not changed:
                reply = "Send the loan amount and time like: 5000 for 30 days."
            else:
                try:
                    from ..nlp import validate_borrow_intent_payload as _vp
                    updated = _vp(updated)
                except Exception:
                    pass
                missing = missing_borrow_fields(updated)
                if missing:
                    save_borrow_draft(conn, user_id=user_id, payload=updated,
                                      source_raw_message_id=raw_message_id, model="selected-option-fragment")
                    reply = clarifying_question(missing[0])
                else:
                    persist_payload = updated
                    persist_raw_message_id = raw_message_id
                    persist_model = "selected-option-fragment"
                    needs_policy = bool(loan_policy_enabled)
                    needs_assistant_rec = bool(assistant_rec_enabled and not loan_policy_enabled)
                    selected_refresh = True

        else:
            needs_llm_parse = True

        if needs_llm_parse:
            conn.commit()
            conn.close()
            parse_result = parse_borrow_intent_with_llm(cfg, text=text, call_json=_call_json_adapter(cfg))
            conn = connect(db_path)
            conn.execute("BEGIN IMMEDIATE")
            session2 = load_user_session(conn, user_id=user_id)
            borrow_source_id2 = int(session2["borrow_source_raw_message_id"]) if session2.get("borrow_source_raw_message_id") else None

            if parse_result is None or not parse_result.payload.get("intent"):
                if parse_result is None:
                    save_borrow_draft(conn, user_id=user_id, payload=empty_borrow_payload(),
                                      source_raw_message_id=raw_message_id, model="fallback")
                reply = reply or ("I see you're thinking about a loan. Send the loan amount and time. "
                                  "Example: \"Need 5000 for 30 days at 5% monthly.\"")
            else:
                try:
                    parsed = apply_text_heuristics(parse_result.payload, text=text)
                except Exception:
                    parsed = parse_result.payload
                missing = missing_borrow_fields(parsed)
                if missing:
                    save_borrow_draft(conn, user_id=user_id, payload=parsed,
                                      source_raw_message_id=raw_message_id, model=parse_result.model)
                    reply = clarifying_question(missing[0])
                else:
                    persist_payload = parsed
                    persist_raw_message_id = raw_message_id
                    persist_model = parse_result.model
                    clear_draft = True
                    needs_policy = bool(loan_policy_enabled)
                    needs_assistant_rec = bool(assistant_rec_enabled and not loan_policy_enabled)

        # Store outbound reply for non-persist case
        if reply is not None and persist_payload is None:
            conn.execute(
                "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json) VALUES (?, 'outbound', ?, ?, ?, ?, ?)",
                (user_id, policy_channel, inbound.to_addr or "", inbound.from_addr, reply,
                 json.dumps({"generated_at": fmt_ts(now_dt)}, ensure_ascii=False)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # persist_borrow_intent_event opens its own connection — call AFTER the above transaction closes
    if persist_payload is not None and district is not None:
        event_id = persist_borrow_intent_event(
            db_path, user_id=user_id, raw_message_id=persist_raw_message_id,
            payload=persist_payload, model=persist_model,
        )

        conn2 = connect(db_path)
        try:
            conn2.execute("BEGIN IMMEDIATE")
            if clear_draft:
                save_borrow_draft(conn2, user_id=user_id, payload=None, source_raw_message_id=None, model=None)

            if needs_policy or needs_assistant_rec:
                state = compute_user_state(db_path, user_id=user_id, now=now_dt)

                if needs_assistant_rec and not needs_policy:
                    amount_inr = persist_payload.get("amount_inr")
                    tenure_days = persist_payload.get("tenure_days")
                    current_rate = persist_payload.get("interest_rate_apr")

                    if selected_refresh and selected_lender_option is not None:
                        # Show updated detail for the already-selected lender with new cost breakdown
                        updated_option = dict(selected_lender_option)
                        if amount_inr is not None:
                            updated_option["amount_inr"] = float(amount_inr)
                        if tenure_days is not None:
                            updated_option["tenure_days"] = int(tenure_days)
                        save_selected_lender(conn2, user_id=user_id, option=updated_option, rank=selected_lender_rank)
                        reply = reply_prefix + (
                            ch.lender_detail(cfg, option=updated_option, rank=selected_lender_rank or 1, district=district)
                            or lender_detail_fallback(option=updated_option, rank=selected_lender_rank or 1, district=district)
                        )
                    else:
                        options = recommended_lender_rows(conn2, district=district, n=3)
                        enriched = enrich_lender_options(options, amount_inr=amount_inr, tenure_days=tenure_days, current_rate=current_rate)
                        fallback_content = suggest_lender_message(
                            conn2, district=district, current_rate=current_rate,
                            amount_inr=amount_inr, tenure_days=tenure_days, n=3,
                        )
                        content = ch.recommendation_message(
                            cfg, fallback=fallback_content, district=district, options=enriched,
                            amount_inr=amount_inr, tenure_days=tenure_days, current_rate=current_rate,
                        ) or fallback_content
                        save_lender_options(conn2, user_id=user_id, options=enriched)
                        reply = reply_prefix + content

                elif needs_policy:
                    decision = decide_policy(conn2, state=state, cfg=cfg)
                    if decision.action != "wait" and decision.nudge_type:
                        conn2.execute(
                            "INSERT INTO nudges(user_id, parsed_event_id, nudge_type, content, policy_name, policy_version, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (user_id, event_id, decision.nudge_type, decision.content,
                             decision.policy_name, decision.policy_version, fmt_ts(now_dt)),
                        )
                        # Save lender options for follow-up selection (1/2/3 replies)
                        if decision.action in {"suggest_lender", "alert"}:
                            amount_inr = persist_payload.get("amount_inr")
                            tenure_days = persist_payload.get("tenure_days")
                            current_rate = persist_payload.get("interest_rate_apr")
                            lender_rows = recommended_lender_rows(conn2, district=district, n=3)
                            enriched = enrich_lender_options(lender_rows, amount_inr=amount_inr, tenure_days=tenure_days, current_rate=current_rate)
                            save_lender_options(conn2, user_id=user_id, options=enriched)
                        else:
                            save_lender_options(conn2, user_id=user_id, options=None)
                        reply = reply_prefix + decision.content
                    else:
                        reply = reply_prefix + (decision.content or "Thanks — noted.")

            if reply is not None:
                conn2.execute(
                    "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json) VALUES (?, 'outbound', ?, ?, ?, ?, ?)",
                    (user_id, policy_channel, inbound.to_addr or "", inbound.from_addr, reply,
                     json.dumps({"generated_at": fmt_ts(now_dt)}, ensure_ascii=False)),
                )
            conn2.commit()
        except Exception:
            conn2.rollback()
            raise
        finally:
            conn2.close()

    return reply


def _call_json_adapter(cfg: Config):
    # Use the bot package's reference so tests can monkey-patch it
    import nudge_webhook.bot as _bot_pkg
    def _adapter(c, system_prompt, user_prompt):
        return _bot_pkg.call_json_with_retries(c, system_prompt, user_prompt)
    return _adapter


def parse_amount_inr_safe(text: str):
    from .parsers import parse_amount_inr
    return parse_amount_inr(text)

def parse_tenure_days_safe(text: str):
    from .parsers import parse_tenure_days
    return parse_tenure_days(text)

def parse_rate_safe(text: str):
    from .parsers import parse_interest_rate_apr
    return parse_interest_rate_apr(text)
