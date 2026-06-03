from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect


def parse_ts(value: str) -> datetime:
    """Parse timestamp string from DB. Handles both SQLite and PostgreSQL TEXT formats."""
    return datetime.strptime((value or "").strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def format_ts(dt: datetime) -> str:
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

    # Household profile (from AIDIS-informed onboarding)
    caste: str | None              # 'sc', 'st', 'obc', 'others'
    religion: str | None           # 'hindu', 'muslim', 'christian', 'others'
    mpce_inr: float | None         # monthly per-capita expenditure in INR
    household_size: int | None
    land_acres: float | None
    urban: int | None              # 1 = urban, 0 = rural
    profile_complete: bool

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


def _borrow_snapshot(conn, *, user_id: int) -> BorrowSnapshot:
    last_intent = conn.execute(
        """
        SELECT id, parsed_at, negotiation_stage, interest_rate_apr
        FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1
        ORDER BY parsed_at DESC, id DESC LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()

    last_borrowed = conn.execute(
        """
        SELECT parsed_at FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1 AND negotiation_stage = 'borrowed'
        ORDER BY parsed_at DESC, id DESC LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()

    amount_row = conn.execute(
        """
        SELECT amount_inr FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1 AND amount_inr IS NOT NULL
        ORDER BY parsed_at DESC, id DESC LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()

    tenure_row = conn.execute(
        """
        SELECT tenure_days FROM parsed_events
        WHERE user_id = ? AND event_type = 'borrow_intent' AND intent = 1 AND tenure_days IS NOT NULL
        ORDER BY parsed_at DESC, id DESC LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()

    return BorrowSnapshot(
        last_intent_event_id=int(last_intent["id"]) if last_intent else None,
        last_intent_at=parse_ts(str(last_intent["parsed_at"])) if last_intent else None,
        last_stage=str(last_intent["negotiation_stage"]) if last_intent else None,
        implied_apr=float(last_intent["interest_rate_apr"]) if last_intent and last_intent["interest_rate_apr"] is not None else None,
        last_borrowed_at=parse_ts(str(last_borrowed["parsed_at"])) if last_borrowed else None,
        amount_inr=float(amount_row["amount_inr"]) if amount_row else None,
        tenure_days=int(tenure_row["tenure_days"]) if tenure_row else None,
    )


def _nudge_history(conn, *, user_id: int, now: datetime) -> NudgeHistory:
    last_row = conn.execute(
        "SELECT nudge_type, sent_at FROM nudges WHERE user_id = ? ORDER BY sent_at DESC, id DESC LIMIT 1",
        (int(user_id),),
    ).fetchone()

    now_ts = now.astimezone(timezone.utc).replace(microsecond=0)
    start_7d = format_ts(now_ts - timedelta(days=7))
    start_30d = format_ts(now_ts - timedelta(days=30))

    total = int(conn.execute("SELECT COUNT(*) AS c FROM nudges WHERE user_id = ?", (int(user_id),)).fetchone()["c"])
    count_7d = int(conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?", (int(user_id), start_7d)
    ).fetchone()["c"])
    count_30d = int(conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?", (int(user_id), start_30d)
    ).fetchone()["c"])

    return NudgeHistory(
        total=total,
        last_nudge_at=parse_ts(str(last_row["sent_at"])) if last_row and last_row["sent_at"] else None,
        last_nudge_type=str(last_row["nudge_type"]) if last_row and last_row["nudge_type"] else None,
        count_7d=count_7d,
        count_30d=count_30d,
    )


def _engagement_history(conn, *, user_id: int, now: datetime) -> EngagementHistory:
    last_inbound_row = conn.execute(
        """
        SELECT received_at FROM raw_messages
        WHERE user_id = ? AND direction = 'inbound'
        ORDER BY received_at DESC, id DESC LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()

    now_ts = now.astimezone(timezone.utc).replace(microsecond=0)
    start_30d = format_ts(now_ts - timedelta(days=30))
    nudge_rows = conn.execute(
        "SELECT id, sent_at FROM nudges WHERE user_id = ? AND sent_at >= ? ORDER BY sent_at ASC, id ASC",
        (int(user_id), start_30d),
    ).fetchall()

    engaged = 0
    for n in nudge_rows:
        sent_at = parse_ts(str(n["sent_at"]))
        window_end = format_ts(sent_at + timedelta(hours=48))
        exists = conn.execute(
            """
            SELECT 1 FROM raw_messages
            WHERE user_id = ? AND direction = 'inbound' AND received_at > ? AND received_at <= ?
            LIMIT 1
            """,
            (int(user_id), format_ts(sent_at), window_end),
        ).fetchone()
        if exists is not None:
            engaged += 1

    return EngagementHistory(
        last_inbound_at=parse_ts(str(last_inbound_row["received_at"])) if last_inbound_row and last_inbound_row["received_at"] else None,
        engaged_nudges_30d=int(engaged),
        nudges_30d=int(len(nudge_rows)),
    )


def compute_user_state(db_path: str, *, user_id: int, now: datetime) -> UserState:
    conn = connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, consent_status, district, caste, religion, mpce_inr,
                   household_size, land_acres, urban, profile_complete
            FROM users WHERE id = ?
            """,
            (int(user_id),),
        ).fetchone()
        if row is None:
            raise ValueError("unknown_user")

        now_utc = now.astimezone(timezone.utc).replace(microsecond=0)
        return UserState(
            user_id=int(row["id"]),
            now=now_utc,
            consent_status=str(row["consent_status"] or "unknown"),
            district=str(row["district"]) if row["district"] is not None else None,
            caste=str(row["caste"]) if row["caste"] is not None else None,
            religion=str(row["religion"]) if row["religion"] is not None else None,
            mpce_inr=float(row["mpce_inr"]) if row["mpce_inr"] is not None else None,
            household_size=int(row["household_size"]) if row["household_size"] is not None else None,
            land_acres=float(row["land_acres"]) if row["land_acres"] is not None else None,
            urban=int(row["urban"]) if row["urban"] is not None else None,
            profile_complete=bool(int(row["profile_complete"] or 0)),
            borrow=_borrow_snapshot(conn, user_id=user_id),
            nudges=_nudge_history(conn, user_id=user_id, now=now_utc),
            engagement=_engagement_history(conn, user_id=user_id, now=now_utc),
        )
    finally:
        conn.close()
