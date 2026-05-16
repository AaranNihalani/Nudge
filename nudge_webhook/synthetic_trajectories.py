from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Literal

from .rl_env import Action, NudgeRLEnv, SurveyCalibration, action_space, flatten_step

BehaviorPolicy = Literal["baseline", "random", "epsilon_baseline"]


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


def _choose_action(
    *,
    env: NudgeRLEnv,
    obs: dict[str, Any],
    behavior: BehaviorPolicy,
    epsilon: float,
) -> Action:
    rng = env._rng
    if behavior == "random":
        return rng.choice(action_space())
    if behavior == "baseline":
        return _baseline_action(obs)
    if rng.random() < float(epsilon):
        return rng.choice(action_space())
    return _baseline_action(obs)


class _JsonlWriter:
    def __init__(self, path: str) -> None:
        self._f = open(path, "w", encoding="utf-8")

    def write(self, row: dict[str, Any]) -> None:
        self._f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._f.close()


class _CsvWriter:
    def __init__(self, path: str, *, fieldnames: list[str]) -> None:
        self._f = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._f, fieldnames=fieldnames)
        self._writer.writeheader()
        self._fieldnames = fieldnames

    def write(self, row: dict[str, Any]) -> None:
        out: dict[str, Any] = {}
        for k in self._fieldnames:
            v = row.get(k)
            out[k] = "" if v is None else v
        self._writer.writerow(out)

    def close(self) -> None:
        self._f.close()


def _writer_for_path(path: str):
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        fieldnames = [
            "user_id",
            "t",
            "action",
            "reward",
            "done",
            "district",
            "sent_nudge",
            "responded",
            "borrowed_today",
            "switched_today",
            "opted_out_today",
            "day",
            "stage",
            "days_since_borrow",
            "implied_apr",
            "debt_burden_proxy",
            "nudges_7d",
            "nudges_30d",
            "engagement_rate_30d",
            "opted_out",
        ]
        return _CsvWriter(path, fieldnames=fieldnames)
    return _JsonlWriter(path)


def generate_trajectories(
    *,
    num_users: int,
    horizon_days: int,
    seed: int = 0,
    behavior: BehaviorPolicy = "epsilon_baseline",
    epsilon: float = 0.10,
    calibration: SurveyCalibration | None = None,
) -> Iterable[dict[str, Any]]:
    cal = calibration or SurveyCalibration()
    cal = replace(cal, horizon_days=int(horizon_days))
    env = NudgeRLEnv(calibration=cal, seed=int(seed))
    for user_id in range(int(num_users)):
        obs, _ = env.reset(user_id=user_id)
        done = False
        t = 0
        while not done and t < int(horizon_days):
            action = _choose_action(env=env, obs=obs, behavior=behavior, epsilon=epsilon)
            next_obs, reward, done, info = env.step(action)
            yield flatten_step(
                user_id=user_id,
                t=t,
                obs=obs,
                action=action,
                reward=reward,
                done=done,
                info=info,
            )
            obs = next_obs
            t += 1


def export_dataset(
    *,
    output_path: str,
    num_users: int = 10_000,
    horizon_days: int = 120,
    seed: int = 0,
    behavior: BehaviorPolicy = "epsilon_baseline",
    epsilon: float = 0.10,
    calibration: SurveyCalibration | None = None,
) -> dict[str, Any]:
    writer = _writer_for_path(output_path)
    rows = 0
    try:
        for row in generate_trajectories(
            num_users=num_users,
            horizon_days=horizon_days,
            seed=seed,
            behavior=behavior,
            epsilon=epsilon,
            calibration=calibration,
        ):
            writer.write(row)
            rows += 1
    finally:
        writer.close()
    return {"output_path": str(output_path), "rows": int(rows)}


def distribution_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    users = set()
    borrowed_aprs: list[float] = []
    nudge_count = 0
    engaged_count = 0
    opt_out_users = set()
    borrow_events_by_user: dict[int, int] = {}

    for r in rows:
        uid = int(r["user_id"])
        users.add(uid)
        if bool(r.get("borrowed_today")) and isinstance(r.get("implied_apr"), (int, float)):
            borrowed_aprs.append(float(r["implied_apr"]))
            borrow_events_by_user[uid] = borrow_events_by_user.get(uid, 0) + 1
        if bool(r.get("sent_nudge")):
            nudge_count += 1
            if bool(r.get("responded")):
                engaged_count += 1
        if bool(r.get("opted_out")) or bool(r.get("opted_out_today")):
            opt_out_users.add(uid)

    n_users = max(1, len(users))
    mean_borrows_per_user = float(sum(borrow_events_by_user.values())) / float(n_users)
    engagement_rate = None
    if nudge_count > 0:
        engagement_rate = float(engaged_count) / float(nudge_count)

    borrowed_aprs_sorted = sorted(borrowed_aprs)
    apr_median = None
    apr_share_ge_60 = None
    apr_share_le_30 = None
    if borrowed_aprs_sorted:
        mid = len(borrowed_aprs_sorted) // 2
        apr_median = (
            borrowed_aprs_sorted[mid]
            if len(borrowed_aprs_sorted) % 2 == 1
            else 0.5 * (borrowed_aprs_sorted[mid - 1] + borrowed_aprs_sorted[mid])
        )
        apr_share_ge_60 = float(sum(1 for x in borrowed_aprs_sorted if x >= 60.0)) / float(len(borrowed_aprs_sorted))
        apr_share_le_30 = float(sum(1 for x in borrowed_aprs_sorted if x <= 30.0)) / float(len(borrowed_aprs_sorted))

    return {
        "num_users": int(n_users),
        "borrow_events": int(len(borrowed_aprs_sorted)),
        "mean_borrows_per_user": float(mean_borrows_per_user),
        "apr_median": apr_median,
        "apr_share_ge_60": apr_share_ge_60,
        "apr_share_le_30": apr_share_le_30,
        "nudge_count": int(nudge_count),
        "engagement_rate": engagement_rate,
        "opt_out_rate": float(len(opt_out_users)) / float(n_users),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--users", type=int, default=10_000)
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--behavior", choices=["baseline", "random", "epsilon_baseline"], default="epsilon_baseline")
    p.add_argument("--epsilon", type=float, default=0.10)
    args = p.parse_args(argv)

    export_dataset(
        output_path=str(args.output),
        num_users=int(args.users),
        horizon_days=int(args.days),
        seed=int(args.seed),
        behavior=str(args.behavior),  # type: ignore[arg-type]
        epsilon=float(args.epsilon),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
