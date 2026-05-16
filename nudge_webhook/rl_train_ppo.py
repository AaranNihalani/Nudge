from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from .rl_env import RewardWeights, SurveyCalibration, parse_reward_weights


def _default_outdir() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return str(Path("runs") / f"ppo_{ts}")


def train_ppo(
    *,
    outdir: str,
    total_timesteps: int,
    seed: int,
    horizon_days: int,
    reward_weights: RewardWeights,
    calibration: SurveyCalibration | None = None,
) -> dict[str, str]:
    from stable_baselines3 import PPO
    from stable_baselines3.common.logger import configure
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv

    from .rl_sb3 import NudgeGymnasiumEnv

    cal = calibration or SurveyCalibration()
    cal = replace(cal, horizon_days=int(horizon_days))

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    env = DummyVecEnv(
        [
            lambda: Monitor(
                NudgeGymnasiumEnv(
                    horizon_days=int(horizon_days),
                    seed=int(seed),
                    calibration=cal,
                    reward_weights=reward_weights,
                    deterministic=False,
                )
            )
        ]
    )

    model = PPO(
        "MlpPolicy",
        env,
        seed=int(seed),
        n_steps=256,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        ent_coef=0.01,
        vf_coef=0.5,
        learning_rate=3e-4,
        clip_range=0.2,
        verbose=1,
    )

    logger = configure(str(out), ["stdout", "csv"])
    model.set_logger(logger)

    model.learn(total_timesteps=int(total_timesteps))

    model_path = out / "model.zip"
    model.save(str(model_path))

    config_path = out / "train_config.json"
    config_path.write_text(
        json.dumps(
            {
                "algo": "ppo",
                "total_timesteps": int(total_timesteps),
                "seed": int(seed),
                "horizon_days": int(horizon_days),
                "reward_weights": asdict(reward_weights),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return {"outdir": str(out), "model_path": str(model_path), "train_config_path": str(config_path)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--outdir", default=_default_outdir())
    p.add_argument("--timesteps", type=int, default=50_000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--reward", default="default")
    args = p.parse_args(argv)

    weights = parse_reward_weights(str(args.reward), base=RewardWeights.from_calibration(SurveyCalibration()))
    train_ppo(
        outdir=str(args.outdir),
        total_timesteps=int(args.timesteps),
        seed=int(args.seed),
        horizon_days=int(args.days),
        reward_weights=weights,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

