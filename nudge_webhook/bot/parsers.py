"""Text parsing — extract commands, amounts, tenures, rates from user messages."""
from __future__ import annotations

import re

from .helpers import strip_prefix


def extract_district_command(text: str) -> str | None:
    raw = text.strip()
    lower = raw.lower()
    for prefix in ("district", "set district", "change district"):
        if lower.startswith(prefix):
            after = lower[len(prefix):]
            if after and after[:1].isalpha():
                continue
            remaining = strip_prefix(raw, prefix)
            return remaining or None
    return None


def extract_districts_query(text: str) -> str | None:
    raw = text.strip()
    lower = raw.lower()
    if not lower.startswith("districts"):
        return None
    after = lower[len("districts"):]
    if after and after[:1].isalpha():
        return None
    return strip_prefix(raw, "districts")


def is_more_command(text: str) -> bool:
    lower = text.strip().lower()
    return lower in {"more", "districts/more"}


def parse_contacted(text: str) -> str | None:
    raw = text.strip()
    lower = raw.lower()
    if lower == "contacted":
        return ""
    if lower.startswith("contacted"):
        return strip_prefix(raw, "contacted").strip()
    return None


def parse_switched(text: str) -> tuple[str | None, str | None] | None:
    raw = text.strip()
    lower = raw.lower()
    if lower == "switched":
        return ("", None)
    if not lower.startswith("switched"):
        return None
    remaining = strip_prefix(raw, "switched").strip()
    if not remaining:
        return ("", None)
    low = remaining.lower()
    if low.startswith("from "):
        parts = remaining[5:].split(" to ", 1)
        if len(parts) == 2:
            return (parts[0].strip() or None, parts[1].strip() or None)
    return (None, remaining.strip() or None)


def extract_correction(text: str) -> tuple[str, str] | None:
    raw = text.strip()
    lower = raw.lower()
    prefix_used = None
    for p in ("correct", "correction", "fix"):
        if lower.startswith(p):
            after = lower[len(p):]
            if after and after[:1].isalpha():
                continue
            prefix_used = p
            break
    if prefix_used is None:
        return None
    rest = strip_prefix(raw, prefix_used)
    if not rest:
        return None
    if "=" in rest:
        field_raw, value_raw = rest.split("=", 1)
    else:
        parts = rest.split(None, 1)
        if len(parts) < 2:
            return None
        field_raw, value_raw = parts[0], parts[1]
    aliases = {
        "intent": "intent", "amount": "amount_inr", "amt": "amount_inr",
        "principal": "amount_inr", "tenure": "tenure_days", "duration": "tenure_days",
        "days": "tenure_days", "rate": "interest_rate_apr", "interest": "interest_rate_apr",
        "apr": "interest_rate_apr", "stage": "negotiation_stage",
        "lender": "lender_name", "lender_name": "lender_name", "lender_type": "lender_type",
    }
    mapped = aliases.get(field_raw.strip().lower().replace("-", "_"))
    return (mapped, value_raw.strip()) if mapped else None


def parse_update_profile_command(text: str) -> bool:
    return text.strip().lower() in {"update profile", "updateprofile", "reset profile", "change profile"}


def parse_amount_inr(text: str) -> float | None:
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
    return float(val) if val > 0 else None


def parse_tenure_days(text: str) -> int | None:
    raw = (text or "").strip().lower()
    for pattern, mult in [
        (r"(\d+)\s*(day|days|d)\b", 1),
        (r"(\d+)\s*(week|weeks|w)\b", 7),
        (r"(\d+)\s*(month|months|m)\b", 30),
    ]:
        m = re.search(pattern, raw)
        if m:
            return max(1, int(m.group(1)) * mult)
    m = re.search(r"\b(\d+)\b", raw)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 3650:
            return val
    return None


def parse_interest_rate_apr(text: str) -> float | None:
    raw = (text or "").strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", raw)
    if not m:
        return None
    rate = float(m.group(1))
    if rate <= 0:
        return None
    if any(w in raw for w in ("apr", "annual", "year", "yearly")):
        return float(rate)
    if any(w in raw for w in ("month", "monthly")):
        return float(rate) * 12.0
    if any(w in raw for w in ("week", "weekly")):
        return float(rate) * 52.0
    if any(w in raw for w in ("day", "daily")):
        return float(rate) * 365.0
    return None


def infer_lender_type(text: str) -> str | None:
    raw = (text or "").strip().lower()
    if not raw:
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


def looks_like_new_loan_message(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    has_loan_word = any(w in raw for w in ("loan", "borrow", "need", "lend", "credit"))
    return has_loan_word and (parse_amount_inr(raw) is not None or parse_tenure_days(raw) is not None)


def looks_like_loan_intent_message(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw or any(k in raw for k in ("district", "districts", "lang", "stop", "start", "help", "more")):
        return False
    has_borrow_intent = any(w in raw for w in ("loan", "borrow", "need", "lend", "credit"))
    has_lender_cue = any(w in raw for w in ("moneylender", "money lender", "microfinance", "mfi", "nbfc", "bank"))
    return bool(has_borrow_intent or (has_lender_cue and ("need" in raw or "borrow" in raw)))


def looks_like_loan_terms_fragment(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw or not re.search(r"\d", raw):
        return False
    has_amount = re.search(r"(₹|rs\.?|rupees?|lakh|lakhs|lac|lacs|\bk\b|\b\d{4,}\b)", raw) is not None
    has_tenure = re.search(r"\b\d+\s*(day|days|d|week|weeks|w|month|months|m)\b", raw) is not None
    has_rate = re.search(r"\b\d+(?:\.\d+)?\s*%?\s*(apr|annual|year|yearly|month|monthly|week|weekly|day|daily)\b", raw) is not None
    has_loan = any(w in raw for w in ("loan", "borrow", "need", "lend", "credit"))
    return bool(has_rate or has_tenure or (has_amount and (has_loan or has_tenure or " for " in raw)))
