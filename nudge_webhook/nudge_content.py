from __future__ import annotations

from typing import Any


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


def _render_rows(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, r in enumerate(rows, start=1):
        lender = str(r.get("lender") or "")
        rate = float(r.get("rate_apr") or 0.0)
        monthly = _apr_to_monthly_percent(rate)
        parts.append(f"{i}) {lender} (~{rate:g}% APR ≈{_format_percent(monthly)}%/month)")
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
    joined = _render_rows(rows)

    preface = ""
    if current_rate is not None:
        monthly = _apr_to_monthly_percent(float(current_rate))
        preface = f"Your quoted rate: ~{current_rate:g}% APR (≈{_format_percent(monthly)}%/month).\n"

    estimate = ""
    if amount_inr is not None and tenure_days is not None and current_rate is not None:
        interest, total = _simple_interest_estimate(float(amount_inr), int(tenure_days), float(current_rate))
        best_apr = float(min(float(r.get("rate_apr") or 0.0) for r in rows))
        _, best_total = _simple_interest_estimate(float(amount_inr), int(tenure_days), best_apr)
        save = max(0.0, total - best_total)
        estimate = (
            f"Rough estimate for {_format_inr(float(amount_inr))} over {int(tenure_days)} days "
            f"(no fees, simple interest): at {current_rate:g}% APR repay ~{_format_inr(total)} "
            f"(interest ~{_format_inr(interest)}). At ~{best_apr:g}% APR repay ~{_format_inr(best_total)} "
            f"(save ~{_format_inr(save)}).\n"
        )

    why = "Why regulated? They’re licensed/registered and overseen, and usually have clearer terms and fairer collections rules."
    if current_rate is not None:
        heading = f"In {district}, some regulated alternatives with lower indicative APR (APR is annualised):"
    else:
        heading = f"In {district}, the top local regulated options by indicative APR (APR is annualised):"

    return (
        f"{preface}{estimate}{heading}\n{joined}\n\n"
        "Reply 1, 2, or 3 to explore an option.\n\n"
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
    joined = _render_rows(rows)

    estimate = ""
    if amount_inr is not None and tenure_days is not None:
        interest, total = _simple_interest_estimate(float(amount_inr), int(tenure_days), float(quoted_apr))
        best_apr = float(min(float(r.get("rate_apr") or 0.0) for r in rows))
        _, best_total = _simple_interest_estimate(float(amount_inr), int(tenure_days), best_apr)
        save = max(0.0, total - best_total)
        estimate = (
            f"Rough estimate for {_format_inr(float(amount_inr))} over {int(tenure_days)} days "
            f"(no fees, simple interest): repay ~{_format_inr(total)} (interest ~{_format_inr(interest)}). "
            f"At ~{best_apr:g}% APR repay ~{_format_inr(best_total)} (save ~{_format_inr(save)}).\n"
        )

    why = "Why regulated? They’re licensed/registered and overseen, and usually have clearer terms and fairer collections rules."
    return (
        f"If you’re being quoted ~{quoted_apr:g}% APR (≈{_format_percent(_apr_to_monthly_percent(quoted_apr))}%/month), that’s very costly.\n"
        f"{estimate}In {district}, some regulated alternatives (APR is annualised):\n{joined}\n\n"
        "Reply 1, 2, or 3 to explore an option.\n\n"
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

    date_line = f"\nRate data date: {effective_date}." if effective_date else ""
    source_line = f"\nSource note: {source[:220]}{'...' if len(source) > 220 else ''}" if source else ""
    return (
        f"Option {int(rank)}: {lender}\n"
        f"Indicative rate: ~{rate:g}% APR (about {_format_percent(monthly)}% per month) in {where}."
        f"{date_line}{source_line}\n\n"
        "Before applying, ask them for the total repayment amount, all fees, penalties, documents needed, and collection terms.\n\n"
        f"Reply CONTACTED {lender} after you contact them, or reply 1, 2, or 3 to explore another option."
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
