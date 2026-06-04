"""Nudge policy — decides when and what to send proactively."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .nudge_content import alert_message, education_message, loan_cost_breakdown, suggest_lender_message
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

def _fmt_inr(amount_inr: float) -> str:
    amt = int(round(float(amount_inr)))
    return f"₹{amt:,}"


def decide_policy(conn, *, state: UserState, cfg) -> PolicyDecision:
    """Evaluate the baseline policy and return a decision."""
    district = state.district
    now = state.now

    if state.consent_status != "opted_in":
        return PolicyDecision(
            action="wait", nudge_type=None,
            content="If you want help with a loan, tell me your district and the amount/time/rate you were offered. Say STOP to pause.",
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
                "I can then calculate payments and suggest regulated options. Say STOP to pause."
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

    if (
        implied_apr is not None
        and state.borrow.amount_inr is not None
        and state.borrow.tenure_days is not None
        and implied_apr < 40.0
    ):
        b = loan_cost_breakdown(float(state.borrow.amount_inr), int(state.borrow.tenure_days), float(implied_apr))
        months = int(b["months"]) if b.get("months") is not None else max(1, int((int(state.borrow.tenure_days) + 29) / 30))
        total = float(b["total_repayment"]) if b.get("total_repayment") is not None else float(state.borrow.amount_inr)
        monthly = float(b["monthly_payment"]) if b.get("monthly_payment") is not None else total / float(max(1, months))
        interest = float(b["tenure_interest"]) if b.get("tenure_interest") is not None else max(0.0, total - float(state.borrow.amount_inr))
        return PolicyDecision(
            action="wait", nudge_type=None,
            content=(
                f"Got it. At {float(implied_apr):g}% APR on {_fmt_inr(float(state.borrow.amount_inr))} for {int(state.borrow.tenure_days)} days, "
                f"you’d repay about {_fmt_inr(total)} total (about {_fmt_inr(monthly)}/month), assuming simple interest and no extra fees.\n\n"
                f"That’s about {_fmt_inr(interest)} in interest over the whole period. If you want, I can still show a few regulated lenders in {district} to compare."
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
        content="Tell me what you want to do next: share another loan offer to compare, or ask for regulated options in your district. Say STOP to pause.",
        policy_name="baseline-threshold", policy_version="v1",
    )
