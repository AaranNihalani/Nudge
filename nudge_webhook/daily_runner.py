from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import connect
from .policy import decide_policy
from .state import compute_user_state
from .twilio_outbound import send_message


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_sqlite_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _parse_sqlite_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _nudge_limits_ok(conn, *, user_id: int, cfg: Config, now: datetime) -> bool:
    cooldown_seconds = max(0, int(cfg.nudge_cooldown_minutes) * 60)
    if cooldown_seconds > 0:
        row = conn.execute(
            "SELECT sent_at FROM nudges WHERE user_id = ? ORDER BY sent_at DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
        if row is not None and row["sent_at"]:
            last_dt = _parse_sqlite_ts(str(row["sent_at"]))
            if now < (last_dt + timedelta(seconds=cooldown_seconds)):
                return False

    day_start = now.replace(hour=0, minute=0, second=0)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)

    day_count = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?",
            (int(user_id), _format_sqlite_ts(day_start)),
        ).fetchone()["c"]
    )
    if day_count >= int(cfg.nudge_max_per_day):
        return False

    week_count = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM nudges WHERE user_id = ? AND sent_at >= ?",
            (int(user_id), _format_sqlite_ts(week_start)),
        ).fetchone()["c"]
    )
    if week_count >= int(cfg.nudge_max_per_week):
        return False

    return True


@dataclass(frozen=True)
class DailyRunResult:
    run_date: str
    skipped: bool
    evaluated_users: int
    nudges_attempted: int
    nudges_sent: int
    nudges_failed: int


def run_daily_decisions(cfg: Config, *, db_path: str, now: datetime | None = None) -> DailyRunResult:
    now_dt = now or _now_utc()
    run_date = now_dt.date().isoformat()
    cutoff = now_dt - timedelta(days=30)

    conn = connect(db_path)
    run_id: int | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                "INSERT INTO admin_runs(run_type, run_date, status) VALUES ('daily_decisions', ?, 'started')",
                (str(run_date),),
            )
            run_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            conn.rollback()
            return DailyRunResult(
                run_date=str(run_date),
                skipped=True,
                evaluated_users=0,
                nudges_attempted=0,
                nudges_sent=0,
                nudges_failed=0,
            )
        conn.commit()
    finally:
        conn.close()

    evaluated = 0
    attempted = 0
    sent = 0
    failed = 0

    conn = connect(db_path)
    try:
        users = conn.execute(
            """
            SELECT u.id, u.phone_e164
            FROM users u
            WHERE u.consent_status = 'opted_in'
                AND u.district IS NOT NULL
                AND (
                    EXISTS (
                        SELECT 1 FROM raw_messages rm
                        WHERE rm.user_id = u.id
                            AND rm.direction = 'inbound'
                            AND rm.received_at >= ?
                    )
                    OR EXISTS (
                        SELECT 1 FROM parsed_events pe
                        WHERE pe.user_id = u.id
                            AND pe.parsed_at >= ?
                    )
                )
            ORDER BY u.id ASC
            """,
            (_format_sqlite_ts(cutoff), _format_sqlite_ts(cutoff)),
        ).fetchall()
    finally:
        conn.close()

    for row in users:
        user_id = int(row["id"])
        phone = str(row["phone_e164"])
        evaluated += 1

        state = compute_user_state(db_path, user_id=user_id, now=now_dt)
        conn = connect(db_path)
        try:
            decision = decide_policy(conn, cfg=cfg, state=state)
            if decision.nudge_type is None:
                continue
            if not _nudge_limits_ok(conn, user_id=user_id, cfg=cfg, now=now_dt):
                continue

            attempted += 1
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO nudges(user_id, parsed_event_id, nudge_type, content, policy_name, policy_version, sent_at, delivery_status, trigger)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'daily')
                """,
                (
                    int(user_id),
                    decision.parsed_event_id,
                    decision.nudge_type,
                    decision.content,
                    decision.policy_name,
                    decision.policy_version,
                    _format_sqlite_ts(now_dt),
                    "pending",
                ),
            )
            nudge_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json)
                VALUES (?, 'outbound', ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    "whatsapp",
                    str(cfg.twilio_from_addr or ""),
                    phone,
                    decision.content,
                    json.dumps({"generated_at": _format_sqlite_ts(now_dt), "trigger": "daily"}, ensure_ascii=False),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            failed += 1
            continue
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not cfg.twilio_account_sid or not cfg.twilio_auth_token or not cfg.twilio_from_addr:
            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE nudges SET delivery_status = ? WHERE id = ?",
                    ("queued", int(nudge_id)),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            finally:
                conn.close()
            continue

        try:
            result = send_message(cfg, to_phone_e164=phone, body=decision.content)
            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE nudges SET twilio_message_sid = ?, delivery_status = ? WHERE id = ?",
                    (result.sid, result.status or "sent", int(nudge_id)),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            sent += 1
        except Exception:
            failed += 1
            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE nudges SET delivery_status = ? WHERE id = ?",
                    ("failed", int(nudge_id)),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            finally:
                conn.close()

    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE admin_runs
            SET status = 'finished',
                details_json = ?,
                finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                json.dumps(
                    {
                        "evaluated_users": int(evaluated),
                        "nudges_attempted": int(attempted),
                        "nudges_sent": int(sent),
                        "nudges_failed": int(failed),
                    },
                    ensure_ascii=False,
                ),
                int(run_id or 0),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return DailyRunResult(
        run_date=str(run_date),
        skipped=False,
        evaluated_users=int(evaluated),
        nudges_attempted=int(attempted),
        nudges_sent=int(sent),
        nudges_failed=int(failed),
    )
