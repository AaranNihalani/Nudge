from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .nudge_content import alert_message, education_message, suggest_lender_message
from .policy_baseline import PolicyDecision, decide_baseline
from .rl_env import action_space
from .state import UserState


def _parse_sqlite_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class LoadedRlPolicy:
    model: Any
    encoder: Any
    horizon_days: int
    policy_name: str
    policy_version: str

    def predict_action(self, *, obs: dict[str, Any]) -> str:
        vec = self.encoder.encode(obs)
        action_index, _ = self.model.predict(vec, deterministic=True)
        if isinstance(action_index, (list, tuple)) and action_index:
            action_index = action_index[0]
        try:
            action_index = int(action_index)
        except Exception:
            action_index = 0
        return str(action_space()[int(action_index)])


_RL_LOCK = threading.Lock()
_RL_CACHE: dict[str, LoadedRlPolicy] = {}


def _resolve_active_rl_version(cfg: Config, conn) -> str | None:
    if cfg.rl_active_version:
        return str(cfg.rl_active_version).strip() or None
    row = conn.execute("SELECT value FROM system_kv WHERE key = 'rl_active_version'").fetchone()
    if row is None:
        return None
    return str(row["value"]).strip() or None


def _resolve_model_path(cfg: Config, *, version: str | None) -> tuple[str | None, str | None]:
    if cfg.rl_model_path:
        path = str(cfg.rl_model_path).strip() or None
        if path is None:
            return None, None
        return path, version or Path(path).parent.name
    if not cfg.rl_model_dir or not version:
        return None, None
    base = Path(str(cfg.rl_model_dir))
    model_path = base / str(version) / "model.zip"
    if not model_path.exists():
        return None, None
    return str(model_path), str(version)


def _load_rl_policy(cfg: Config, conn) -> LoadedRlPolicy | None:
    version = _resolve_active_rl_version(cfg, conn)
    model_path, resolved_version = _resolve_model_path(cfg, version=version)
    if not model_path or not resolved_version:
        return None

    cache_key = f"{model_path}::{resolved_version}"
    with _RL_LOCK:
        cached = _RL_CACHE.get(cache_key)
        if cached is not None:
            return cached

        try:
            from stable_baselines3 import PPO
        except Exception:
            return None
        try:
            from .rl_sb3 import ObsEncoder
        except Exception:
            return None

        horizon_days = 120
        train_config_path = Path(model_path).with_name("train_config.json")
        if train_config_path.exists():
            try:
                payload = json.loads(train_config_path.read_text(encoding="utf-8"))
                horizon_days = int(payload.get("horizon_days") or horizon_days)
            except Exception:
                horizon_days = int(horizon_days)

        try:
            model = PPO.load(model_path)
        except Exception:
            return None

        loaded = LoadedRlPolicy(
            model=model,
            encoder=ObsEncoder(horizon_days=int(horizon_days)),
            horizon_days=int(horizon_days),
            policy_name="ppo",
            policy_version=str(resolved_version),
        )
        _RL_CACHE[cache_key] = loaded
        return loaded


def _state_to_rl_obs(conn, *, state: UserState, horizon_days: int) -> dict[str, Any]:
    row = conn.execute("SELECT created_at FROM users WHERE id = ?", (int(state.user_id),)).fetchone()
    day = 0
    if row is not None and row["created_at"]:
        try:
            created_at = _parse_sqlite_ts(str(row["created_at"]))
            day = int(
                max(
                    0.0,
                    (state.now.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds() / 86400.0,
                )
            )
        except Exception:
            day = 0

    engagement_rate = state.engagement.engagement_rate_30d
    return {
        "day": int(min(max(0, day), int(horizon_days))),
        "stage": str(state.borrow.last_stage or "none"),
        "days_since_borrow": state.days_since_borrow,
        "implied_apr": state.implied_apr,
        "debt_burden_proxy": state.debt_burden_proxy,
        "nudges_7d": int(state.nudges.count_7d),
        "nudges_30d": int(state.nudges.count_30d),
        "engagement_rate_30d": engagement_rate,
        "opted_out": state.consent_status != "opted_in",
    }


def _decision_for_action(
    conn,
    *,
    state: UserState,
    action: str,
    policy_name: str,
    policy_version: str,
) -> PolicyDecision:
    district = state.district
    if state.consent_status != "opted_in":
        d = decide_baseline(conn, state=state)
        return PolicyDecision(
            action=d.action,
            nudge_type=d.nudge_type,
            content=d.content,
            policy_name=d.policy_name,
            policy_version=d.policy_version,
            parsed_event_id=d.parsed_event_id,
        )

    if not district:
        d = decide_baseline(conn, state=state)
        return PolicyDecision(
            action=d.action,
            nudge_type=d.nudge_type,
            content=d.content,
            policy_name=d.policy_name,
            policy_version=d.policy_version,
            parsed_event_id=d.parsed_event_id,
        )

    if action == "alert":
        if state.implied_apr is not None:
            return PolicyDecision(
                action="alert",
                nudge_type="alert",
                content=alert_message(conn, district=district, quoted_apr=float(state.implied_apr), current_lender=None, n=3),
                policy_name=policy_name,
                policy_version=policy_version,
                parsed_event_id=state.borrow.last_intent_event_id,
            )
        action = "suggest_lender"

    if action == "suggest_lender":
        return PolicyDecision(
            action="suggest_lender",
            nudge_type="suggest_lender",
            content=suggest_lender_message(
                conn,
                district=district,
                current_rate=float(state.implied_apr) if state.implied_apr is not None else None,
                exclude_lender=None,
                n=3,
            ),
            policy_name=policy_name,
            policy_version=policy_version,
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    if action == "education":
        return PolicyDecision(
            action="education",
            nudge_type="education",
            content=education_message(district=district),
            policy_name=policy_name,
            policy_version=policy_version,
            parsed_event_id=state.borrow.last_intent_event_id,
        )

    d = decide_baseline(conn, state=state)
    return PolicyDecision(
        action="wait",
        nudge_type=None,
        content=d.content,
        policy_name=policy_name,
        policy_version=policy_version,
        parsed_event_id=d.parsed_event_id,
    )


def decide_policy(conn, *, cfg: Config, state: UserState) -> PolicyDecision:
    mode = str(cfg.policy_mode or "off").strip().lower()
    if mode == "baseline":
        return decide_baseline(conn, state=state)

    if mode in {"rl", "auto"}:
        rl = _load_rl_policy(cfg, conn)
        if rl is not None and state.consent_status == "opted_in" and state.district:
            obs = _state_to_rl_obs(conn, state=state, horizon_days=int(rl.horizon_days))
            try:
                action = rl.predict_action(obs=obs)
                return _decision_for_action(
                    conn,
                    state=state,
                    action=action,
                    policy_name=rl.policy_name,
                    policy_version=rl.policy_version,
                )
            except Exception:
                return decide_baseline(conn, state=state)
        return decide_baseline(conn, state=state)

    return decide_baseline(conn, state=state)
