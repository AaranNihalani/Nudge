from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from nudge_webhook.rl_env import SurveyCalibration
from nudge_webhook.synthetic_trajectories import distribution_summary, export_dataset, generate_trajectories


class TestTask7SyntheticEnvironment(unittest.TestCase):
    def test_distribution_sanity(self) -> None:
        cal = replace(SurveyCalibration(), horizon_days=60, opt_out_base_prob=0.0015)
        rows = generate_trajectories(
            num_users=800,
            horizon_days=60,
            seed=7,
            behavior="epsilon_baseline",
            epsilon=0.10,
            calibration=cal,
        )
        summary = distribution_summary(rows)
        self.assertGreater(summary["borrow_events"], 50)
        self.assertGreater(summary["mean_borrows_per_user"], 0.05)
        self.assertLess(summary["mean_borrows_per_user"], 1.2)
        self.assertIsNotNone(summary["apr_median"])
        self.assertGreater(float(summary["apr_median"]), 25.0)
        self.assertLess(float(summary["apr_median"]), 110.0)
        self.assertIsNotNone(summary["apr_share_ge_60"])
        self.assertGreaterEqual(float(summary["apr_share_ge_60"]), 0.05)
        self.assertLessEqual(float(summary["apr_share_ge_60"]), 0.75)
        self.assertIsNotNone(summary["engagement_rate"])
        self.assertGreaterEqual(float(summary["engagement_rate"]), 0.05)
        self.assertLessEqual(float(summary["engagement_rate"]), 0.60)
        self.assertGreaterEqual(float(summary["opt_out_rate"]), 0.0)
        self.assertLessEqual(float(summary["opt_out_rate"]), 0.25)

    def test_export_jsonl_smoke(self) -> None:
        cal = replace(SurveyCalibration(), horizon_days=12, opt_out_base_prob=0.0)
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "synthetic.jsonl")
            res = export_dataset(
                output_path=out,
                num_users=25,
                horizon_days=12,
                seed=3,
                behavior="baseline",
                epsilon=0.0,
                calibration=cal,
            )
            self.assertEqual(res["rows"], 25 * 12)
            self.assertTrue(Path(out).exists())
            self.assertGreater(Path(out).stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()

