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
_CITATION = f"AIDIS 2019, ~480,000 loans, Logit with district fixed effects — Nihalani (2025): {_PAPER_URL}"

# Profile collection steps in order
_PROFILE_STEPS = ["intro", "caste", "religion", "mpce", "household_size", "land", "urban", "done"]


# ---------------------------------------------------------------------------
# Profile step management
# ---------------------------------------------------------------------------

def profile_intro_message() -> str:
    return (
        "Before I show you options, I have 5 quick questions about your household. "
        "This helps me give you more relevant guidance based on national research data.\n\n"
        "Your answers are stored to personalise future messages — I won't ask again.\n\n"
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
    Returns a personalised credit access profile message using only the AME
    directly from the paper for each characteristic the user provided.
    No figures are summed or extrapolated.
    """
    lines: list[str] = []

    if caste is not None:
        if caste == "sc":
            lines.append(
                f"SC households are {abs(_AME['caste_sc']):.1f} percentage points less likely to access "
                "formal credit than OBC households."
            )
        elif caste == "st":
            lines.append(
                f"ST households are {abs(_AME['caste_st']):.1f} percentage points less likely to access "
                "formal credit than OBC households."
            )
        elif caste == "others":
            lines.append(
                f"Households in the 'Others' caste category are {_AME['caste_others']:.1f} percentage "
                "points more likely to access formal credit than OBC households."
            )
        elif caste == "obc":
            lines.append(
                "OBC households are the reference group — among the two most likely caste categories "
                "to access formal credit in the AIDIS data."
            )

    if religion is not None:
        if religion == "muslim":
            lines.append(
                f"Muslim households are {abs(_AME['religion_muslim']):.1f} percentage points less likely "
                "to access formal credit than Hindu households."
            )
        elif religion == "christian":
            lines.append(
                f"Christian households are {abs(_AME['religion_christian']):.1f} percentage points less "
                "likely to access formal credit than Hindu households."
            )
        elif religion == "others":
            lines.append(
                f"Households in other religion categories are {_AME['religion_others']:.1f} percentage "
                "points more likely to access formal credit than Hindu households."
            )

    if mpce_inr is not None:
        diff_thousands = (mpce_inr - _MEAN_MPCE_INR) / 1000.0
        direction = "above" if diff_thousands >= 0 else "below"
        lines.append(
            f"Your monthly spending per person (₹{int(mpce_inr):,}) is {direction} the AIDIS national "
            f"average (₹{int(_MEAN_MPCE_INR):,}). Each ₹1,000 more per person per month is associated "
            f"with +{_AME['mpce_per_1000_inr']:.2f} percentage points of formal credit likelihood."
        )

    if urban is not None:
        if urban == 1:
            lines.append(
                f"Urban residents are {_AME['urban']:.1f} percentage points more likely to access "
                "formal credit than rural residents."
            )
        else:
            lines.append(
                f"Rural residents face a {_AME['urban']:.1f} percentage point lower likelihood of "
                "formal credit access compared to urban residents."
            )

    if land_acres is not None and land_acres > 0:
        effect = land_acres * _AME["land_acres"]
        lines.append(
            f"Land ownership of {land_acres:g} acre(s) is associated with a "
            f"+{effect:.1f} percentage point increase in formal credit likelihood "
            f"({_AME['land_acres']:.2f} pp per acre)."
        )

    if not lines:
        return None

    header = f"Your credit access profile ({_CITATION}):\n\n"
    body = "\n".join(f"• {l}" for l in lines)
    footer = "\n\nThese are population-level averages, not a personal credit score. Source: Nihalani (2025)."
    return header + body + footer
