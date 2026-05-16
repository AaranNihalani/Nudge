from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect


def _parse_sqlite_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _format_sqlite_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class NudgeHistory:
    total: int
    last_nudge_at: datetime | None
    last_nudge_type: str | None
    count_7d: int
    count_30d: int


@dataclass(frozen=True)
class EngagementHistory:
    last_inbound_at: datetime | None
    engaged_nudges_30d: int
    nudges_30d: int

    @property
    def engagement_rate_30d(self) -> float | None:
        if self.nudges_30d <= 0:
            return None
        return float(self.engaged_nudges_30d) / float(self.nudges_30d)


@dataclass(frozen=True)
class BorrowSnapshot:
    last_intent_event_id: int | None
    last_intent_at: datetime | None
    last_stage: str | None
    last_borrowed_at: datetime | None
    implied_apr: float | None
    amount_inr: float | None
    tenure_days: int | None

    def days_since_borrow(self, *, now: datetime) -> float | None:
        if self.last_borrowed_at is None:
            return None
        delta = now.astimezone(timezone.utc) - self.last_borrowed_at.astimezone(timezone.utc)
        return max(0.0, delta.total_seconds() / 86400.0)

    def debt_burden_proxy(self) -> float | None:
        if self.amount_inr is None or self.tenure_days is None or self.implied_apr is None:
            return None
        ratio = (self.implied_apr / 100.0) * (float(self.tenure_days) / 365.0)
        return max(0.0, float(self.amount_inr) * ratio)


@dataclass(frozen=True)
class UserState:
    user_id: int
    now: datetime
    consent_status: str
    district: str | None
    borrow: BorrowSnapshot
    nudges: NudgeHistory
    engagement: EngagementHistory

    @property
    def days_since_borrow(self) -> float | None:
        return self.borrow.days_since_borrow(now=self.now)

    @property
    def implied_apr(self) -> float | None:
        return self.borrow.implied_apr

    @property
    def debt_burden_proxy(self) -> float | None:
        return self.borrow.debt_burden_proxy()

    @property
    def days_since_last_nudge(self) -> float | None:
        if self.nudges.last_nudge_at is None:
            return None
        delta = self.now.astimezone(timezone.utc) - self.nudges.last_nudge_at.astimezone(timezone.utc)
        return max(0.0, delta.total_seconds() / 86400.0)

    @property
    def days_since_last_inbound(self) -> float | None:
        if self.engagement.last_inbound_at is None:
            return None
        delta = self.now.astimezone(timezone.utc) - self.engagement.last_inbound_at.astimezone(timezone.utc)
        return max(0.0, delta.total_seconds() / 86400.0)


