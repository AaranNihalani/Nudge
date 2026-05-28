from __future__ import annotations

from typing import Any
import math
import json
import os


def _apr_to_monthly_percent(apr_percent: float) -> float:
    return float(apr_percent) / 12.0


def _format_percent(x: float, *, decimals: int = 1) -> str:
    if decimals <= 0:
        return f"{x:.0f}"
    return f"{x:.{int(decimals)}f}".rstrip("0").rstrip(".")


def _format_inr(amount_inr: float) -> str:
    amt = int(round(float(amount_inr)))
    return f"INR {amt:,}"


def _simple_interest_estimate(amount_inr: float, tenure_days: int, apr_percent: float) -> tuple[float, float]:
    ratio = (float(apr_percent) / 100.0) * (float(tenure_days) / 365.0)
    interest = max(0.0, float(amount_inr) * ratio)
    total = max(0.0, float(amount_inr) + interest)
    return interest, total


def _monthly_repayment_estimate(amount_inr: float, tenure_days: int, apr_percent: float) -> tuple[float, int, float]:
    _, total = _simple_interest_estimate(amount_inr, tenure_days, apr_percent)
    months = max(1, int(math.ceil(float(tenure_days) / 30.0)))
    monthly = max(0.0, total / float(months))
    return monthly, months, total


def _apr_cost_amounts(amount_inr: float, apr_percent: float) -> tuple[float, float]:
    annual_interest = max(0.0, float(amount_inr) * (float(apr_percent) / 100.0))
    monthly_interest = annual_interest / 12.0
    return annual_interest, monthly_interest


def loan_cost_breakdown(amount_inr: float, tenure_days: int, apr_percent: float) -> dict[str, float | int]:
    annual_interest, monthly_interest = _apr_cost_amounts(amount_inr, apr_percent)
    tenure_interest, total_repayment = _simple_interest_estimate(amount_inr, tenure_days, apr_percent)
    monthly_payment, months, _ = _monthly_repayment_estimate(amount_inr, tenure_days, apr_percent)
    return {
        "annual_interest": annual_interest,
        "monthly_interest": monthly_interest,
        "tenure_interest": tenure_interest,
        "total_repayment": total_repayment,
        "monthly_payment": monthly_payment,
        "months": months,
    }


_CONTACTS_CACHE: dict[str, dict[str, Any]] | None = None


def _norm_key(s: str) -> str:
    raw = (s or "").strip().lower()
    out = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append(" ")
    joined = "".join(out)
    joined = " ".join([p for p in joined.split() if p])
    return joined


def _load_contacts() -> dict[str, dict[str, Any]]:
    global _CONTACTS_CACHE
    if _CONTACTS_CACHE is not None:
        return _CONTACTS_CACHE
    try:
        here = os.path.dirname(__file__)
        path = os.path.join(here, "lender_contacts.json")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            _CONTACTS_CACHE = {}
            return _CONTACTS_CACHE
        normalized: dict[str, dict[str, Any]] = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            normalized[_norm_key(k)] = dict(v)
        _CONTACTS_CACHE = normalized
        return _CONTACTS_CACHE
    except Exception:
        _CONTACTS_CACHE = {}
        return _CONTACTS_CACHE


def lender_contact_block(lender_name: str) -> str:
    book = _load_contacts()
    key = _norm_key(lender_name)
    entry = book.get(key)
    if entry is None:
        return ""
    phones = entry.get("phone")
    emails = entry.get("email")
    website = entry.get("website")
    parts: list[str] = []
    if isinstance(phones, list):
        clean = [str(p).strip() for p in phones if str(p).strip()]
        if clean:
            parts.append("Phone: " + ", ".join(clean[:3]))
    if isinstance(emails, list):
        clean = [str(e).strip() for e in emails if str(e).strip()]
        if clean:
            parts.append("Email: " + ", ".join(clean[:2]))
    if isinstance(website, str) and website.strip():
        parts.append("Website: " + website.strip())
    if not parts:
        return ""
    return "\n" + "\n".join(parts)


