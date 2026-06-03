"""Database layer — SQLite for local dev, PostgreSQL when DATABASE_URL is set."""
from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

_DATABASE_URL: str | None = os.environ.get("DATABASE_URL")
BACKEND: str = "postgres" if _DATABASE_URL else "sqlite"

# Timestamp format used throughout the app
TS_FMT = "%Y-%m-%d %H:%M:%S"

# PostgreSQL equivalent of SQLite's CURRENT_TIMESTAMP that produces the same format
_PG_NOW = "to_char(now() at time zone 'utc', 'YYYY-MM-DD HH24:MI:SS')"

_SCHEMA_VERSION = 9


@dataclass(frozen=True)
class DbInfo:
    path: str
    schema_version: int


# ---------------------------------------------------------------------------
# Cursor adapter
# ---------------------------------------------------------------------------

class _NullCursor:
    """Returned for no-op statements (e.g. BEGIN in PostgreSQL)."""
    lastrowid: int | None = None

    def fetchone(self) -> dict[str, Any] | None:
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return []


class _Cursor:
    """Normalises SQLite and psycopg2 cursors to a consistent dict-based interface."""

    __slots__ = ("_raw", "_last_id")

    def __init__(self, raw: Any, *, last_id: Any = None) -> None:
        self._raw = raw
        self._last_id = last_id

    @property
    def lastrowid(self) -> int | None:
        if self._last_id is not None:
            return int(self._last_id)
        lid = getattr(self._raw, "lastrowid", None)
        return int(lid) if lid is not None else None

    def fetchone(self) -> dict[str, Any] | None:
        row = self._raw.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(r) for r in (self._raw.fetchall() or [])]


# ---------------------------------------------------------------------------
# Connection adapter
# ---------------------------------------------------------------------------

_COLLATE_NOCASE_RE = re.compile(r"\s*COLLATE\s+NOCASE\b", re.IGNORECASE)
_BEGIN_RE = re.compile(r"^\s*BEGIN(\s+IMMEDIATE)?\s*;?\s*$", re.IGNORECASE)
_PRAGMA_RE = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)


class Connection:
    """Backend-agnostic wrapper around SQLite or psycopg2 connection."""

    __slots__ = ("_conn", "backend")

    def __init__(self, raw_conn: Any, *, backend: str) -> None:
        self._conn = raw_conn
        self.backend = backend

    def _adapt(self, sql: str) -> str:
        """Translate SQLite SQL to PostgreSQL."""
        s = sql.replace("?", "%s")
        s = s.replace("CURRENT_TIMESTAMP", _PG_NOW)
        s = _COLLATE_NOCASE_RE.sub("", s)
        return s

    def execute(self, sql: str, params: Any = None) -> _Cursor | _NullCursor:
        stripped = sql.strip()

        if self.backend == "postgres":
            # BEGIN and PRAGMA are no-ops — psycopg2 manages transactions implicitly
            if _BEGIN_RE.match(stripped) or _PRAGMA_RE.match(stripped):
                return _NullCursor()

            adapted = self._adapt(stripped)
            import psycopg2.extras  # type: ignore
            upper = adapted.lstrip().upper()
            is_insert = upper.startswith("INSERT") and "RETURNING" not in upper

            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            last_id: Any = None

            if is_insert:
                cur.execute(adapted.rstrip(";") + " RETURNING id", params or ())
                row = cur.fetchone()
                last_id = row["id"] if row else None
            else:
                cur.execute(adapted, params or ())

            return _Cursor(cur, last_id=last_id)

        # SQLite path — pass through unchanged
        cur = self._conn.execute(sql, params or ())
        return _Cursor(cur)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SQLite migrations
# ---------------------------------------------------------------------------

