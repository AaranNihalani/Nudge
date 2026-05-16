from __future__ import annotations

import unittest
from dataclasses import replace

from nudge_webhook.rl_env import NudgeRLEnv, RewardWeights, SurveyCalibration


class TestTask8PPOHarnessBuildingBlocks(unittest.TestCase):
    def test_deterministic_mode_replays(self) -> None:
        cal = replace(SurveyCalibration(), horizon_days=15, opt_out_base_prob=0.0)
        env_a = NudgeRLEnv(calibration=cal, seed=123, deterministic=True)
        env_b = NudgeRLEnv(calibration=cal, seed=123, deterministic=True)

        obs_a, _ = env_a.reset(user_id=7)
        obs_b, _ = env_b.reset(user_id=7)
        self.assertEqual(obs_a, obs_b)

        for _ in range(10):
            next_a, r_a, d_a, info_a = env_a.step("wait")
            next_b, r_b, d_b, info_b = env_b.step("wait")
            self.assertEqual(next_a, next_b)
            self.assertAlmostEqual(float(r_a), float(r_b), places=9)
            self.assertEqual(bool(d_a), bool(d_b))
            self.assertEqual(bool(getattr(info_a.get("info"), "sent_nudge", False)), bool(getattr(info_b.get("info"), "sent_nudge", False)))
            obs_a = next_a
            obs_b = next_b

    def test_reward_weights_change_objective(self) -> None:
        cal = replace(SurveyCalibration(), horizon_days=5, opt_out_base_prob=0.0)
        base = RewardWeights.from_calibration(cal)
        w0 = RewardWeights(
            nudge_spam_penalty=0.0,
            engagement_reward=base.engagement_reward,
            switch_reward=base.switch_reward,
            opt_out_penalty=base.opt_out_penalty,
            apr_penalty_weight=base.apr_penalty_weight,
        )
        w1 = RewardWeights(
            nudge_spam_penalty=0.25,
            engagement_reward=base.engagement_reward,
            switch_reward=base.switch_reward,
            opt_out_penalty=base.opt_out_penalty,
            apr_penalty_weight=base.apr_penalty_weight,
        )

        env0 = NudgeRLEnv(calibration=cal, seed=99, deterministic=True, reward_weights=w0)
        env1 = NudgeRLEnv(calibration=cal, seed=99, deterministic=True, reward_weights=w1)
        env0.reset(user_id=1)
        env1.reset(user_id=1)

        _, r0, _, _ = env0.step("alert")
        _, r1, _, _ = env1.step("alert")
        self.assertAlmostEqual(float(r0) - float(r1), 0.25, places=6)


if __name__ == "__main__":
    unittest.main()

