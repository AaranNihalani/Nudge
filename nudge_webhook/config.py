from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if (value is None or value == "") else value


def _bool_env(name: str, default: bool) -> bool:
    raw = (_env(name) or "").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _int_env(name: str, default: int, *, lo: int | None = None, hi: int | None = None) -> int:
    try:
        v = int(_env(name) or default)
    except ValueError:
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


@dataclass(frozen=True)
class Config:
    port: int = 5000
    railway_environment: str | None = None
    db_path: str = "data/nudge.sqlite3"

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_validate_signature: bool = True
    twilio_from_addr: str | None = None

    claude_api_key: str | None = None
    claude_model: str = "claude-sonnet-4-6"
    claude_timeout_seconds: float = 8.0
    claude_attempts: int = 1
    debug_claude: bool = False

    nudge_cooldown_minutes: int = 360
    nudge_max_per_day: int = 2
    nudge_max_per_week: int = 5
    baseline_policy_enabled: bool = False
    policy_mode: str = "off"

    mfi_dataset_path: str | None = None
    mfi_autoload: bool = True

    admin_token: str | None = None
    anon_salt: str | None = None
    verbose_replies: bool = False

    @staticmethod
    def from_env() -> Config:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass

        db_path = _env("NUDGE_DB_PATH") or _env("SQLITE_PATH")
        if not db_path:
            db_path = (
                "/tmp/nudge.sqlite3"
                if _env("VERCEL")
                else os.path.join(os.getcwd(), "data", "nudge.sqlite3")
            )

        baseline_policy_enabled = _bool_env("NUDGE_BASELINE_POLICY_ENABLED", False)
        policy_mode = (_env("NUDGE_POLICY_MODE") or "").strip().lower()
        if not policy_mode:
            policy_mode = "baseline" if baseline_policy_enabled else "off"

        mfi_dataset_path = (_env("NUDGE_MFI_DATASET_PATH") or "").strip() or None
        if mfi_dataset_path is None:
            mfi_dataset_path = os.path.join(os.getcwd(), "datasets", "mfi_rates.csv")

        return Config(
            port=_int_env("PORT", 5000),
            railway_environment=_env("RAILWAY_ENVIRONMENT"),
            db_path=db_path,
            twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
            twilio_validate_signature=_bool_env("TWILIO_VALIDATE_SIGNATURE", True),
            twilio_from_addr=_env("TWILIO_FROM") or _env("TWILIO_FROM_ADDR"),
            claude_api_key=_env("CLAUDE_API_KEY") or _env("ANTHROPIC_API_KEY"),
            claude_model=_env("CLAUDE_MODEL", "claude-sonnet-4-6") or "claude-sonnet-4-6",
            claude_timeout_seconds=min(max(1.0, float(_env("NUDGE_CLAUDE_TIMEOUT_SECONDS") or 8)), 12.0),
            claude_attempts=min(max(1, _int_env("NUDGE_CLAUDE_ATTEMPTS", 1)), 3),
            debug_claude=_bool_env("NUDGE_DEBUG_CLAUDE", False),
            nudge_cooldown_minutes=_int_env("NUDGE_COOLDOWN_MINUTES", 360, lo=0),
            nudge_max_per_day=_int_env("NUDGE_MAX_PER_DAY", 2, lo=0),
            nudge_max_per_week=_int_env("NUDGE_MAX_PER_WEEK", 5, lo=0),
            baseline_policy_enabled=baseline_policy_enabled,
            policy_mode=policy_mode,
            mfi_dataset_path=str(mfi_dataset_path) if mfi_dataset_path else None,
            mfi_autoload=_bool_env("NUDGE_MFI_AUTOLOAD", True),
            admin_token=_env("NUDGE_ADMIN_TOKEN"),
            anon_salt=_env("NUDGE_ANON_SALT"),
            verbose_replies=_bool_env("NUDGE_VERBOSE_REPLIES", False),
        )
