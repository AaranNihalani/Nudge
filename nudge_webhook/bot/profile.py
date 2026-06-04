"""Profile collection flow and AIDIS-informed credit access assessment.

All statistical figures come directly from Table 5 Model 5 (Logit + District FE)
of: Nihalani, A. (2025). Understanding Financial Inclusion: Patterns and
Determinants of Formal Borrowing in India. SSRN 6006354.

Only characteristics the user has provided are reported — no figures are
combined or extrapolated beyond what the paper supports.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# AIDIS 2019 Average Marginal Effects — Table 5, Model 5 (Logit + District FE)
# Source: Nihalani (2025), SSRN 6006354
# Units: percentage points change in probability of formal borrowing
# ---------------------------------------------------------------------------
_AME: dict[str, float] = {
    "mpce_per_1000_inr":    1.13,   # pp per INR 1,000 above mean
    "household_size":      -0.59,   # pp per additional person above mean
    "land_acres":           2.52,   # pp per acre
    "urban":                2.26,   # pp vs rural
    "caste_sc":            -6.24,   # pp vs OBC
    "caste_st":            -3.69,   # pp vs OBC
    "caste_others":         6.16,   # pp vs OBC
    "religion_muslim":    -10.67,   # pp vs Hindu
    "religion_christian":  -3.23,   # pp vs Hindu
    "religion_others":      2.67,   # pp vs Hindu
}

# AIDIS 2019 sample means (Table 2)
_MEAN_MPCE_INR = 13_571.0   # INR/month
_MEAN_HH_SIZE = 5.5          # people

_PAPER_URL = "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6006354"
_CITATION = f"Nihalani (2025/2026), IJHSR — {_PAPER_URL}"

# Profile collection steps in order
_PROFILE_STEPS = ["intro", "caste", "religion", "mpce", "household_size", "land", "urban", "done"]


# ---------------------------------------------------------------------------
# Profile step management
# ---------------------------------------------------------------------------

def profile_intro_message() -> str:
    return (
        "Before I show you options, I have 6 quick questions about your household. "
        "This helps me give you more relevant guidance based on national research data.\n\n"
        "Your answers are saved — I won't ask again.\n\n"
        "Reply YES to continue, or SKIP to go straight to loan help."
    )


def profile_question(step: str) -> str | None:
    questions = {
        "caste": "What is your caste group?\nReply: OBC / SC / ST / OTHER / DON'T KNOW",
        "religion": "What is your religion?\nReply: HINDU / MUSLIM / CHRISTIAN / OTHER / DON'T KNOW",
        "mpce": "Roughly, what does your household spend per person per month (in ₹)?\nExample: 8000\nReply DON'T KNOW if unsure.",
        "household_size": "How many people live in your household?\nExample: 5\nReply DON'T KNOW if unsure.",
        "land": "Do you own any land? If yes, approximately how many acres?\nReply 0 if none, or DON'T KNOW.",
        "urban": "Are you in an urban or rural area?\nReply: URBAN / RURAL / DON'T KNOW",
    }
    return questions.get(step)


def next_step(current: str) -> str:
    idx = _PROFILE_STEPS.index(current) if current in _PROFILE_STEPS else -1
    return _PROFILE_STEPS[idx + 1] if idx + 1 < len(_PROFILE_STEPS) else "done"


def is_skip(text: str) -> bool:
    return text.strip().lower() in {"skip", "s", "don't know", "dont know", "dk", "?", "idk", "no idea"}


def parse_profile_answer(step: str, text: str) -> tuple[str, Any]:
    """Parse the user's answer for a given profile step.
    Returns (column_name, value) where value=None means skip/unknown.
    """
    t = text.strip().lower()
    if is_skip(t):
        return (step, None)

    if step == "caste":
        mapping = {"obc": "obc", "sc": "sc", "st": "st", "other": "others", "others": "others",
                   "general": "others", "forward": "others"}
        return ("caste", mapping.get(t))

    if step == "religion":
        mapping = {"hindu": "hindu", "hinduism": "hindu", "muslim": "muslim", "islam": "muslim",
                   "christian": "christian", "christianity": "christian", "other": "others", "others": "others"}
        return ("religion", mapping.get(t))

    if step == "mpce":
        try:
            val = float(t.replace(",", "").replace("₹", "").replace("rs", "").strip())
            return ("mpce_inr", val if val > 0 else None)
        except Exception:
            return ("mpce_inr", None)

    if step == "household_size":
        try:
            val = int(float(t))
            return ("household_size", val if 1 <= val <= 50 else None)
        except Exception:
            return ("household_size", None)

    if step == "land":
        try:
            val = float(t.replace(",", "").replace("acres", "").strip())
            return ("land_acres", max(0.0, val))
        except Exception:
            return ("land_acres", None)

    if step == "urban":
        if any(w in t for w in ("urban", "city", "town", "metro")):
            return ("urban", 1)
        if any(w in t for w in ("rural", "village", "gram", "gaon")):
            return ("urban", 0)
        return ("urban", None)

    return (step, None)


def save_profile_field(conn, *, user_id: int, column: str, value: Any) -> None:
    safe_columns = {"caste", "religion", "mpce_inr", "household_size", "land_acres", "urban"}
    if column not in safe_columns or value is None:
        return
    conn.execute(
        f"UPDATE users SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (value, int(user_id)),
    )


def mark_profile_complete(conn, *, user_id: int) -> None:
    conn.execute(
        "UPDATE users SET profile_complete = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (int(user_id),),
    )


# ---------------------------------------------------------------------------
# AIDIS credit access assessment (statistically valid, individual findings only)
# ---------------------------------------------------------------------------

def build_aidis_assessment(
    *,
    caste: str | None,
    religion: str | None,
    mpce_inr: float | None,
    household_size: int | None,
    land_acres: float | None,
    urban: int | None,
) -> str | None:
    """
    Returns a personalised credit access profile using only the AMEs directly
    from the paper. Each characteristic is reported separately with its direction
    and magnitude. An overall directional summary is included.
    No figures are summed or combined into a single score.
    """
    factors: list[tuple[str, float, str]] = []  # (description, ame_value, direction)

    if caste is not None:
        if caste == "sc":
            ame = _AME["caste_sc"]
            factors.append((
                f"Caste (SC vs OBC): −{abs(ame):.1f} pp — SC households are "
                f"{abs(ame):.1f} percentage points less likely to access formal credit than OBC households.",
                ame, "negative"
            ))
        elif caste == "st":
            ame = _AME["caste_st"]
            factors.append((
                f"Caste (ST vs OBC): −{abs(ame):.1f} pp — ST households are "
                f"{abs(ame):.1f} percentage points less likely to access formal credit than OBC households.",
                ame, "negative"
            ))
        elif caste == "others":
            ame = _AME["caste_others"]
            factors.append((
                f"Caste (Others vs OBC): +{ame:.1f} pp — households in the 'Others' caste category are "
                f"{ame:.1f} percentage points more likely to access formal credit than OBC households.",
                ame, "positive"
            ))
        elif caste == "obc":
            factors.append((
                "Caste (OBC): neutral reference group — OBC is the mid-point in the AIDIS caste distribution for formal credit access.",
                0.0, "neutral"
            ))

    if religion is not None:
        if religion == "muslim":
            ame = _AME["religion_muslim"]
            factors.append((
                f"Religion (Muslim vs Hindu): −{abs(ame):.1f} pp — the largest religion-linked gap in the study. "
                f"Muslim households are {abs(ame):.1f} percentage points less likely to access formal credit than Hindu households.",
                ame, "negative"
            ))
        elif religion == "christian":
            ame = _AME["religion_christian"]
            factors.append((
                f"Religion (Christian vs Hindu): −{abs(ame):.1f} pp — Christian households are "
                f"{abs(ame):.1f} percentage points less likely to access formal credit than Hindu households.",
                ame, "negative"
            ))
        elif religion == "others":
            ame = _AME["religion_others"]
            factors.append((
                f"Religion (Other vs Hindu): +{ame:.1f} pp — households in other religion categories are "
                f"{ame:.1f} percentage points more likely to access formal credit than Hindu households.",
                ame, "positive"
            ))
        elif religion == "hindu":
            factors.append((
                "Religion (Hindu): neutral reference group — Hindu is the baseline in the AIDIS religion analysis.",
                0.0, "neutral"
            ))

    if mpce_inr is not None:
        diff_thousands = (mpce_inr - _MEAN_MPCE_INR) / 1000.0
        income_effect = diff_thousands * _AME["mpce_per_1000_inr"]
        direction = "above" if diff_thousands >= 0 else "below"
        sign = "+" if income_effect >= 0 else "−"
        factors.append((
            f"Monthly income (₹{int(mpce_inr):,}/person): {sign}{abs(income_effect):.1f} pp vs the national average "
            f"(₹{int(_MEAN_MPCE_INR):,}/person). Each ₹1,000 {direction} average is linked to "
            f"{'+'}{_AME['mpce_per_1000_inr']:.2f} pp of formal credit likelihood.",
            income_effect, "positive" if income_effect >= 0 else "negative"
        ))

    if urban is not None:
        if urban == 1:
            ame = _AME["urban"]
            factors.append((
                f"Location (urban): +{ame:.1f} pp — urban residents are {ame:.1f} percentage points "
                "more likely to access formal credit than rural residents.",
                ame, "positive"
            ))
        else:
            ame = _AME["urban"]
            factors.append((
                f"Location (rural): −{ame:.1f} pp — rural residents are {ame:.1f} percentage points "
                "less likely to access formal credit than urban residents.",
                -ame, "negative"
            ))

    if land_acres is not None and land_acres > 0:
        effect = land_acres * _AME["land_acres"]
        factors.append((
            f"Land ({land_acres:g} acre{'s' if land_acres != 1 else ''}): +{effect:.1f} pp — "
            f"each acre of land is associated with +{_AME['land_acres']:.2f} pp of formal credit likelihood.",
            effect, "positive"
        ))

    if not factors:
        return None

    # Build per-factor bullet list
    bullet_lines = "\n".join(f"• {desc}" for desc, _, _ in factors)

    # Overall directional summary — qualitative only, no summing
    positives = [d for _, v, d in factors if d == "positive"]
    negatives = [d for _, v, d in factors if d == "negative"]
    if positives and negatives:
        summary = (
            "Overall, your profile is mixed — some factors increase your likelihood of formal credit access, "
            "others reduce it. These are independent estimates and cannot be combined into a single number."
        )
    elif positives:
        summary = (
            "Overall, the factors you've shared are associated with higher formal credit access than average. "
            "These are independent estimates — they reflect population-level patterns, not a personal score."
        )
    elif negatives:
        summary = (
            "Overall, the factors you've shared are associated with lower formal credit access than average. "
            "This makes finding regulated alternatives even more important — that's exactly what Nudge is here for."
        )
    else:
        summary = (
            "Your profile sits close to the national reference point for formal credit access."
        )

    return (
        f"We've analysed your profile using research on ~480,000 Indian loans "
        f"(Nihalani, 2025 — {_PAPER_URL}).\n\n"
        f"Here's how each factor affects your likelihood of formal credit access "
        f"(pp = percentage points, vs a reference group):\n\n"
        f"{bullet_lines}\n\n"
        f"{summary}\n\n"
        f"These are population-level patterns, not a personal credit score."
    )
