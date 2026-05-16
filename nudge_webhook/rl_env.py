from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

Action = Literal["wait", "alert", "suggest_lender", "education"]
Stage = Literal["none", "considering", "asking", "offered", "agreed", "borrowed"]


@dataclass(frozen=True)
class SurveyCalibration:
    horizon_days: int = 120
    nudge_cooldown_days: int = 2
    borrow_hazard_beta_a: float = 1.6
    borrow_hazard_beta_b: float = 7.0
    borrow_hazard_scale: float = 0.06
    informal_share: float = 0.78
    engagement_beta_a: float = 2.0
    engagement_beta_b: float = 7.5
    spam_tolerance_beta_a: float = 2.2
    spam_tolerance_beta_b: float = 3.2
    price_sensitivity_beta_a: float = 2.0
    price_sensitivity_beta_b: float = 2.0
    opt_out_base_prob: float = 0.002
    nudge_spam_penalty: float = 0.02
    engagement_reward: float = 0.20
    switch_reward: float = 0.30
    opt_out_penalty: float = 1.5
    apr_penalty_weight: float = 0.9
    informal_apr_mu: float = 4.2
    informal_apr_sigma: float = 0.35
    regulated_rate_multiplier: float = 1.35
    regulated_apr_noise_sigma: float = 0.08
    action_effect_alert: float = 0.28
    action_effect_suggest: float = 0.14
    action_effect_education: float = 0.04


@dataclass(frozen=True)
class RewardWeights:
    nudge_spam_penalty: float = 0.02
    engagement_reward: float = 0.20
    switch_reward: float = 0.30
    opt_out_penalty: float = 1.5
    apr_penalty_weight: float = 0.9

    @classmethod
    def from_calibration(cls, calibration: SurveyCalibration) -> "RewardWeights":
        return cls(
            nudge_spam_penalty=float(calibration.nudge_spam_penalty),
            engagement_reward=float(calibration.engagement_reward),
            switch_reward=float(calibration.switch_reward),
            opt_out_penalty=float(calibration.opt_out_penalty),
            apr_penalty_weight=float(calibration.apr_penalty_weight),
        )


def reward_weight_presets(*, base: RewardWeights | None = None) -> dict[str, RewardWeights]:
    b = base or RewardWeights()
    return {
        "default": b,
        "no_spam": RewardWeights(
            nudge_spam_penalty=0.0,
            engagement_reward=b.engagement_reward,
            switch_reward=b.switch_reward,
            opt_out_penalty=b.opt_out_penalty,
            apr_penalty_weight=b.apr_penalty_weight,
        ),
        "no_engagement": RewardWeights(
            nudge_spam_penalty=b.nudge_spam_penalty,
            engagement_reward=0.0,
            switch_reward=b.switch_reward,
            opt_out_penalty=b.opt_out_penalty,
            apr_penalty_weight=b.apr_penalty_weight,
        ),
        "no_switch": RewardWeights(
            nudge_spam_penalty=b.nudge_spam_penalty,
            engagement_reward=b.engagement_reward,
            switch_reward=0.0,
            opt_out_penalty=b.opt_out_penalty,
            apr_penalty_weight=b.apr_penalty_weight,
        ),
        "no_optout_penalty": RewardWeights(
            nudge_spam_penalty=b.nudge_spam_penalty,
            engagement_reward=b.engagement_reward,
            switch_reward=b.switch_reward,
            opt_out_penalty=0.0,
            apr_penalty_weight=b.apr_penalty_weight,
        ),
        "no_apr_penalty": RewardWeights(
            nudge_spam_penalty=b.nudge_spam_penalty,
            engagement_reward=b.engagement_reward,
            switch_reward=b.switch_reward,
            opt_out_penalty=b.opt_out_penalty,
            apr_penalty_weight=0.0,
        ),
    }


