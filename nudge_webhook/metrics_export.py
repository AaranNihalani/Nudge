from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .db import connect


def _blake_id(*, salt: str, value: str) -> str:
    payload = f"{salt}|{value}".encode("utf-8")
    return hashlib.blake2b(payload, digest_size=12).hexdigest()


def _date_range(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = cur + timedelta(days=1)
    return days


def _min_date(conn, *, table: str, column: str) -> str | None:
    row = conn.execute(f"SELECT MIN(substr({column}, 1, 10)) AS d FROM {table}").fetchone()
    if row is None:
        return None
    d = row["d"]
    return str(d) if d else None


def _max_date(conn, *, table: str, column: str) -> str | None:
    row = conn.execute(f"SELECT MAX(substr({column}, 1, 10)) AS d FROM {table}").fetchone()
    if row is None:
        return None
    d = row["d"]
    return str(d) if d else None


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass(frozen=True)
class ExportBundle:
    filename: str
    content_type: str
    data: bytes


def export_metrics_zip(*, db_path: str, anon_salt: str | None = None) -> ExportBundle:
    salt = str(anon_salt or "").strip() or "anon"

    conn = connect(db_path)
    try:
        mins = [
            _min_date(conn, table="users", column="created_at"),
            _min_date(conn, table="raw_messages", column="received_at"),
            _min_date(conn, table="parsed_events", column="parsed_at"),
            _min_date(conn, table="nudges", column="sent_at"),
            _min_date(conn, table="self_reported_switches", column="reported_at"),
        ]
        maxs = [
            _max_date(conn, table="users", column="created_at"),
            _max_date(conn, table="raw_messages", column="received_at"),
            _max_date(conn, table="parsed_events", column="parsed_at"),
            _max_date(conn, table="nudges", column="sent_at"),
            _max_date(conn, table="self_reported_switches", column="reported_at"),
        ]
        min_d = min((d for d in mins if d), default=None)
        max_d = max((d for d in maxs if d), default=None)
        if min_d is None or max_d is None:
            start = date(2026, 1, 1)
            end = start
        else:
            start = _parse_date(str(min_d))
            end = _parse_date(str(max_d))

        days = _date_range(start, end)

        by_day: dict[str, dict[str, int]] = {d.isoformat(): {} for d in days}

        def _merge_counts(query: str, *, key: str, params: tuple = ()) -> None:
            for r in conn.execute(query, params).fetchall():
                d = str(r["d"])
                c = int(r["c"] or 0)
                by_day.setdefault(d, {})[key] = int(c)

        _merge_counts(
            "SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS c FROM users GROUP BY d",
            key="new_users",
        )
        _merge_counts(
            """
            SELECT substr(consent_updated_at, 1, 10) AS d, COUNT(*) AS c
            FROM users
            WHERE consent_updated_at IS NOT NULL AND consent_status = 'opted_in'
            GROUP BY d
            """,
            key="opted_in",
        )
        _merge_counts(
            """
            SELECT substr(consent_updated_at, 1, 10) AS d, COUNT(*) AS c
            FROM users
            WHERE consent_updated_at IS NOT NULL AND consent_status = 'opted_out'
            GROUP BY d
            """,
            key="opted_out",
        )
        _merge_counts(
            """
            SELECT substr(received_at, 1, 10) AS d, COUNT(*) AS c
            FROM raw_messages
            WHERE direction = 'inbound'
            GROUP BY d
            """,
            key="inbound_messages",
        )
        _merge_counts(
            """
            SELECT substr(parsed_at, 1, 10) AS d, COUNT(*) AS c
            FROM parsed_events
            WHERE event_type = 'borrow_intent' AND intent = 1
            GROUP BY d
            """,
            key="borrow_intents",
        )
        _merge_counts(
            """
            SELECT substr(sent_at, 1, 10) AS d, COUNT(*) AS c
            FROM nudges
            GROUP BY d
            """,
            key="nudges_total",
        )
        _merge_counts(
            """
            SELECT substr(sent_at, 1, 10) AS d, COUNT(*) AS c
            FROM nudges
            WHERE nudge_type = 'alert'
            GROUP BY d
            """,
            key="nudges_alert",
        )
        _merge_counts(
            """
            SELECT substr(sent_at, 1, 10) AS d, COUNT(*) AS c
            FROM nudges
            WHERE nudge_type = 'suggest_lender'
            GROUP BY d
            """,
            key="nudges_suggest_lender",
        )
        _merge_counts(
            """
            SELECT substr(sent_at, 1, 10) AS d, COUNT(*) AS c
            FROM nudges
            WHERE nudge_type = 'education'
            GROUP BY d
            """,
            key="nudges_education",
        )
        _merge_counts(
            """
            SELECT substr(n.sent_at, 1, 10) AS d, COUNT(DISTINCT n.id) AS c
            FROM nudges n
            WHERE EXISTS (
                SELECT 1
                FROM raw_messages rm
                WHERE rm.user_id = n.user_id
                    AND rm.direction = 'inbound'
                    AND rm.received_at > n.sent_at
                    AND rm.received_at <= datetime(n.sent_at, '+48 hours')
            )
            GROUP BY d
            """,
            key="engaged_nudges_48h",
        )
        _merge_counts(
            """
            SELECT substr(reported_at, 1, 10) AS d, COUNT(*) AS c
            FROM self_reported_switches
            GROUP BY d
            """,
            key="switches_reported",
        )

        daily_csv = io.StringIO()
        daily_writer = csv.writer(daily_csv)
        daily_writer.writerow(
            [
                "date",
                "new_users",
                "opted_in",
                "opted_out",
                "inbound_messages",
                "borrow_intents",
                "nudges_total",
                "nudges_alert",
                "nudges_suggest_lender",
                "nudges_education",
                "engaged_nudges_48h",
                "switches_reported",
            ]
        )
        for d in days:
            key = d.isoformat()
            m = by_day.get(key, {})
            daily_writer.writerow(
                [
                    key,
                    int(m.get("new_users", 0)),
                    int(m.get("opted_in", 0)),
                    int(m.get("opted_out", 0)),
                    int(m.get("inbound_messages", 0)),
                    int(m.get("borrow_intents", 0)),
                    int(m.get("nudges_total", 0)),
                    int(m.get("nudges_alert", 0)),
                    int(m.get("nudges_suggest_lender", 0)),
                    int(m.get("nudges_education", 0)),
                    int(m.get("engaged_nudges_48h", 0)),
                    int(m.get("switches_reported", 0)),
                ]
            )

        user_rows = conn.execute(
            "SELECT id, phone_e164, district, consent_status, created_at FROM users ORDER BY id ASC"
        ).fetchall()

        inbound_by_user = {
            int(r["user_id"]): int(r["c"])
            for r in conn.execute(
                "SELECT user_id, COUNT(*) AS c FROM raw_messages WHERE direction = 'inbound' GROUP BY user_id"
            ).fetchall()
        }
        borrow_by_user = {
            int(r["user_id"]): int(r["c"])
            for r in conn.execute(
                """
                SELECT user_id, COUNT(*) AS c
                FROM parsed_events
                WHERE event_type = 'borrow_intent' AND intent = 1
                GROUP BY user_id
                """
            ).fetchall()
        }
        nudges_by_user = {
            int(r["user_id"]): int(r["c"])
            for r in conn.execute("SELECT user_id, COUNT(*) AS c FROM nudges GROUP BY user_id").fetchall()
        }
        engaged_by_user = {
            int(r["user_id"]): int(r["c"])
            for r in conn.execute(
                """
                SELECT n.user_id AS user_id, COUNT(DISTINCT n.id) AS c
                FROM nudges n
                WHERE EXISTS (
                    SELECT 1
                    FROM raw_messages rm
                    WHERE rm.user_id = n.user_id
                        AND rm.direction = 'inbound'
                        AND rm.received_at > n.sent_at
                        AND rm.received_at <= datetime(n.sent_at, '+48 hours')
                )
                GROUP BY n.user_id
                """
            ).fetchall()
        }
        switches_by_user = {
            int(r["user_id"]): int(r["c"])
            for r in conn.execute("SELECT user_id, COUNT(*) AS c FROM self_reported_switches GROUP BY user_id").fetchall()
        }
        last_inbound_by_user = {
            int(r["user_id"]): str(r["ts"])
            for r in conn.execute(
                """
                SELECT user_id, MAX(received_at) AS ts
                FROM raw_messages
                WHERE direction = 'inbound'
                GROUP BY user_id
                """
            ).fetchall()
        }

        user_csv = io.StringIO()
        user_writer = csv.writer(user_csv)
        user_writer.writerow(
            [
                "anon_user_id",
                "district",
                "consent_status",
                "created_date",
                "last_inbound_date",
                "inbound_messages",
                "borrow_intents",
                "nudges_sent",
                "engaged_nudges_48h",
                "switches_reported",
            ]
        )
        for r in user_rows:
            user_id = int(r["id"])
            phone = str(r["phone_e164"])
            anon_user_id = _blake_id(salt=salt, value=phone)
            created_at = str(r["created_at"] or "")
            last_inbound = str(last_inbound_by_user.get(user_id) or "")
            user_writer.writerow(
                [
                    anon_user_id,
                    str(r["district"] or ""),
                    str(r["consent_status"] or ""),
                    created_at[:10],
                    last_inbound[:10],
                    int(inbound_by_user.get(user_id, 0)),
                    int(borrow_by_user.get(user_id, 0)),
                    int(nudges_by_user.get(user_id, 0)),
                    int(engaged_by_user.get(user_id, 0)),
                    int(switches_by_user.get(user_id, 0)),
                ]
            )

        policy_csv = io.StringIO()
        policy_writer = csv.writer(policy_csv)
        policy_writer.writerow(["policy_name", "policy_version", "nudge_type", "trigger", "count"])
        for r in conn.execute(
            """
            SELECT COALESCE(policy_name, '') AS policy_name,
                   COALESCE(policy_version, '') AS policy_version,
                   COALESCE(nudge_type, '') AS nudge_type,
                   COALESCE(trigger, 'event') AS trigger,
                   COUNT(*) AS c
            FROM nudges
            GROUP BY policy_name, policy_version, nudge_type, trigger
            ORDER BY c DESC
            """
        ).fetchall():
            policy_writer.writerow(
                [
                    str(r["policy_name"]),
                    str(r["policy_version"]),
                    str(r["nudge_type"]),
                    str(r["trigger"]),
                    int(r["c"] or 0),
                ]
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("daily_metrics.csv", daily_csv.getvalue().encode("utf-8"))
            zf.writestr("user_metrics.csv", user_csv.getvalue().encode("utf-8"))
            zf.writestr("policy_usage.csv", policy_csv.getvalue().encode("utf-8"))

        return ExportBundle(
            filename="nudge_metrics.zip",
            content_type="application/zip",
            data=buf.getvalue(),
        )
    finally:
        conn.close()

