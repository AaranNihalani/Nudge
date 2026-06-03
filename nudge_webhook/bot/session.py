"""User session management — load, save, and manage per-user session state."""
from __future__ import annotations

import json
from typing import Any


def ensure_user_session(conn, *, user_id: int) -> None:
    conn.execute(
        "INSERT INTO user_sessions(user_id) VALUES (?) ON CONFLICT (user_id) DO NOTHING",
        (int(user_id),),
    )


def load_user_session(conn, *, user_id: int) -> dict[str, Any]:
    ensure_user_session(conn, user_id=user_id)
    row = conn.execute(
        """
        SELECT districts_prefix, districts_offset, districts_page_size,
               borrow_draft_json, borrow_source_raw_message_id, borrow_model,
               lender_options_json, lender_options_updated_at,
               selected_lender_option_json, selected_lender_rank, selected_lender_updated_at,
               profile_step
        FROM user_sessions WHERE user_id = ?
        """,
        (int(user_id),),
    ).fetchone()
    return dict(row) if row is not None else {}


def save_district_paging(conn, *, user_id: int, prefix: str | None, offset: int, page_size: int) -> None:
    ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET districts_prefix = ?, districts_offset = ?, districts_page_size = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (prefix, int(offset), int(page_size), int(user_id)),
    )


def clear_district_paging(conn, *, user_id: int) -> None:
    ensure_user_session(conn, user_id=user_id)
    conn.execute(
        "UPDATE user_sessions SET districts_prefix = NULL, districts_offset = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (int(user_id),),
    )


def save_borrow_draft(conn, *, user_id: int, payload: dict[str, Any] | None, source_raw_message_id: int | None, model: str | None) -> None:
    ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET borrow_draft_json = ?, borrow_source_raw_message_id = ?, borrow_model = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            int(source_raw_message_id) if source_raw_message_id is not None else None,
            model,
            int(user_id),
        ),
    )


def save_lender_options(conn, *, user_id: int, options: list[dict[str, Any]] | None) -> None:
    ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET lender_options_json = ?,
            lender_options_updated_at = CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (
            json.dumps(options, ensure_ascii=False) if options else None,
            json.dumps(options, ensure_ascii=False) if options else None,
            int(user_id),
        ),
    )


def save_selected_lender(conn, *, user_id: int, option: dict[str, Any] | None, rank: int | None) -> None:
    ensure_user_session(conn, user_id=user_id)
    conn.execute(
        """
        UPDATE user_sessions
        SET selected_lender_option_json = ?, selected_lender_rank = ?,
            selected_lender_updated_at = CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (
            json.dumps(option, ensure_ascii=False) if option else None,
            int(rank) if option is not None and rank is not None else None,
            json.dumps(option, ensure_ascii=False) if option else None,
            int(user_id),
        ),
    )


def save_profile_step(conn, *, user_id: int, step: str | None) -> None:
    ensure_user_session(conn, user_id=user_id)
    conn.execute(
        "UPDATE user_sessions SET profile_step = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (step, int(user_id)),
    )


def load_lender_options(session: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (session or {}).get("lender_options_json")
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
        return [dict(x) for x in parsed if isinstance(x, dict)] if isinstance(parsed, list) else []
    except Exception:
        return []


def load_selected_lender(session: dict[str, Any] | None) -> tuple[int | None, dict[str, Any] | None]:
    raw = (session or {}).get("selected_lender_option_json")
    if not raw:
        return None, None
    try:
        parsed = json.loads(str(raw))
        if not isinstance(parsed, dict):
            return None, None
        rank = None
        raw_rank = (session or {}).get("selected_lender_rank")
        if raw_rank is not None:
            try:
                rank = int(raw_rank)
            except Exception:
                pass
        return rank, dict(parsed)
    except Exception:
        return None, None