def _loan_cost_summary(amount_inr: float, tenure_days: int, apr_percent: float) -> str:
    breakdown = loan_cost_breakdown(amount_inr, tenure_days, apr_percent)
    months = int(breakdown["months"])
    return (
        f"On {_format_inr(float(amount_inr))}, {apr_percent:g}% APR works out to about "
        f"{_format_inr(float(breakdown['annual_interest']))} interest over a year and "
        f"{_format_inr(float(breakdown['monthly_interest']))} interest per month. "
        f"For {int(tenure_days)} days, estimated interest is {_format_inr(float(breakdown['tenure_interest']))}, "
        f"so total repayment is about {_format_inr(float(breakdown['total_repayment']))} before fees. "
        f"If that cost is spread across ~{months} month{'s' if months != 1 else ''}, "
        f"that is about {_format_inr(float(breakdown['monthly_payment']))} per month."
    )


def _alternatives_rows(conn, *, district: str, current_rate: float | None, exclude_lender: str | None, n: int) -> list[dict[str, Any]]:
    params: list[Any] = [district]
    where_extra = ""
    if current_rate is not None:
        where_extra += " AND r.rate_apr < ?"
        params.append(float(current_rate))
    if exclude_lender:
        where_extra += " AND l.name <> ?"
        params.append(exclude_lender)
    params.append(int(n))

    rows = conn.execute(
        f"""
        SELECT d.name AS district, l.name AS lender, r.rate_apr, r.effective_date, r.source
        FROM mfi_rates r
        JOIN mfi_districts d ON d.id = r.district_id
        JOIN mfi_lenders l ON l.id = r.lender_id
        WHERE d.name = ?
            {where_extra}
        ORDER BY r.rate_apr ASC, l.name COLLATE NOCASE ASC, l.id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def _fallback_rows(conn, *, district: str, n: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.name AS district, l.name AS lender, r.rate_apr, r.effective_date, r.source
        FROM mfi_rates r
        JOIN mfi_districts d ON d.id = r.district_id
        JOIN mfi_lenders l ON l.id = r.lender_id
        WHERE d.name = ?
        ORDER BY r.rate_apr ASC, l.name COLLATE NOCASE ASC, l.id ASC
        LIMIT ?
        """,
        (district, int(n)),
    ).fetchall()
    return [dict(r) for r in rows]


def recommended_lender_rows(
    conn,
    *,
    district: str,
    current_rate: float | None = None,
    exclude_lender: str | None = None,
    n: int = 3,
) -> list[dict[str, Any]]:
    rows = _alternatives_rows(conn, district=district, current_rate=current_rate, exclude_lender=exclude_lender, n=n)
    if not rows:
        rows = _fallback_rows(conn, district=district, n=n)
    return rows


def _selection_prompt(count: int) -> str:
    if count <= 0:
        return ""
    if count == 1:
        return "Reply 1 to explore this option."
    if count == 2:
        return "Reply 1 or 2 to explore an option."
    return "Reply 1, 2, or 3 to explore an option."


def _render_rows(rows: list[dict[str, Any]], *, amount_inr: float | None = None, tenure_days: int | None = None) -> str:
    parts: list[str] = []
    for i, r in enumerate(rows, start=1):
        lender = str(r.get("lender") or "")
        rate = float(r.get("rate_apr") or 0.0)
        monthly = _apr_to_monthly_percent(rate)
        estimate = ""
        if amount_inr is not None and tenure_days is not None:
            breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), rate)
            months = int(breakdown["months"])
            estimate = (
                f"; on {_format_inr(float(amount_inr))}: about {_format_inr(float(breakdown['annual_interest']))}/year, "
                f"{_format_inr(float(breakdown['monthly_interest']))}/month; "
                f"for {int(tenure_days)} days: repay about {_format_inr(float(breakdown['total_repayment']))} "
                f"(interest about {_format_inr(float(breakdown['tenure_interest']))}, "
                f"about {_format_inr(float(breakdown['monthly_payment']))}/month over ~{months} month{'s' if months != 1 else ''})"
            )
        parts.append(f"{i}) {lender} (~{rate:g}% APR ≈{_format_percent(monthly)}%/month{estimate})")
    return "\n".join(parts)


