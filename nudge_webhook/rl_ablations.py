from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .rl_env import RewardWeights, SurveyCalibration, parse_reward_weights, reward_weight_presets
from .rl_eval import evaluate_policy
from .rl_sb3 import ObsEncoder
from .rl_train_ppo import train_ppo


def _default_outdir() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return str(Path("runs") / f"ablations_{ts}")


def _baseline_action(obs: dict) -> str:
    stage = str(obs.get("stage") or "none")
    apr = obs.get("implied_apr")
    days_since_borrow = obs.get("days_since_borrow")

    if isinstance(apr, (int, float)) and apr >= 60.0 and stage in {"asking", "offered", "agreed", "borrowed"}:
        return "alert"
    if isinstance(apr, (int, float)) and apr >= 40.0 and stage in {"considering", "asking", "offered", "agreed"}:
        return "suggest_lender"
    if stage == "borrowed" and isinstance(days_since_borrow, (int, float)) and days_since_borrow <= 7.0:
        return "education"
    return "wait"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--outdir", default=_default_outdir())
    p.add_argument("--presets", default="default,no_spam,no_engagement,no_switch,no_optout_penalty,no_apr_penalty")
    p.add_argument("--timesteps", type=int, default=30_000)
    p.add_argument("--train-seed", type=int, default=0)
    p.add_argument("--eval-seed", type=int, default=42)
    p.add_argument("--users", type=int, default=2_000)
    p.add_argument("--days", type=int, default=120)
    args = p.parse_args(argv)

    out = Path(str(args.outdir))
    out.mkdir(parents=True, exist_ok=True)

    base = RewardWeights.from_calibration(SurveyCalibration())
    presets = reward_weight_presets(base=base)
    requested = [p.strip() for p in str(args.presets).split(",") if p.strip()]

    results: dict[str, dict] = {}
    encoder = ObsEncoder(horizon_days=int(args.days))

    for name in requested:
        weights = presets.get(name) or parse_reward_weights(name, base=base)
        run_dir = out / name
        run_dir.mkdir(parents=True, exist_ok=True)

        train_res = train_ppo(
            outdir=str(run_dir),
            total_timesteps=int(args.timesteps),
            seed=int(args.train_seed),
            horizon_days=int(args.days),
            reward_weights=weights,
        )

        baseline_metrics, _ = evaluate_policy(
            policy_name="baseline",
            policy_fn=_baseline_action,  # type: ignore[arg-type]
            num_users=int(args.users),
            horizon_days=int(args.days),
            seed=int(args.eval_seed),
            reward_weights=weights,
        )

        from stable_baselines3 import PPO

        model = PPO.load(train_res["model_path"])

        def _ppo_action(obs: dict) -> str:
            x = encoder.encode(obs)
            a, _ = model.predict(x, deterministic=True)
            return ["wait", "alert", "suggest_lender", "education"][int(a)]

        ppo_metrics, _ = evaluate_policy(
            policy_name="ppo",
            policy_fn=_ppo_action,  # type: ignore[arg-type]
            num_users=int(args.users),
            horizon_days=int(args.days),
            seed=int(args.eval_seed),
            reward_weights=weights,
        )

        combined = {"baseline": baseline_metrics, "ppo": ppo_metrics, "train": train_res}
        (run_dir / "metrics.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results[name] = combined

    (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

