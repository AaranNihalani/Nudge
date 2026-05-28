from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class DbInfo:
    path: str
    schema_version: int


_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            phone_e164 TEXT NOT NULL UNIQUE,
            consent_status TEXT NOT NULL DEFAULT 'unknown',
            consent_updated_at TEXT,
            district TEXT,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );

        CREATE TABLE IF NOT EXISTS raw_messages (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            direction TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'whatsapp',
            from_addr TEXT,
            to_addr TEXT,
            body TEXT NOT NULL,
            twilio_message_sid TEXT,
            payload_json TEXT,
            received_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_messages_twilio_message_sid
            ON raw_messages(twilio_message_sid)
            WHERE twilio_message_sid IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_raw_messages_user_received_at
            ON raw_messages(user_id, received_at);

        CREATE TABLE IF NOT EXISTS parsed_events (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            raw_message_id INTEGER REFERENCES raw_messages(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            event_json TEXT NOT NULL,
            confidence REAL,
            model TEXT,
            parsed_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );

        CREATE INDEX IF NOT EXISTS idx_parsed_events_user_parsed_at
            ON parsed_events(user_id, parsed_at);
        CREATE INDEX IF NOT EXISTS idx_parsed_events_type_parsed_at
            ON parsed_events(event_type, parsed_at);

        CREATE TABLE IF NOT EXISTS nudges (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            parsed_event_id INTEGER REFERENCES parsed_events(id) ON DELETE SET NULL,
            nudge_type TEXT NOT NULL,
            content TEXT NOT NULL,
            policy_name TEXT,
            policy_version TEXT,
            twilio_message_sid TEXT,
            sent_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            delivery_status TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_nudges_twilio_message_sid
            ON nudges(twilio_message_sid)
            WHERE twilio_message_sid IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_nudges_user_sent_at
            ON nudges(user_id, sent_at);

        CREATE TABLE IF NOT EXISTS self_reported_switches (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_raw_message_id INTEGER REFERENCES raw_messages(id) ON DELETE SET NULL,
            from_lender TEXT,
            to_lender TEXT,
            reported_rate_old REAL,
            reported_rate_new REAL,
            switched_at TEXT,
            reported_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            notes TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_switches_user_reported_at
            ON self_reported_switches(user_id, reported_at);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS mfi_districts (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS mfi_lenders (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS mfi_rates (
            id INTEGER PRIMARY KEY,
            district_id INTEGER NOT NULL REFERENCES mfi_districts(id) ON DELETE CASCADE,
            lender_id INTEGER NOT NULL REFERENCES mfi_lenders(id) ON DELETE CASCADE,
            rate_apr REAL NOT NULL,
            effective_date TEXT,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            UNIQUE(district_id, lender_id)
        );

        CREATE INDEX IF NOT EXISTS idx_mfi_rates_district_rate
            ON mfi_rates(district_id, rate_apr);
        CREATE INDEX IF NOT EXISTS idx_mfi_rates_rate
            ON mfi_rates(rate_apr);
        """,
    ),
    (
        3,
        """
        ALTER TABLE parsed_events ADD COLUMN intent INTEGER;
        ALTER TABLE parsed_events ADD COLUMN amount_inr REAL;
        ALTER TABLE parsed_events ADD COLUMN tenure_days INTEGER;
        ALTER TABLE parsed_events ADD COLUMN interest_rate_apr REAL;
        ALTER TABLE parsed_events ADD COLUMN lender_name TEXT;
        ALTER TABLE parsed_events ADD COLUMN lender_type TEXT;
        ALTER TABLE parsed_events ADD COLUMN negotiation_stage TEXT;

        CREATE INDEX IF NOT EXISTS idx_parsed_events_intent_parsed_at
            ON parsed_events(intent, parsed_at);
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS system_kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );

        CREATE TABLE IF NOT EXISTS admin_runs (
            id INTEGER PRIMARY KEY,
            run_type TEXT NOT NULL,
            run_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'started',
            details_json TEXT,
            started_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            finished_at TEXT,
            UNIQUE(run_type, run_date)
        );

        ALTER TABLE nudges ADD COLUMN trigger TEXT NOT NULL DEFAULT 'event';
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            districts_prefix TEXT,
            districts_offset INTEGER NOT NULL DEFAULT 0,
            districts_page_size INTEGER NOT NULL DEFAULT 30,
            borrow_draft_json TEXT,
            borrow_source_raw_message_id INTEGER REFERENCES raw_messages(id) ON DELETE SET NULL,
            borrow_model TEXT,
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );
        """,
    ),
    (
        6,
        """
        ALTER TABLE user_sessions ADD COLUMN language TEXT;

        CREATE TABLE IF NOT EXISTS user_actions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_raw_message_id INTEGER REFERENCES raw_messages(id) ON DELETE SET NULL,
            action_type TEXT NOT NULL,
            lender TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );

        CREATE INDEX IF NOT EXISTS idx_user_actions_user_created_at
            ON user_actions(user_id, created_at);
        """,
    ),
    (
        7,
        """
        ALTER TABLE user_sessions ADD COLUMN lender_options_json TEXT;
        ALTER TABLE user_sessions ADD COLUMN lender_options_updated_at TEXT;
        """,
    ),
    (
        8,
        """
        ALTER TABLE user_sessions ADD COLUMN selected_lender_option_json TEXT;
        ALTER TABLE user_sessions ADD COLUMN selected_lender_rank INTEGER;
        ALTER TABLE user_sessions ADD COLUMN selected_lender_updated_at TEXT;
        """,
    ),
]


def connect(db_path: str, *, timeout_seconds: int = 30) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=timeout_seconds, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def init_and_migrate(db_path: str, *, timeout_seconds: int = 30, attempts: int = 6) -> DbInfo:
    abs_path = os.path.abspath(db_path)
    parent = os.path.dirname(abs_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        conn = connect(abs_path, timeout_seconds=timeout_seconds)
        try:
            conn.execute("BEGIN IMMEDIATE")
            current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for version, sql in _MIGRATIONS:
                if version <= current_version:
                    continue
                conn.executescript(sql)
                conn.execute(f"PRAGMA user_version = {version}")
                current_version = version
            conn.commit()
            return DbInfo(path=abs_path, schema_version=current_version)
        except sqlite3.OperationalError as e:
            conn.rollback()
            last_error = e
            msg = str(e).lower()
            if attempt >= max(1, int(attempts)) - 1:
                break
            if "locked" not in msg and "busy" not in msg:
                raise
            time.sleep(min(2.0, 0.1 * (2**attempt)))
        except Exception as e:
            conn.rollback()
            last_error = e
            raise
        finally:
            conn.close()

    if last_error is not None:
        raise last_error
    raise RuntimeError("init_and_migrate_failed")