def suggest_lender_message(
    conn,
    *,
    district: str,
    current_rate: float | None = None,
    exclude_lender: str | None = None,
    amount_inr: float | None = None,
    tenure_days: int | None = None,
    n: int = 3,
) -> str:
    rows = recommended_lender_rows(
        conn,
        district=district,
        current_rate=current_rate,
        exclude_lender=exclude_lender,
        n=n,
    )
    if not rows:
        return (
            f"Thanks. I don’t have regulated lender rate data for {district} yet. "
            "You can reply DISTRICT <name> to update your district or STOP to opt out."
        )
    joined = _render_rows(rows, amount_inr=amount_inr, tenure_days=tenure_days)

    preface = ""
    if current_rate is not None:
        monthly = _apr_to_monthly_percent(float(current_rate))
        preface = f"Your quoted rate: ~{current_rate:g}% APR (≈{_format_percent(monthly)}%/month).\n"

    estimate = ""
    if amount_inr is not None and tenure_days is not None and current_rate is not None:
        current_breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), float(current_rate))
        best_apr = float(min(float(r.get("rate_apr") or 0.0) for r in rows))
        best_breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), best_apr)
        save = max(0.0, float(current_breakdown["total_repayment"]) - float(best_breakdown["total_repayment"]))
        estimate = (
            f"Your current quote: {_loan_cost_summary(float(amount_inr), int(tenure_days), float(current_rate))}\n"
            f"At the best option shown here (~{best_apr:g}% APR), total repayment would be about "
            f"{_format_inr(float(best_breakdown['total_repayment']))}, so you could save about {_format_inr(save)}.\n"
        )
    elif amount_inr is None or tenure_days is None:
        estimate = (
            "To tell you what you would actually pay in rupees for each option, send the loan amount and loan time. "
            "Example: Need 5000 for 30 days.\n"
        )

    assumptions = "These estimates assume APR-only simple interest and no extra fees, insurance, or penalties."
    why = "Why regulated? They’re licensed or registered, usually have clearer terms, and are less likely to surprise you on collections."
    if current_rate is not None:
        heading = f"In {district}, here are regulated alternatives with lower indicative APR:"
    else:
        heading = f"In {district}, here are the top local regulated options by indicative APR:"

    prompt = _selection_prompt(len(rows))
    prompt_block = f"{prompt}\n\n" if prompt else ""
    return (
        f"{preface}{estimate}{heading}\n"
        f"{joined}\n\n"
        f"{prompt_block}"
        f"{assumptions}\n\n"
        f"{why}\n\n"
        "Reply DISTRICT <name> to change district. Reply STOP to opt out."
    )


def alert_message(
    conn,
    *,
    district: str,
    quoted_apr: float,
    current_lender: str | None = None,
    amount_inr: float | None = None,
    tenure_days: int | None = None,
    n: int = 3,
) -> str:
    rows = recommended_lender_rows(
        conn,
        district=district,
        current_rate=quoted_apr,
        exclude_lender=current_lender,
        n=n,
    )
    if not rows:
        return (
            f"If you’re being quoted ~{quoted_apr:g}% APR (≈{_format_percent(_apr_to_monthly_percent(quoted_apr))}%/month), that’s very costly. "
            f"I don’t have regulated lender rate data for {district} yet. Reply STOP to opt out."
        )
    joined = _render_rows(rows, amount_inr=amount_inr, tenure_days=tenure_days)

    estimate = ""
    if amount_inr is not None and tenure_days is not None:
        quoted_breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), float(quoted_apr))
        best_apr = float(min(float(r.get("rate_apr") or 0.0) for r in rows))
        best_breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), best_apr)
        save = max(0.0, float(quoted_breakdown["total_repayment"]) - float(best_breakdown["total_repayment"]))
        estimate = (
            f"For your loan: {_loan_cost_summary(float(amount_inr), int(tenure_days), float(quoted_apr))}\n"
            f"At the best option shown here (~{best_apr:g}% APR), total repayment would be about "
            f"{_format_inr(float(best_breakdown['total_repayment']))}, so you could save about {_format_inr(save)}.\n"
        )

    assumptions = "These estimates assume APR-only simple interest and no extra fees, insurance, or penalties."
    why = "Why regulated? They’re licensed or registered, usually have clearer terms, and are less likely to surprise you on collections."
    prompt = _selection_prompt(len(rows))
    prompt_block = f"{prompt}\n\n" if prompt else ""
    return (
        f"If you’re being quoted ~{quoted_apr:g}% APR (≈{_format_percent(_apr_to_monthly_percent(quoted_apr))}%/month), that’s very costly.\n"
        f"{estimate}In {district}, some regulated alternatives (APR is annualised):\n{joined}\n\n"
        f"{prompt_block}"
        f"{assumptions}\n\n"
        f"{why}\n\n"
        "Reply DISTRICT <name> to change district. Reply STOP to opt out."
    )


