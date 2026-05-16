from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .rl_env import Action, NudgeRLEnv, RewardWeights, SurveyCalibration, action_space, parse_reward_weights
from .rl_sb3 import ObsEncoder, index_to_action


def _default_outdir() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return str(Path("runs") / f"eval_{ts}")


def _baseline_action(obs: dict[str, Any]) -> Action:
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


def _policy_from_model(model_path: str, *, encoder: ObsEncoder) -> Callable[[dict[str, Any]], Action]:
    from stable_baselines3 import PPO

    model = PPO.load(model_path)

    def _act(obs: dict[str, Any]) -> Action:
        x = encoder.encode(obs)
        a, _ = model.predict(x, deterministic=True)
        return index_to_action(int(a))

    return _act


def evaluate_policy(
    *,
    policy_name: str,
    policy_fn: Callable[[dict[str, Any]], Action],
    num_users: int,
    horizon_days: int,
    seed: int,
    reward_weights: RewardWeights,
    calibration: SurveyCalibration | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cal = calibration or SurveyCalibration()
    cal = replace(cal, horizon_days=int(horizon_days))
    env = NudgeRLEnv(
        calibration=cal,
        seed=int(seed),
        reward_weights=reward_weights,
        deterministic=True,
    )

    per_user: list[dict[str, Any]] = []
    totals: dict[str, float] = {
        "reward": 0.0,
        "nudges": 0.0,
        "responded": 0.0,
        "switched": 0.0,
        "borrow_events": 0.0,
        "opt_out_users": 0.0,
    }
    borrowed_aprs: list[float] = []

    for user_id in range(int(num_users)):
        obs, _ = env.reset(user_id=int(user_id))
        done = False
        t = 0

        user_reward = 0.0
        user_nudges = 0
        user_responded = 0
        user_switched = 0
        user_borrow_events = 0
        user_opted_out = False

        while not done and t < int(horizon_days):
            action = policy_fn(obs)
            if action not in action_space():
                action = "wait"
            next_obs, reward, done, info = env.step(action)
            user_reward += float(reward)

            step_info = info.get("info")
            sent = bool(getattr(step_info, "sent_nudge", False))
            responded = bool(getattr(step_info, "responded", False))
            switched = bool(getattr(step_info, "switched_today", False))
            borrowed = bool(getattr(step_info, "borrowed_today", False))
            opted_out = bool(getattr(step_info, "opted_out_today", False)) or bool(next_obs.get("opted_out"))

            if sent:
                user_nudges += 1
                if responded:
                    user_responded += 1
            if switched:
                user_switched += 1
            if borrowed:
                user_borrow_events += 1
                apr = next_obs.get("implied_apr")
                if isinstance(apr, (int, float)):
                    borrowed_aprs.append(float(apr))
            if opted_out:
                user_opted_out = True

            obs = next_obs
            t += 1

        per_user.append(
            {
                "policy": str(policy_name),
                "user_id": int(user_id),
                "total_reward": float(user_reward),
                "nudges": int(user_nudges),
                "responded": int(user_responded),
                "switched": int(user_switched),
                "borrow_events": int(user_borrow_events),
                "opted_out": bool(user_opted_out),
            }
        )

        totals["reward"] += float(user_reward)
        totals["nudges"] += float(user_nudges)
        totals["responded"] += float(user_responded)
        totals["switched"] += float(user_switched)
        totals["borrow_events"] += float(user_borrow_events)
        totals["opt_out_users"] += 1.0 if user_opted_out else 0.0

    n_users = max(1, int(num_users))
    apr_median = None
    apr_share_ge_60 = None
    apr_share_le_30 = None
    borrowed_aprs_sorted = sorted(borrowed_aprs)
    if borrowed_aprs_sorted:
        mid = len(borrowed_aprs_sorted) // 2
        apr_median = (
            borrowed_aprs_sorted[mid]
            if len(borrowed_aprs_sorted) % 2 == 1
            else 0.5 * (borrowed_aprs_sorted[mid - 1] + borrowed_aprs_sorted[mid])
        )
        apr_share_ge_60 = float(sum(1 for x in borrowed_aprs_sorted if x >= 60.0)) / float(len(borrowed_aprs_sorted))
        apr_share_le_30 = float(sum(1 for x in borrowed_aprs_sorted if x <= 30.0)) / float(len(borrowed_aprs_sorted))

    metrics = {
        "policy": str(policy_name),
        "num_users": int(num_users),
        "horizon_days": int(horizon_days),
        "seed": int(seed),
        "reward_weights": asdict(reward_weights),
        "mean_total_reward": float(totals["reward"]) / float(n_users),
        "mean_nudges_per_user": float(totals["nudges"]) / float(n_users),
        "mean_switched_per_user": float(totals["switched"]) / float(n_users),
        "mean_borrow_events_per_user": float(totals["borrow_events"]) / float(n_users),
        "engagement_rate": float(totals["responded"]) / float(max(1.0, totals["nudges"])),
        "opt_out_rate": float(totals["opt_out_users"]) / float(n_users),
        "borrow_events": int(len(borrowed_aprs_sorted)),
        "apr_median": apr_median,
        "apr_share_ge_60": apr_share_ge_60,
        "apr_share_le_30": apr_share_le_30,
    }
    return metrics, per_user


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_per_user_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--outdir", default=_default_outdir())
    p.add_argument("--users", type=int, default=2_000)
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--reward", default="default")
    args = p.parse_args(argv)

    out = Path(str(args.outdir))
    out.mkdir(parents=True, exist_ok=True)

    weights = parse_reward_weights(str(args.reward), base=RewardWeights.from_calibration(SurveyCalibration()))
    encoder = ObsEncoder(horizon_days=int(args.days))

    baseline_metrics, baseline_per_user = evaluate_policy(
        policy_name="baseline",
        policy_fn=_baseline_action,
        num_users=int(args.users),
        horizon_days=int(args.days),
        seed=int(args.seed),
        reward_weights=weights,
    )
    ppo_metrics, ppo_per_user = evaluate_policy(
        policy_name="ppo",
        policy_fn=_policy_from_model(str(args.model), encoder=encoder),
        num_users=int(args.users),
        horizon_days=int(args.days),
        seed=int(args.seed),
        reward_weights=weights,
    )

    combined = {"baseline": baseline_metrics, "ppo": ppo_metrics}
    _write_json(out / "metrics.json", combined)
    _write_json(out / "metrics_baseline.json", baseline_metrics)
    _write_json(out / "metrics_ppo.json", ppo_metrics)
    _write_per_user_csv(out / "per_user_baseline.csv", baseline_per_user)
    _write_per_user_csv(out / "per_user_ppo.csv", ppo_per_user)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