def parse_reward_weights(spec: str, *, base: RewardWeights | None = None) -> RewardWeights:
    s = str(spec or "").strip()
    presets = reward_weight_presets(base=base)
    if s in presets:
        return presets[s]

    if s.lower().endswith(".json") and Path(s).exists():
        with open(s, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.loads(s)

    if not isinstance(payload, dict):
        raise ValueError("invalid_reward_weights")
    b = base or RewardWeights()
    return RewardWeights(
        nudge_spam_penalty=float(payload.get("nudge_spam_penalty", b.nudge_spam_penalty)),
        engagement_reward=float(payload.get("engagement_reward", b.engagement_reward)),
        switch_reward=float(payload.get("switch_reward", b.switch_reward)),
        opt_out_penalty=float(payload.get("opt_out_penalty", b.opt_out_penalty)),
        apr_penalty_weight=float(payload.get("apr_penalty_weight", b.apr_penalty_weight)),
    )


@dataclass(frozen=True)
class UserLatentTraits:
    district: str
    daily_borrow_prob: float
    informal_affinity: float
    engagement_propensity: float
    spam_tolerance: float
    price_sensitivity: float


@dataclass
class EnvState:
    user_id: int
    day: int
    stage: Stage
    last_borrow_day: int | None
    amount_inr: float | None
    tenure_days: int | None
    current_apr: float | None
    informal: bool | None
    nudge_days: list[int]
    engaged_nudges: int
    switch_attempted_in_episode: bool
    opted_out: bool


@dataclass(frozen=True)
class StepInfo:
    sent_nudge: bool
    responded: bool
    borrowed_today: bool
    switched_today: bool
    opted_out_today: bool


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_rate(value: float | None) -> float | None:
    if value is None:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def _safe_int(value: int | None) -> int | None:
    return int(value) if value is not None else None


def _stable_seed(*parts: Any) -> int:
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _load_regulated_rates(dataset_path: str) -> dict[str, list[float]]:
    suffix = Path(dataset_path).suffix.lower()
    if suffix == ".json":
        with open(dataset_path, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload if isinstance(payload, list) else []
    else:
        with open(dataset_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    by_district: dict[str, list[float]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        district = str(r.get("district") or "").strip()
        if district == "":
            continue
        raw_rate = r.get("rate_apr") if "rate_apr" in r else r.get("rate")
        if raw_rate is None:
            continue
        try:
            rate = float(str(raw_rate).strip())
        except Exception:
            continue
        if not (rate > 0):
            continue
        by_district.setdefault(district, []).append(rate)

    for d, rates in list(by_district.items()):
        cleaned = [float(x) for x in rates if x > 0]
        if cleaned:
            by_district[d] = sorted(cleaned)
        else:
            del by_district[d]
    return by_district


def _default_regulated_rates() -> dict[str, list[float]]:
    root = Path(__file__).resolve().parents[1]
    candidate = root / "datasets" / "mfi_rates.json"
    if candidate.exists():
        return _load_regulated_rates(str(candidate))
    return {"default": [22.0, 24.0, 28.0]}


def _days_since(last_day: int | None, *, current_day: int) -> float | None:
    if last_day is None:
        return None
    return float(max(0, int(current_day) - int(last_day)))


def _debt_burden_proxy(amount_inr: float | None, tenure_days: int | None, apr: float | None) -> float | None:
    if amount_inr is None or tenure_days is None or apr is None:
        return None
    ratio = (float(apr) / 100.0) * (float(tenure_days) / 365.0)
    return max(0.0, float(amount_inr) * ratio)


def observe(state: EnvState) -> dict[str, Any]:
    nudges_7d = sum(1 for d in state.nudge_days if state.day - d <= 7)
    nudges_30d = sum(1 for d in state.nudge_days if state.day - d <= 30)
    engagement_rate = None
    if nudges_30d > 0:
        engagement_rate = float(state.engaged_nudges) / float(nudges_30d)
    return {
        "day": int(state.day),
        "stage": str(state.stage),
        "days_since_borrow": _days_since(state.last_borrow_day, current_day=state.day),
        "implied_apr": _safe_rate(state.current_apr),
        "debt_burden_proxy": _debt_burden_proxy(state.amount_inr, state.tenure_days, state.current_apr),
        "nudges_7d": int(nudges_7d),
        "nudges_30d": int(nudges_30d),
        "engagement_rate_30d": _safe_rate(engagement_rate),
        "opted_out": bool(state.opted_out),
    }


class NudgeRLEnv:
    def __init__(
        self,
        *,
        calibration: SurveyCalibration | None = None,
        seed: int = 0,
        regulated_rates: dict[str, list[float]] | None = None,
        reward_weights: RewardWeights | None = None,
        deterministic: bool = False,
    ) -> None:
        self.calibration = calibration or SurveyCalibration()
        self._rng = random.Random(int(seed))
        self._base_seed = int(seed)
        self._deterministic = bool(deterministic)
        self._regulated_rates = regulated_rates or _default_regulated_rates()
        self._districts = sorted(self._regulated_rates.keys()) or ["default"]
        self._traits: UserLatentTraits | None = None
        self._state: EnvState | None = None
        self._reward_weights = reward_weights or RewardWeights.from_calibration(self.calibration)

    @property
    def state(self) -> EnvState:
        if self._state is None:
            raise RuntimeError("env_not_reset")
        return self._state

    @property
    def traits(self) -> UserLatentTraits:
        if self._traits is None:
            raise RuntimeError("env_not_reset")
        return self._traits

    def _rng_for(self, *, user_id: int, day: int, tag: str) -> random.Random:
        if not self._deterministic:
            return self._rng
        return random.Random(_stable_seed(self._base_seed, int(user_id), int(day), str(tag)))

    def _sample_traits(self, *, user_id: int) -> UserLatentTraits:
        cal = self.calibration
        if not self._deterministic:
            district = self._rng.choice(self._districts)
        else:
            district = self._rng_for(user_id=int(user_id), day=-1, tag="district").choice(self._districts)
        daily_borrow_prob = _clip(
            self._rng_for(user_id=int(user_id), day=-1, tag="daily_borrow").betavariate(
                cal.borrow_hazard_beta_a, cal.borrow_hazard_beta_b
            )
            * cal.borrow_hazard_scale,
            0.0001,
            0.25,
        )
        engagement = _clip(
            self._rng_for(user_id=int(user_id), day=-1, tag="engagement").betavariate(
                cal.engagement_beta_a, cal.engagement_beta_b
            ),
            0.01,
            0.95,
        )
        spam_tolerance = _clip(
            self._rng_for(user_id=int(user_id), day=-1, tag="spam_tolerance").betavariate(
                cal.spam_tolerance_beta_a, cal.spam_tolerance_beta_b
            ),
            0.01,
            0.99,
        )
        price_sensitivity = _clip(
            self._rng_for(user_id=int(user_id), day=-1, tag="price_sensitivity").betavariate(
                cal.price_sensitivity_beta_a, cal.price_sensitivity_beta_b
            ),
            0.01,
            0.99,
        )
        informal_affinity = _clip(self._rng_for(user_id=int(user_id), day=-1, tag="informal_affinity").random(), 0.0, 1.0)
        return UserLatentTraits(
            district=str(district),
            daily_borrow_prob=float(daily_borrow_prob),
            informal_affinity=float(informal_affinity),
            engagement_propensity=float(engagement),
            spam_tolerance=float(spam_tolerance),
            price_sensitivity=float(price_sensitivity),
        )

    def reset(self, *, user_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        self._traits = self._sample_traits(user_id=int(user_id))
        self._state = EnvState(
            user_id=int(user_id),
            day=0,
            stage="none",
            last_borrow_day=None,
            amount_inr=None,
            tenure_days=None,
            current_apr=None,
            informal=None,
            nudge_days=[],
            engaged_nudges=0,
            switch_attempted_in_episode=False,
            opted_out=False,
        )
        return observe(self._state), {"district": self._traits.district}

    def _sample_amount_and_tenure(self, *, rng: random.Random) -> tuple[float, int]:
        amount = _clip(rng.lognormvariate(8.3, 0.55), 1000.0, 200000.0)
        tenure = int(_clip(rng.triangular(7, 120, 30), 3, 365))
        return float(amount), int(tenure)

    def _sample_informal_apr(self, *, rng: random.Random) -> float:
        cal = self.calibration
        apr = rng.lognormvariate(cal.informal_apr_mu, cal.informal_apr_sigma)
        return float(_clip(apr, 10.0, 220.0))

    def _sample_regulated_apr(self, *, district: str, rng: random.Random) -> float:
        cal = self.calibration
        rates = self._regulated_rates.get(district) or self._regulated_rates.get("default") or [24.0]
        base = float(rng.choice(rates)) * float(cal.regulated_rate_multiplier)
        noisy = base * float(rng.lognormvariate(0.0, cal.regulated_apr_noise_sigma))
        return float(_clip(noisy, 8.0, 60.0))

    def _maybe_start_borrowing(self) -> None:
        state = self.state
        traits = self.traits
        if state.stage != "none":
            return
        if not self._deterministic:
            if self._rng.random() >= traits.daily_borrow_prob:
                return
            informal_p = self.calibration.informal_share * (0.6 + 0.8 * traits.informal_affinity)
            informal = self._rng.random() < _clip(informal_p, 0.05, 0.98)
            amount, tenure = self._sample_amount_and_tenure(rng=self._rng)
            apr = (
                self._sample_informal_apr(rng=self._rng)
                if informal
                else self._sample_regulated_apr(district=traits.district, rng=self._rng)
            )
        else:
            rng_start = self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="borrow_start")
            if rng_start.random() >= traits.daily_borrow_prob:
                return
            informal_p = self.calibration.informal_share * (0.6 + 0.8 * traits.informal_affinity)
            rng_informal = self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="informal_choice")
            informal = rng_informal.random() < _clip(informal_p, 0.05, 0.98)
            amount, tenure = self._sample_amount_and_tenure(
                rng=self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="amount_tenure")
            )
            if informal:
                apr = self._sample_informal_apr(
                    rng=self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="informal_apr")
                )
            else:
                apr = self._sample_regulated_apr(
                    district=traits.district,
                    rng=self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="regulated_apr"),
                )
        state.stage = "considering"
        state.amount_inr = amount
        state.tenure_days = tenure
        state.current_apr = apr
        state.informal = bool(informal)

    def _advance_stage(self) -> bool:
        state = self.state
        if state.stage == "none":
            return False
        if state.stage == "borrowed":
            if state.last_borrow_day is not None and state.day - state.last_borrow_day >= 14:
                if not self._deterministic:
                    reset_today = self._rng.random() < 0.25
                else:
                    rng_reset = self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="reset_to_none")
                    reset_today = rng_reset.random() < 0.25
                if reset_today:
                    state.stage = "none"
                    state.amount_inr = None
                    state.tenure_days = None
                    state.current_apr = None
                    state.informal = None
                    state.switch_attempted_in_episode = False
            return False

        p = 0.22 if state.stage in {"considering", "asking"} else 0.30
        if not self._deterministic:
            if self._rng.random() >= p:
                return False
        else:
            rng_advance = self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="advance_stage")
            if rng_advance.random() >= p:
                return False

        next_stage: dict[Stage, Stage] = {
            "considering": "asking",
            "asking": "offered",
            "offered": "agreed",
            "agreed": "borrowed",
            "none": "none",
            "borrowed": "borrowed",
        }
        state.stage = next_stage[state.stage]
        if state.stage == "borrowed":
            state.last_borrow_day = int(state.day)
            return True
        return False

    def _maybe_switch_to_regulated(self, *, action: Action, responded: bool) -> bool:
        state = self.state
        traits = self.traits
        if state.stage not in {"asking", "offered", "agreed", "borrowed"}:
            return False
        if state.current_apr is None or state.informal is not True:
            return False

        cal = self.calibration
        effect = 0.0
        if action == "alert":
            effect = cal.action_effect_alert
        elif action == "suggest_lender":
            effect = cal.action_effect_suggest
        elif action == "education":
            effect = cal.action_effect_education

        if responded:
            effect *= 1.25
        if state.switch_attempted_in_episode:
            effect *= 0.25

        sensitivity = float(traits.price_sensitivity)
        p = _clip(effect * sensitivity, 0.0, 0.95)
        if not self._deterministic:
            if self._rng.random() >= p:
                return False
        else:
            rng_switch = self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="switch")
            if rng_switch.random() >= p:
                return False

        state.current_apr = self._sample_regulated_apr(
            district=traits.district,
            rng=self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="regulated_apr_after_switch")
            if self._deterministic
            else self._rng,
        )
        state.informal = False
        state.switch_attempted_in_episode = True
        return True

    def _maybe_opt_out(self, *, sent_nudge: bool, responded: bool) -> bool:
        state = self.state
        if not sent_nudge:
            return False
        if state.opted_out:
            return False

        cal = self.calibration
        nudges_7d = sum(1 for d in state.nudge_days if state.day - d <= 7)
        overload = max(0.0, float(nudges_7d) - 2.0)
        p = cal.opt_out_base_prob * (1.0 + 0.6 * overload) * (1.0 - self.traits.spam_tolerance)
        if responded:
            p *= 0.35
        p = _clip(p, 0.0, 0.25)
        if not self._deterministic:
            if self._rng.random() >= p:
                return False
        else:
            rng_opt_out = self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="opt_out")
            if rng_opt_out.random() >= p:
                return False

        state.opted_out = True
        return True

    def step(self, action: Action) -> tuple[dict[str, Any], float, bool, dict[str, Any]]:
        state = self.state
        traits = self.traits
        cal = self.calibration

        if state.opted_out:
            return observe(state), 0.0, True, {"info": StepInfo(False, False, False, False, False)}

        sent_nudge = False
        if action != "wait":
            last_nudge_day = max(state.nudge_days) if state.nudge_days else None
            if last_nudge_day is None or (state.day - int(last_nudge_day)) >= int(cal.nudge_cooldown_days):
                sent_nudge = True
        responded = False
        if sent_nudge:
            state.nudge_days.append(int(state.day))
            mult = 1.0
            if action == "alert":
                mult = 1.1
            elif action == "suggest_lender":
                mult = 1.0
            elif action == "education":
                mult = 0.9
            if not self._deterministic:
                responded = self._rng.random() < _clip(traits.engagement_propensity * mult, 0.0, 0.98)
            else:
                rng_respond = self._rng_for(user_id=int(state.user_id), day=int(state.day), tag="respond")
                responded = rng_respond.random() < _clip(traits.engagement_propensity * mult, 0.0, 0.98)
            if responded:
                state.engaged_nudges += 1

        self._maybe_start_borrowing()

        borrowed_today = self._advance_stage()

        switched_today = False
        before_apr = state.current_apr
        if sent_nudge:
            switched_today = self._maybe_switch_to_regulated(action=action, responded=responded)
        after_apr = state.current_apr

        opted_out_today = self._maybe_opt_out(sent_nudge=sent_nudge, responded=responded)

        weights = self._reward_weights
        reward = 0.0
        if sent_nudge:
            reward -= float(weights.nudge_spam_penalty)
        if responded:
            reward += float(weights.engagement_reward)
        if switched_today and before_apr is not None and after_apr is not None and after_apr < before_apr:
            reward += float(weights.switch_reward)
        if borrowed_today and state.current_apr is not None:
            penalty = (max(0.0, float(state.current_apr) - 30.0) / 100.0) * float(weights.apr_penalty_weight)
            reward -= float(penalty)
        if opted_out_today:
            reward -= float(weights.opt_out_penalty)

        state.day += 1
        done = state.day >= int(cal.horizon_days) or state.opted_out
        obs = observe(state)
        info = {
            "district": traits.district,
            "user_id": int(state.user_id),
            "info": StepInfo(
                sent_nudge=bool(sent_nudge),
                responded=bool(responded),
                borrowed_today=bool(borrowed_today),
                switched_today=bool(switched_today),
                opted_out_today=bool(opted_out_today),
            ),
        }
        return obs, float(reward), bool(done), info


def action_space() -> tuple[Action, ...]:
    return ("wait", "alert", "suggest_lender", "education")


def stage_space() -> tuple[Stage, ...]:
    return ("none", "considering", "asking", "offered", "agreed", "borrowed")


def flatten_step(
    *,
    user_id: int,
    t: int,
    obs: dict[str, Any],
    action: Action,
    reward: float,
    done: bool,
    info: dict[str, Any],
) -> dict[str, Any]:
    s = dict(obs)
    payload: dict[str, Any] = {
        "user_id": int(user_id),
        "t": int(t),
        "action": str(action),
        "reward": float(reward),
        "done": bool(done),
        "district": str(info.get("district") or ""),
        "sent_nudge": bool(getattr(info.get("info"), "sent_nudge", False)),
        "responded": bool(getattr(info.get("info"), "responded", False)),
        "borrowed_today": bool(getattr(info.get("info"), "borrowed_today", False)),
        "switched_today": bool(getattr(info.get("info"), "switched_today", False)),
        "opted_out_today": bool(getattr(info.get("info"), "opted_out_today", False)),
    }
    payload.update(s)
    return payload


def iter_jsonl(path: str) -> Iterable[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line == "":
                continue
            yield json.loads(line)