def lender_detail_fallback(*, option: dict[str, Any], rank: int, district: str | None = None) -> str:
    lender = str(option.get("lender") or "this lender")
    rate = float(option.get("rate_apr") or 0.0)
    monthly = _apr_to_monthly_percent(rate)
    effective_date = str(option.get("effective_date") or "").strip()
    source = str(option.get("source") or "").strip()
    where = district or str(option.get("district") or "").strip() or "your district"
    amount_inr = option.get("amount_inr")
    tenure_days = option.get("tenure_days")
    option_count = int(option.get("option_count") or 0)

    date_line = f"\nRate data date: {effective_date}." if effective_date else ""
    source_line = f"\nSource note: {source[:220]}{'...' if len(source) > 220 else ''}" if source else ""
    estimate_line = ""
    followup_line = ""
    if amount_inr is not None and tenure_days is not None:
        estimate_line = (
            f"\n{_loan_cost_summary(float(amount_inr), int(tenure_days), rate)}"
            "\nThese numbers use APR only and assume no processing fee, insurance, or penalty charges."
        )
        followup_line = ""
    else:
        estimate_line = (
            "\nIf you tell me the loan amount and how long you need it for, I can estimate the yearly cost, monthly cost, "
            "and likely total repayment for this option in rupees."
        )
        followup_line = "Reply with something like: 5000 for 30 days."
    if option_count <= 1:
        next_step = f"Reply CONTACTED {lender} after you contact them."
    elif option_count == 2:
        next_step = f"Reply CONTACTED {lender} after you contact them, or reply 1 or 2 to explore another option."
    else:
        next_step = f"Reply CONTACTED {lender} after you contact them, or reply 1, 2, or 3 to explore another option."
    contact = lender_contact_block(lender)
    contact_fallback = (
        "\nI don’t have a verified phone/email for this lender in my directory yet. "
        "Use their official website or branch locator, or search on Google Maps for the nearest branch/office."
        if contact == ""
        else "\nVerified contacts:"
    )
    return (
        f"Option {int(rank)}: {lender}\n"
        f"Indicative rate: ~{rate:g}% APR (about {_format_percent(monthly)}% per month) in {where}."
        f"{estimate_line}{date_line}{source_line}\n\n"
        + (f"{followup_line}\n\n" if followup_line else "")
        + contact_fallback
        + contact
        + "\n\nBefore applying, ask them to confirm the exact EMI or monthly payment, total repayment, all fees, penalties, documents needed, and collection terms.\n\n"
        + next_step
    )


def education_message(*, district: str | None) -> str:
    where = f"in {district}" if district else "in your area"
    return (
        "Quick tip before you borrow:\n"
        "1) Ask for APR (%) and total repayment amount\n"
        "2) Ask if there are any fees/penalties\n"
        f"3) Compare with regulated lenders {where}\n\n"
        "Reply STOP to opt out."
    )