_SQLITE_MIGRATIONS: list[tuple[int, str]] = [
    (1, """
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
        CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_messages_twilio_sid
            ON raw_messages(twilio_message_sid) WHERE twilio_message_sid IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_raw_messages_user_received
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
        CREATE INDEX IF NOT EXISTS idx_parsed_events_user_parsed
            ON parsed_events(user_id, parsed_at);
        CREATE INDEX IF NOT EXISTS idx_parsed_events_type_parsed
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
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nudges_twilio_sid
            ON nudges(twilio_message_sid) WHERE twilio_message_sid IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_nudges_user_sent
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
        CREATE INDEX IF NOT EXISTS idx_switches_user_reported
            ON self_reported_switches(user_id, reported_at);
    """),
    (2, """
        CREATE TABLE IF NOT EXISTS mfi_districts (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS mfi_lenders (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
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
        CREATE INDEX IF NOT EXISTS idx_mfi_rates_district_rate ON mfi_rates(district_id, rate_apr);
        CREATE INDEX IF NOT EXISTS idx_mfi_rates_rate ON mfi_rates(rate_apr);
    """),
    (3, """
        ALTER TABLE parsed_events ADD COLUMN intent INTEGER;
        ALTER TABLE parsed_events ADD COLUMN amount_inr REAL;
        ALTER TABLE parsed_events ADD COLUMN tenure_days INTEGER;
        ALTER TABLE parsed_events ADD COLUMN interest_rate_apr REAL;
        ALTER TABLE parsed_events ADD COLUMN lender_name TEXT;
        ALTER TABLE parsed_events ADD COLUMN lender_type TEXT;
        ALTER TABLE parsed_events ADD COLUMN negotiation_stage TEXT;
        CREATE INDEX IF NOT EXISTS idx_parsed_events_intent_parsed
            ON parsed_events(intent, parsed_at);
    """),
    (4, """
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
    """),
    (5, """
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
    """),
    (6, """
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
        CREATE INDEX IF NOT EXISTS idx_user_actions_user_created
            ON user_actions(user_id, created_at);
    """),
    (7, """
        ALTER TABLE user_sessions ADD COLUMN lender_options_json TEXT;
        ALTER TABLE user_sessions ADD COLUMN lender_options_updated_at TEXT;
    """),
    (8, """
        ALTER TABLE user_sessions ADD COLUMN selected_lender_option_json TEXT;
        ALTER TABLE user_sessions ADD COLUMN selected_lender_rank INTEGER;
        ALTER TABLE user_sessions ADD COLUMN selected_lender_updated_at TEXT;
    """),
    (9, """
        ALTER TABLE users ADD COLUMN caste TEXT;
        ALTER TABLE users ADD COLUMN religion TEXT;
        ALTER TABLE users ADD COLUMN mpce_inr REAL;
        ALTER TABLE users ADD COLUMN household_size INTEGER;
        ALTER TABLE users ADD COLUMN land_acres REAL;
        ALTER TABLE users ADD COLUMN urban INTEGER;
        ALTER TABLE users ADD COLUMN profile_complete INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE user_sessions ADD COLUMN profile_step TEXT;
    """),
]


# ---------------------------------------------------------------------------
# PostgreSQL full schema (at version 9)
# ---------------------------------------------------------------------------

