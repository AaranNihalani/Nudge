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


def _fmt(amount_inr: float) -> str:
    amt = int(round(float(amount_inr)))
    return f"₹{amt:,}"


# Keep old name as alias so other modules don't break
def _format_inr(amount_inr: float) -> str:
    return _fmt(amount_inr)


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
    return " ".join([p for p in joined.split() if p])


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


def _selection_prompt(count: int) -> str:
    if count <= 0:
        return ""
    if count == 1:
        return "Reply 1 to see full details."
    if count == 2:
        return "Reply 1 or 2 to see full details."
    return "Reply 1, 2, or 3 to see full details."


def _render_rows(rows: list[dict[str, Any]], *, amount_inr: float | None = None, tenure_days: int | None = None) -> str:
    """Render a compact lender list — APR, monthly rate, and total repayment only."""
    parts: list[str] = []
    for i, r in enumerate(rows, start=1):
        lender = str(r.get("lender") or "")
        rate = float(r.get("rate_apr") or 0.0)
        monthly = _apr_to_monthly_percent(rate)
        line = f"{i}. {lender} — {rate:g}% APR (~{_format_percent(monthly)}%/month)"
        if amount_inr is not None and tenure_days is not None:
            breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), rate)
            months = int(breakdown["months"])
            line += (
                f"\n   {_fmt(float(breakdown['total_repayment']))} total over {months} month{'s' if months != 1 else ''}"
                f" ({_fmt(float(breakdown['monthly_payment']))}/month)"
            )
        parts.append(line)
    return "\n".join(parts)


def recommend_lender_rows(
    conn,
    *,
    district: str,
    current_rate: float | None = None,
    exclude_lender: str | None = None,
    n: int = 3,
) -> list[dict[str, Any]]:
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
    if rows:
        return [dict(r) for r in rows]
    # Fallback: show lowest rates regardless of current_rate
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


# Keep old name so handler.py doesn't break
def recommended_lender_rows(conn, *, district: str, current_rate: float | None = None,
                            exclude_lender: str | None = None, n: int = 3) -> list[dict[str, Any]]:
    return recommend_lender_rows(conn, district=district, current_rate=current_rate,
                                 exclude_lender=exclude_lender, n=n)


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
    rows = recommend_lender_rows(
        conn, district=district, current_rate=current_rate,
        exclude_lender=exclude_lender, n=n,
    )
    if not rows:
        return f"I don't have regulated lender rate data for {district} yet. Try a nearby district — reply DISTRICT <name>."

    joined = _render_rows(rows, amount_inr=amount_inr, tenure_days=tenure_days)
    prompt = _selection_prompt(len(rows))

    if amount_inr is None or tenure_days is None:
        ask = "\nSend the loan amount and how long you need it for to see rupee totals."
    else:
        ask = ""

    return (
        f"Regulated options in {district}:\n\n{joined}\n\n{prompt}{ask}\n\n"
        "Numbers assume simple interest and no extra fees."
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
    rows = recommend_lender_rows(
        conn, district=district, current_rate=quoted_apr,
        exclude_lender=current_lender, n=n,
    )
    monthly_quoted = _format_percent(_apr_to_monthly_percent(quoted_apr))

    if not rows:
        return (
            f"At {quoted_apr:g}% APR (~{monthly_quoted}%/month), that rate is high. "
            f"I don't have regulated lender data for {district} yet."
        )

    joined = _render_rows(rows, amount_inr=amount_inr, tenure_days=tenure_days)
    prompt = _selection_prompt(len(rows))

    savings_line = ""
    if amount_inr is not None and tenure_days is not None:
        quoted_breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), float(quoted_apr))
        best_apr = float(min(float(r.get("rate_apr") or 0.0) for r in rows))
        best_breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), best_apr)
        save = max(0.0, float(quoted_breakdown["total_repayment"]) - float(best_breakdown["total_repayment"]))
        if save > 0:
            savings_line = (
                f"\nAt your quoted rate you'd repay {_fmt(float(quoted_breakdown['total_repayment']))} total. "
                f"The cheapest option above is {_fmt(float(best_breakdown['total_repayment']))} — "
                f"about {_fmt(save)} less.\n"
            )

    return (
        f"At {quoted_apr:g}% APR (~{monthly_quoted}%/month), that's costly. "
        f"Regulated alternatives in {district}:\n\n"
        f"{joined}\n{savings_line}\n{prompt}\n\n"
        "Numbers assume simple interest and no extra fees."
    )


def lender_detail_fallback(*, option: dict[str, Any], rank: int, district: str | None = None) -> str:
    lender = str(option.get("lender") or "this lender")
    rate = float(option.get("rate_apr") or 0.0)
    monthly = _apr_to_monthly_percent(rate)
    effective_date = str(option.get("effective_date") or "").strip()
    where = district or str(option.get("district") or "").strip() or "your district"
    amount_inr = option.get("amount_inr")
    tenure_days = option.get("tenure_days")
    option_count = int(option.get("option_count") or 0)

    header = f"{lender} — {rate:g}% APR (~{_format_percent(monthly)}%/month) in {where}"
    if effective_date:
        header += f" (rate as of {effective_date})"

    if amount_inr is not None and tenure_days is not None:
        breakdown = loan_cost_breakdown(float(amount_inr), int(tenure_days), rate)
        months = int(breakdown["months"])
        cost_block = (
            f"\nFor {_fmt(float(amount_inr))} over {months} month{'s' if months != 1 else ''}:\n"
            f"• Monthly interest: ~{_fmt(float(breakdown['monthly_interest']))}\n"
            f"• Total interest: ~{_fmt(float(breakdown['tenure_interest']))}\n"
            f"• Total repayment: ~{_fmt(float(breakdown['total_repayment']))}\n"
            f"• Estimated monthly payment: ~{_fmt(float(breakdown['monthly_payment']))}\n"
            "\nThese numbers assume simple interest and no processing fees, insurance, or penalties."
        )
    else:
        cost_block = (
            "\nSend the loan amount and tenure to see rupee totals — e.g. 5000 for 30 days."
        )

    contact = lender_contact_block(lender)
    contact_block = (
        f"\nContacts:{contact}"
        if contact
        else "\nNo verified contact details on file. Search their name online or visit a local branch."
    )

    if option_count <= 1:
        other_opts = ""
    elif option_count == 2:
        other_opts = " Or reply 1 or 2 to look at a different option."
    else:
        other_opts = " Or reply 1, 2, or 3 to look at a different option."

    return (
        f"{header}\n"
        f"{cost_block}\n"
        f"{contact_block}\n\n"
        f"Before applying, ask them to confirm the exact EMI, all fees, and total repayment in writing.\n\n"
        f"Let me know when you've spoken to them.{other_opts}"
    )


def education_message(*, district: str | None) -> str:
    where = f"in {district}" if district else "in your area"
    return (
        f"Before borrowing, it's worth checking regulated lenders {where}. "
        "Ask for the APR, total repayment amount, and any fees upfront. "
        "Send your loan amount and tenure and I can show you what's available."
    )