def _fetch_user_row(conn, *, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, consent_status, district FROM users WHERE id = ?",
        (int(user_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def _borrow_snapshot(conn, *, user_id: int) -> BorrowSnapshot:
    last_intent = conn.execute(
        """
        SELECT id, parsed_at, negotiation_stage
        FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1
        ORDER BY parsed_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    last_intent_event_id = int(last_intent["id"]) if last_intent is not None else None
    last_intent_at = _parse_sqlite_ts(str(last_intent["parsed_at"])) if last_intent is not None else None
    last_stage = str(last_intent["negotiation_stage"]) if last_intent is not None else None

    last_borrowed = conn.execute(
        """
        SELECT parsed_at
        FROM parsed_events
        WHERE user_id = ?
            AND event_type = 'borrow_intent'
            AND intent = 1
            AND negotiation_stage = 'borrowed'
        ORDER BY parsed_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    last_borrowed_at = _parse_sqlite_ts(str(last_borrowed["parsed_at"])) if last_borrowed is not None else None

    rate_row = conn.execute(
        """
        SELECT interest_rate_apr
        FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1 AND interest_rate_apr IS NOT NULL
        ORDER BY parsed_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    implied_apr = float(rate_row["interest_rate_apr"]) if rate_row is not None else None

    amount_row = conn.execute(
        """
        SELECT amount_inr
        FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1 AND amount_inr IS NOT NULL
        ORDER BY parsed_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    amount_inr = float(amount_row["amount_inr"]) if amount_row is not None else None

    tenure_row = conn.execute(
        """
        SELECT tenure_days
        FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1 AND tenure_days IS NOT NULL
        ORDER BY parsed_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    tenure_days = int(tenure_row["tenure_days"]) if tenure_row is not None else None

    return BorrowSnapshot(
        last_intent_event_id=last_intent_event_id,
        last_intent_at=last_intent_at,
        last_stage=last_stage,
        last_borrowed_at=last_borrowed_at,
        implied_apr=implied_apr,
        amount_inr=amount_inr,
        tenure_days=tenure_days,
    )


def _nudge_history(conn, *, user_id: int, now: datetime) -> NudgeHistory:
    last_row = conn.execute(
        """
        SELECT nudge_type, sent_at
        FROM nudges
        WHERE user_id = ?
        ORDER BY sent_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    last_nudge_at = _parse_sqlite_ts(str(last_row["sent_at"])) if last_row is not None and last_row["sent_at"] else None
    last_nudge_type = str(last_row["nudge_type"]) if last_row is not None and last_row["nudge_type"] else None

    now_ts = now.astimezone(timezone.utc).replace(microsecond=0)
    start_7d = now_ts - timedelta(days=7)
    start_30d = now_ts - timedelta(days=30)

    total = int(
        conn.execute("SELECT COUNT(*) AS c FROM nudges WHERE user_id = ?", (int(user_id),)).fetchone()["c"]
    )
    count_7d = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?",
            (int(user_id), _format_sqlite_ts(start_7d)),
        ).fetchone()["c"]
    )
    count_30d = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?",
            (int(user_id), _format_sqlite_ts(start_30d)),
        ).fetchone()["c"]
    )

    return NudgeHistory(
        total=total,
        last_nudge_at=last_nudge_at,
        last_nudge_type=last_nudge_type,
        count_7d=count_7d,
        count_30d=count_30d,
    )


def _engagement_history(conn, *, user_id: int, now: datetime) -> EngagementHistory:
    last_inbound_row = conn.execute(
        """
        SELECT received_at
        FROM raw_messages
        WHERE user_id = ? AND direction = 'inbound'
        ORDER BY received_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    last_inbound_at = (
        _parse_sqlite_ts(str(last_inbound_row["received_at"]))
        if last_inbound_row is not None and last_inbound_row["received_at"]
        else None
    )

    now_ts = now.astimezone(timezone.utc).replace(microsecond=0)
    start_30d = now_ts - timedelta(days=30)
    nudge_rows = conn.execute(
        """
        SELECT id, sent_at
        FROM nudges
        WHERE user_id = ? AND sent_at >= ?
        ORDER BY sent_at ASC, id ASC
        """,
        (int(user_id), _format_sqlite_ts(start_30d)),
    ).fetchall()

    engaged = 0
    for n in nudge_rows:
        sent_at = _parse_sqlite_ts(str(n["sent_at"]))
        window_end = sent_at + timedelta(hours=48)
        exists = conn.execute(
            """
            SELECT 1
            FROM raw_messages
            WHERE user_id = ?
                AND direction = 'inbound'
                AND received_at > ?
                AND received_at <= ?
            LIMIT 1
            """,
            (int(user_id), _format_sqlite_ts(sent_at), _format_sqlite_ts(window_end)),
        ).fetchone()
        if exists is not None:
            engaged += 1

    return EngagementHistory(
        last_inbound_at=last_inbound_at,
        engaged_nudges_30d=int(engaged),
        nudges_30d=int(len(nudge_rows)),
    )


def compute_user_state(db_path: str, *, user_id: int, now: datetime) -> UserState:
    conn = connect(db_path)
    try:
        user_row = _fetch_user_row(conn, user_id=user_id)
        if user_row is None:
            raise ValueError("unknown_user")
        district = str(user_row["district"]) if user_row.get("district") is not None else None
        consent_status = str(user_row.get("consent_status") or "unknown")
        borrow = _borrow_snapshot(conn, user_id=user_id)
        nudges = _nudge_history(conn, user_id=user_id, now=now)
        engagement = _engagement_history(conn, user_id=user_id, now=now)
        return UserState(
            user_id=int(user_id),
            now=now.astimezone(timezone.utc).replace(microsecond=0),
            consent_status=consent_status,
            district=district,
            borrow=borrow,
            nudges=nudges,
            engagement=engagement,
        )
    finally:
        conn.close()