_PG_SCHEMA_STATEMENTS: list[str] = [
    "CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)",
    f"INSERT INTO _schema_version VALUES ({_SCHEMA_VERSION}) ON CONFLICT DO NOTHING",
    """CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        phone_e164 TEXT NOT NULL UNIQUE,
        consent_status TEXT NOT NULL DEFAULT 'unknown',
        consent_updated_at TEXT,
        district TEXT,
        caste TEXT,
        religion TEXT,
        mpce_inr DOUBLE PRECISION,
        household_size INTEGER,
        land_acres DOUBLE PRECISION,
        urban INTEGER,
        profile_complete INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        updated_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """
    )""",
    """CREATE TABLE IF NOT EXISTS raw_messages (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        direction TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'whatsapp',
        from_addr TEXT,
        to_addr TEXT,
        body TEXT NOT NULL,
        twilio_message_sid TEXT,
        payload_json TEXT,
        received_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_messages_twilio_sid ON raw_messages(twilio_message_sid) WHERE twilio_message_sid IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_user_received ON raw_messages(user_id, received_at)",
    """CREATE TABLE IF NOT EXISTS parsed_events (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        raw_message_id BIGINT REFERENCES raw_messages(id) ON DELETE SET NULL,
        event_type TEXT NOT NULL,
        event_json TEXT NOT NULL,
        confidence DOUBLE PRECISION,
        model TEXT,
        parsed_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        intent INTEGER,
        amount_inr DOUBLE PRECISION,
        tenure_days INTEGER,
        interest_rate_apr DOUBLE PRECISION,
        lender_name TEXT,
        lender_type TEXT,
        negotiation_stage TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_parsed_events_user_parsed ON parsed_events(user_id, parsed_at)",
    "CREATE INDEX IF NOT EXISTS idx_parsed_events_type_parsed ON parsed_events(event_type, parsed_at)",
    "CREATE INDEX IF NOT EXISTS idx_parsed_events_intent_parsed ON parsed_events(intent, parsed_at)",
    """CREATE TABLE IF NOT EXISTS nudges (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        parsed_event_id BIGINT REFERENCES parsed_events(id) ON DELETE SET NULL,
        nudge_type TEXT NOT NULL,
        content TEXT NOT NULL,
        policy_name TEXT,
        policy_version TEXT,
        twilio_message_sid TEXT,
        sent_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        delivery_status TEXT,
        trigger TEXT NOT NULL DEFAULT 'event'
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_nudges_twilio_sid ON nudges(twilio_message_sid) WHERE twilio_message_sid IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_nudges_user_sent ON nudges(user_id, sent_at)",
    """CREATE TABLE IF NOT EXISTS self_reported_switches (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source_raw_message_id BIGINT REFERENCES raw_messages(id) ON DELETE SET NULL,
        from_lender TEXT,
        to_lender TEXT,
        reported_rate_old DOUBLE PRECISION,
        reported_rate_new DOUBLE PRECISION,
        switched_at TEXT,
        reported_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        notes TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_switches_user_reported ON self_reported_switches(user_id, reported_at)",
    "CREATE TABLE IF NOT EXISTS mfi_districts (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE)",
    "CREATE TABLE IF NOT EXISTS mfi_lenders (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE)",
    """CREATE TABLE IF NOT EXISTS mfi_rates (
        id BIGSERIAL PRIMARY KEY,
        district_id BIGINT NOT NULL REFERENCES mfi_districts(id) ON DELETE CASCADE,
        lender_id BIGINT NOT NULL REFERENCES mfi_lenders(id) ON DELETE CASCADE,
        rate_apr DOUBLE PRECISION NOT NULL,
        effective_date TEXT,
        source TEXT,
        created_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        updated_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        UNIQUE(district_id, lender_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_mfi_rates_district_rate ON mfi_rates(district_id, rate_apr)",
    "CREATE INDEX IF NOT EXISTS idx_mfi_rates_rate ON mfi_rates(rate_apr)",
    """CREATE TABLE IF NOT EXISTS system_kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """
    )""",
    """CREATE TABLE IF NOT EXISTS admin_runs (
        id BIGSERIAL PRIMARY KEY,
        run_type TEXT NOT NULL,
        run_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'started',
        details_json TEXT,
        started_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        finished_at TEXT,
        UNIQUE(run_type, run_date)
    )""",
    """CREATE TABLE IF NOT EXISTS user_sessions (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        districts_prefix TEXT,
        districts_offset INTEGER NOT NULL DEFAULT 0,
        districts_page_size INTEGER NOT NULL DEFAULT 30,
        borrow_draft_json TEXT,
        borrow_source_raw_message_id BIGINT REFERENCES raw_messages(id) ON DELETE SET NULL,
        borrow_model TEXT,
        updated_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """,
        language TEXT,
        lender_options_json TEXT,
        lender_options_updated_at TEXT,
        selected_lender_option_json TEXT,
        selected_lender_rank INTEGER,
        selected_lender_updated_at TEXT,
        profile_step TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS user_actions (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source_raw_message_id BIGINT REFERENCES raw_messages(id) ON DELETE SET NULL,
        action_type TEXT NOT NULL,
        lender TEXT,
        details_json TEXT,
        created_at TEXT NOT NULL DEFAULT """ + f"{_PG_NOW}" + """
    )""",
    "CREATE INDEX IF NOT EXISTS idx_user_actions_user_created ON user_actions(user_id, created_at)",
]


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def connect(db_path: str, *, timeout_seconds: int = 30) -> Connection:
    if BACKEND == "postgres":
        return _pg_connect()
    return _sqlite_connect(db_path, timeout_seconds=timeout_seconds)


def _pg_connect() -> Connection:
    import psycopg2  # type: ignore
    assert _DATABASE_URL is not None
    raw = psycopg2.connect(_DATABASE_URL)
    raw.autocommit = False
    return Connection(raw, backend="postgres")


def _sqlite_connect(db_path: str, *, timeout_seconds: int = 30) -> Connection:
    raw = sqlite3.connect(db_path, timeout=timeout_seconds, check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    try:
        raw.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return Connection(raw, backend="sqlite")


# ---------------------------------------------------------------------------
# Initialisation & migration
# ---------------------------------------------------------------------------

def init_and_migrate(db_path: str, *, timeout_seconds: int = 30, attempts: int = 6) -> DbInfo:
    if BACKEND == "postgres":
        return _pg_init()
    return _sqlite_init(db_path, timeout_seconds=timeout_seconds, attempts=attempts)


def _pg_init() -> DbInfo:
    import psycopg2  # type: ignore
    assert _DATABASE_URL is not None
    raw = psycopg2.connect(_DATABASE_URL)
    raw.autocommit = False
    try:
        cur = raw.cursor()
        cur.execute("""
            SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = '_schema_version'
            )
        """)
        already_init: bool = cur.fetchone()[0]
        if not already_init:
            for stmt in _PG_SCHEMA_STATEMENTS:
                cur.execute(stmt)
        cur.execute("SELECT version FROM _schema_version ORDER BY version DESC LIMIT 1")
        row = cur.fetchone()
        version = row[0] if row else _SCHEMA_VERSION
        raw.commit()
        return DbInfo(path=_DATABASE_URL, schema_version=int(version))
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _sqlite_fallback_path(abs_path: str) -> str:
    base = os.path.basename(abs_path) or "nudge.sqlite3"
    if not base.endswith(".sqlite3"):
        base += ".sqlite3"
    return os.path.join("/tmp", base)


def _sqlite_init(db_path: str, *, timeout_seconds: int = 30, attempts: int = 6) -> DbInfo:
    abs_path = os.path.abspath(db_path)
    parent = os.path.dirname(abs_path)
    if parent and not os.path.exists(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            if getattr(e, "errno", None) in {30, 13}:
                abs_path = _sqlite_fallback_path(abs_path)
                fallback_parent = os.path.dirname(abs_path)
                if fallback_parent and not os.path.exists(fallback_parent):
                    os.makedirs(fallback_parent, exist_ok=True)
            else:
                raise

    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        raw = sqlite3.connect(abs_path, timeout=timeout_seconds, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        try:
            raw.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        try:
            raw.execute("BEGIN IMMEDIATE")
            version = int(raw.execute("PRAGMA user_version").fetchone()[0])
            for ver, sql in _SQLITE_MIGRATIONS:
                if ver <= version:
                    continue
                raw.executescript(sql)
                raw.execute(f"PRAGMA user_version = {ver}")
                version = ver
            raw.commit()
            return DbInfo(path=abs_path, schema_version=version)
        except sqlite3.OperationalError as e:
            raw.rollback()
            last_error = e
            msg = str(e).lower()
            if attempt >= attempts - 1 or ("locked" not in msg and "busy" not in msg):
                raise
            time.sleep(min(2.0, 0.1 * (2 ** attempt)))
        except Exception as e:
            raw.rollback()
            last_error = e
            raise
        finally:
            raw.close()

    if last_error:
        raise last_error
    raise RuntimeError("db_init_failed")
