from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from .rl_env import Action, NudgeRLEnv, RewardWeights, SurveyCalibration, action_space, stage_space

try:
    import gymnasium as gym
    from gymnasium import spaces
except Exception:
    gym = None
    spaces = None


class ObsEncoder:
    def __init__(self, *, horizon_days: int) -> None:
        self._horizon_days = max(1, int(horizon_days))
        self._stage_to_idx = {s: i for i, s in enumerate(stage_space())}

    @property
    def dim(self) -> int:
        return 14

    def encode(self, obs: dict[str, Any]) -> np.ndarray:
        day = float(obs.get("day") or 0.0) / float(self._horizon_days)
        stage = str(obs.get("stage") or "none")
        stage_vec = np.zeros((len(stage_space()),), dtype=np.float32)
        stage_vec[self._stage_to_idx.get(stage, 0)] = 1.0

        def _num(v: Any, *, scale: float = 1.0, missing: float = -1.0) -> float:
            if not isinstance(v, (int, float)):
                return float(missing)
            if scale == 0:
                return float(v)
            return float(v) / float(scale)

        days_since_borrow = _num(obs.get("days_since_borrow"), scale=float(self._horizon_days))
        implied_apr = _num(obs.get("implied_apr"), scale=200.0)
        debt_burden = _num(obs.get("debt_burden_proxy"), scale=100_000.0)
        nudges_7d = _num(obs.get("nudges_7d"), scale=10.0, missing=0.0)
        nudges_30d = _num(obs.get("nudges_30d"), scale=30.0, missing=0.0)
        engagement = _num(obs.get("engagement_rate_30d"), scale=1.0)
        opted_out = 1.0 if bool(obs.get("opted_out")) else 0.0

        vec = np.concatenate(
            [
                np.array([day], dtype=np.float32),
                stage_vec,
                np.array(
                    [
                        days_since_borrow,
                        implied_apr,
                        debt_burden,
                        nudges_7d,
                        nudges_30d,
                        engagement,
                        opted_out,
                    ],
                    dtype=np.float32,
                ),
            ],
            axis=0,
        )
        return vec


def action_to_index(action: Action) -> int:
    mapping = {a: i for i, a in enumerate(action_space())}
    return int(mapping[action])


def index_to_action(index: int) -> Action:
    return action_space()[int(index)]


class NudgeGymnasiumEnv(gym.Env if gym is not None else object):
    def __init__(
        self,
        *,
        horizon_days: int = 120,
        seed: int = 0,
        calibration: SurveyCalibration | None = None,
        reward_weights: RewardWeights | None = None,
        deterministic: bool = False,
    ) -> None:
        if gym is None or spaces is None:
            raise RuntimeError("gymnasium_not_installed")

        cal = calibration or SurveyCalibration()
        cal = replace(cal, horizon_days=int(horizon_days))
        self._env = NudgeRLEnv(
            calibration=cal,
            seed=int(seed),
            reward_weights=reward_weights,
            deterministic=bool(deterministic),
        )
        self._encoder = ObsEncoder(horizon_days=int(horizon_days))
        self._next_user_id = 0

        self.action_space = spaces.Discrete(len(action_space()))  # type: ignore[union-attr]
        self.observation_space = spaces.Box(  # type: ignore[union-attr]
            low=-np.inf,
            high=np.inf,
            shape=(self._encoder.dim,),
            dtype=np.float32,
        )
        self.metadata: dict[str, Any] = {}
        self.reward_range = (-float("inf"), float("inf"))
        self.spec = None
        self._seed = int(seed)

    @property
    def encoder(self) -> ObsEncoder:
        return self._encoder

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        if gym is None:
            raise RuntimeError("gymnasium_not_installed")
        if seed is not None:
            gym.utils.seeding.np_random(seed)
        user_id = int(self._next_user_id)
        self._next_user_id += 1
        obs, info = self._env.reset(user_id=user_id)
        return self._encoder.encode(obs), info

    def step(self, action: int):
        obs, reward, done, info = self._env.step(index_to_action(int(action)))
        terminated = bool(done)
        truncated = False
        return self._encoder.encode(obs), float(reward), terminated, truncated, info

    def close(self) -> None:
        return None
