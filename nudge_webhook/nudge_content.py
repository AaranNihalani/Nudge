from __future__ import annotations

from typing import Any


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


def _render_rows(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, r in enumerate(rows, start=1):
        lender = str(r.get("lender") or "")
        rate = float(r.get("rate_apr") or 0.0)
        parts.append(f"{i}) {lender} (~{rate:g}% APR)")
    return "\n".join(parts)


def suggest_lender_message(
    conn,
    *,
    district: str,
    current_rate: float | None = None,
    exclude_lender: str | None = None,
    n: int = 3,
) -> str:
    rows = _alternatives_rows(conn, district=district, current_rate=current_rate, exclude_lender=exclude_lender, n=n)
    if not rows:
        rows = _fallback_rows(conn, district=district, n=n)
    if not rows:
        return (
            f"Thanks. I don’t have regulated lender rate data for {district} yet. "
            "You can reply DISTRICT <name> to update your district or STOP to opt out."
        )
    joined = _render_rows(rows)
    return (
        f"In {district}, some regulated alternatives with lower indicative APR:\n{joined}\n\n"
        "Reply DISTRICT <name> to change district. Reply STOP to opt out."
    )


def alert_message(
    conn,
    *,
    district: str,
    quoted_apr: float,
    current_lender: str | None = None,
    n: int = 3,
) -> str:
    rows = _alternatives_rows(conn, district=district, current_rate=quoted_apr, exclude_lender=current_lender, n=n)
    if not rows:
        rows = _fallback_rows(conn, district=district, n=n)
    if not rows:
        return (
            f"If you’re being quoted ~{quoted_apr:g}% APR, that’s very costly. "
            f"I don’t have regulated lender rate data for {district} yet. Reply STOP to opt out."
        )
    joined = _render_rows(rows)
    return (
        f"If you’re being quoted ~{quoted_apr:g}% APR, that’s very costly.\n"
        f"In {district}, some regulated alternatives:\n{joined}\n\n"
        "Reply DISTRICT <name> to change district. Reply STOP to opt out."
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
