from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


@dataclass(frozen=True)
class Config:
    port: int
    railway_environment: str | None
    db_path: str

    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_validate_signature: bool

    claude_api_key: str | None
    claude_model: str

    nudge_cooldown_minutes: int
    nudge_max_per_day: int
    nudge_max_per_week: int
    baseline_policy_enabled: bool
    policy_mode: str = "off"
    rl_model_dir: str | None = None
    rl_model_path: str | None = None
    rl_active_version: str | None = None
    twilio_from_addr: str | None = None
    default_channel: str = "whatsapp"
    admin_token: str | None = None
    anon_salt: str | None = None
    verbose_replies: bool = False
    rl_rollout_pct: int = 0
    support_contact: str | None = None
    default_language: str = "en"
    mfi_dataset_path: str | None = None
    mfi_autoload: bool = True

    @staticmethod
    def from_env() -> "Config":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

        port_raw = _env("PORT", "5000")
        try:
            port = int(port_raw or "5000")
        except ValueError:
            port = 5000

        railway_environment = _env("RAILWAY_ENVIRONMENT")
        db_path = _env("NUDGE_DB_PATH") or _env("SQLITE_PATH")
        if not db_path:
            db_path = os.path.join(os.getcwd(), "data", "nudge.sqlite3")

        validate_raw = (_env("TWILIO_VALIDATE_SIGNATURE", "true") or "true").lower()
        twilio_validate_signature = validate_raw not in {"0", "false", "no"}

        claude_api_key = _env("CLAUDE_API_KEY") or _env("ANTHROPIC_API_KEY")

        cooldown_raw = _env("NUDGE_COOLDOWN_MINUTES", "360") or "360"
        max_day_raw = _env("NUDGE_MAX_PER_DAY", "2") or "2"
        max_week_raw = _env("NUDGE_MAX_PER_WEEK", "5") or "5"
        try:
            nudge_cooldown_minutes = max(0, int(cooldown_raw))
        except ValueError:
            nudge_cooldown_minutes = 360
        try:
            nudge_max_per_day = max(0, int(max_day_raw))
        except ValueError:
            nudge_max_per_day = 2
        try:
            nudge_max_per_week = max(0, int(max_week_raw))
        except ValueError:
            nudge_max_per_week = 5

        baseline_raw = (_env("NUDGE_BASELINE_POLICY_ENABLED", "false") or "false").lower()
        baseline_policy_enabled = baseline_raw in {"1", "true", "yes", "on"}

        policy_mode = (_env("NUDGE_POLICY_MODE") or "").strip().lower()
        if policy_mode == "":
            policy_mode = "baseline" if baseline_policy_enabled else "off"

        verbose_raw = (_env("NUDGE_VERBOSE_REPLIES", "false") or "false").strip().lower()
        verbose_replies = verbose_raw in {"1", "true", "yes", "on"}

        rollout_raw = (_env("NUDGE_RL_ROLLOUT_PCT", "0") or "0").strip()
        try:
            rl_rollout_pct = max(0, min(100, int(rollout_raw)))
        except ValueError:
            rl_rollout_pct = 0

        default_language = (_env("NUDGE_DEFAULT_LANGUAGE", "en") or "en").strip().lower()
        if default_language not in {"en", "hi", "hinglish"}:
            default_language = "en"

        mfi_dataset_path = (_env("NUDGE_MFI_DATASET_PATH") or "").strip() or None
        if mfi_dataset_path is None:
            mfi_dataset_path = os.path.join(os.getcwd(), "datasets", "mfi_rates.csv")

        mfi_autoload_raw = (_env("NUDGE_MFI_AUTOLOAD", "true") or "true").strip().lower()
        mfi_autoload = mfi_autoload_raw in {"1", "true", "yes", "on"}

        return Config(
            port=port,
            railway_environment=railway_environment,
            db_path=db_path,
            twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
            twilio_validate_signature=twilio_validate_signature,
            claude_api_key=claude_api_key,
            claude_model=_env("CLAUDE_MODEL", "claude-3-5-sonnet-latest") or "claude-3-5-sonnet-latest",
            nudge_cooldown_minutes=nudge_cooldown_minutes,
            nudge_max_per_day=nudge_max_per_day,
            nudge_max_per_week=nudge_max_per_week,
            baseline_policy_enabled=baseline_policy_enabled,
            policy_mode=policy_mode,
            rl_model_dir=_env("NUDGE_RL_MODEL_DIR"),
            rl_model_path=_env("NUDGE_RL_MODEL_PATH"),
            rl_active_version=_env("NUDGE_RL_ACTIVE_VERSION"),
            twilio_from_addr=_env("TWILIO_FROM") or _env("TWILIO_FROM_ADDR"),
            default_channel=_env("NUDGE_DEFAULT_CHANNEL", "whatsapp") or "whatsapp",
            admin_token=_env("NUDGE_ADMIN_TOKEN"),
            anon_salt=_env("NUDGE_ANON_SALT"),
            verbose_replies=bool(verbose_replies),
            rl_rollout_pct=int(rl_rollout_pct),
            support_contact=_env("NUDGE_SUPPORT_CONTACT"),
            default_language=str(default_language),
            mfi_dataset_path=str(mfi_dataset_path) if mfi_dataset_path is not None else None,
            mfi_autoload=bool(mfi_autoload),
        )
