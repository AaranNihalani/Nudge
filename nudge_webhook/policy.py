"""Nudge policy — decides when and what to send proactively."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .nudge_content import alert_message, education_message, suggest_lender_message
from .state import UserState


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    nudge_type: str | None
    content: str
    policy_name: str
    policy_version: str
    parsed_event_id: int | None = None


def _recent(now: datetime, ts: datetime | None, *, days: int) -> bool:
    if ts is None:
        return False
    return (now.astimezone(timezone.utc) - ts.astimezone(timezone.utc)) <= timedelta(days=days)


def decide_policy(conn, *, state: UserState, cfg) -> PolicyDecision:
    """Evaluate the baseline policy and return a decision."""
    district = state.district
    now = state.now

    if state.consent_status != "opted_in":
        return PolicyDecision(
            action="wait", nudge_type=None,
            content="If you want help with a loan, tell me your district and the amount/time/rate you were offered. Reply STOP to pause.",
            policy_name="baseline-threshold", policy_version="v1",
        )

    if not district:
        return PolicyDecision(
            action="wait", nudge_type=None,
            content="To personalise suggestions, what district are you in? Reply DISTRICT <name>.",
            policy_name="baseline-threshold", policy_version="v1",
        )

    if not _recent(now, state.borrow.last_intent_at, days=14):
        return PolicyDecision(
            action="wait", nudge_type=None,
            content=(
                "If you're asking about a loan, send the amount, time (days/months), and the rate (APR or %/month) if you have it. "
                "I can then calculate payments and suggest regulated options. Reply STOP to pause."
            ),
            policy_name="baseline-threshold", policy_version="v1",
        )

    implied_apr = state.implied_apr
    stage = state.borrow.last_stage or "none"

    if implied_apr is not None and implied_apr >= 60.0 and stage in {"asking", "offered", "agreed", "borrowed"}:
        return PolicyDecision(
            action="alert", nudge_type="alert",
            content=alert_message(
                conn, district=district, quoted_apr=float(implied_apr),
                amount_inr=state.borrow.amount_inr, tenure_days=state.borrow.tenure_days, n=3,
            ),
            policy_name="baseline-threshold", policy_version="v1",
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    if implied_apr is not None and implied_apr >= 40.0 and stage in {"considering", "asking", "offered", "agreed"}:
        return PolicyDecision(
            action="suggest_lender", nudge_type="suggest_lender",
            content=suggest_lender_message(
                conn, district=district, current_rate=float(implied_apr),
                amount_inr=state.borrow.amount_inr, tenure_days=state.borrow.tenure_days, n=3,
            ),
            policy_name="baseline-threshold", policy_version="v1",
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    if implied_apr is None and state.borrow.amount_inr is not None and state.borrow.tenure_days is not None:
        return PolicyDecision(
            action="suggest_lender", nudge_type="suggest_lender",
            content=suggest_lender_message(
                conn, district=district, amount_inr=state.borrow.amount_inr,
                tenure_days=state.borrow.tenure_days, n=3,
            ),
            policy_name="baseline-threshold", policy_version="v1",
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    if stage == "borrowed" and state.days_since_borrow is not None and state.days_since_borrow <= 7.0:
        return PolicyDecision(
            action="education", nudge_type="education",
            content=education_message(district=district),
            policy_name="baseline-threshold", policy_version="v1",
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    return PolicyDecision(
        action="wait", nudge_type=None,
        content="If you want help with a loan, send the amount, time (days/months), and rate (APR or %/month). Reply STOP to pause.",
        policy_name="baseline-threshold", policy_version="v1",
    )
