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
    delta = now.astimezone(timezone.utc) - ts.astimezone(timezone.utc)
    return delta <= timedelta(days=int(days))


def decide_baseline(conn, *, state: UserState) -> PolicyDecision:
    district = state.district
    now = state.now

    if state.consent_status != "opted_in":
        return PolicyDecision(
            action="wait",
            nudge_type=None,
            content="To get nudges, reply START to opt in. Reply STOP to opt out.",
            policy_name="baseline-threshold",
            policy_version="v1",
        )

    if not district:
        return PolicyDecision(
            action="wait",
            nudge_type=None,
            content="To personalise suggestions, what district are you in? Reply DISTRICT <name>. Reply STOP to opt out.",
            policy_name="baseline-threshold",
            policy_version="v1",
        )

    last_intent_at = state.borrow.last_intent_at
    stage = state.borrow.last_stage or "none"
    implied_apr = state.implied_apr

    if not _recent(now, last_intent_at, days=14):
        return PolicyDecision(
            action="wait",
            nudge_type=None,
            content=(
                "Thanks — I’ve got your message. If you’re discussing a loan, share the rate/tenure and I can suggest regulated options. "
                "Reply STOP to opt out."
            ),
            policy_name="baseline-threshold",
            policy_version="v1",
        )

    if implied_apr is not None and implied_apr >= 60.0 and stage in {"asking", "offered", "agreed", "borrowed"}:
        return PolicyDecision(
            action="alert",
            nudge_type="alert",
            content=alert_message(
                conn,
                district=district,
                quoted_apr=float(implied_apr),
                current_lender=None,
                amount_inr=state.borrow.amount_inr,
                tenure_days=state.borrow.tenure_days,
                n=3,
            ),
            policy_name="baseline-threshold",
            policy_version="v1",
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    if implied_apr is not None and implied_apr >= 40.0 and stage in {"considering", "asking", "offered", "agreed"}:
        return PolicyDecision(
            action="suggest_lender",
            nudge_type="suggest_lender",
            content=suggest_lender_message(
                conn,
                district=district,
                current_rate=float(implied_apr),
                exclude_lender=None,
                amount_inr=state.borrow.amount_inr,
                tenure_days=state.borrow.tenure_days,
                n=3,
            ),
            policy_name="baseline-threshold",
            policy_version="v1",
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    if stage == "borrowed" and state.days_since_borrow is not None and state.days_since_borrow <= 7.0:
        return PolicyDecision(
            action="education",
            nudge_type="education",
            content=education_message(district=district),
            policy_name="baseline-threshold",
            policy_version="v1",
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    return PolicyDecision(
        action="wait",
        nudge_type=None,
        content="Thanks — I’ve got your message. I’ll keep nudges low-frequency. Reply STOP to opt out.",
        policy_name="baseline-threshold",
        policy_version="v1",
    )
