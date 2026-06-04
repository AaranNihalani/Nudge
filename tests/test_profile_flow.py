from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nudge_webhook.app import create_app
from nudge_webhook.config import Config
from nudge_webhook.db import init_and_migrate


def _make_cfg(db_path: str) -> Config:
    return Config(
        port=5000,
        railway_environment=None,
        db_path=db_path,
        claude_api_key=None,
        claude_model="claude-sonnet-4-6",
        claude_timeout_seconds=2.0,
        claude_attempts=1,
        debug_claude=False,
        nudge_cooldown_minutes=0,
        nudge_max_per_day=100,
        nudge_max_per_week=100,
        baseline_policy_enabled=False,
        policy_mode="off",
        mfi_dataset_path=str(Path(__file__).resolve().parents[1] / "datasets" / "mfi_rates.csv"),
        mfi_autoload=False,
        admin_token="test",
        anon_salt="test",
        verbose_replies=False,
    )


class TestProfileFlow(unittest.TestCase):
    def test_household_size_does_not_500_and_advances_to_land(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)
            app = create_app(_make_cfg(db_path))
            client = app.test_client()

            session_id = "profile-flow-001"

            def chat(msg: str) -> tuple[int, str]:
                r = client.post("/api/chat", json={"session_id": session_id, "message": msg})
                data = r.get_json(silent=True) or {}
                return r.status_code, str(data.get("reply") or "")

            code, _ = chat("START")
            self.assertEqual(code, 200)
            code, _ = chat("DISTRICT Chennai")
            self.assertEqual(code, 200)

            code, reply = chat("yes")
            self.assertEqual(code, 200)
            self.assertIn("caste", reply.lower())

            code, _ = chat("obc")
            self.assertEqual(code, 200)
            code, _ = chat("hindu")
            self.assertEqual(code, 200)
            code, _ = chat("15000")
            self.assertEqual(code, 200)

            code, reply = chat("18")
            self.assertEqual(code, 200)
            self.assertIn("land", reply.lower())

    def test_large_land_is_accepted_and_advances(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)
            app = create_app(_make_cfg(db_path))
            client = app.test_client()

            session_id = "profile-flow-002"

            def chat(msg: str) -> tuple[int, str]:
                r = client.post("/api/chat", json={"session_id": session_id, "message": msg})
                data = r.get_json(silent=True) or {}
                return r.status_code, str(data.get("reply") or "")

            chat("START")
            chat("DISTRICT Chennai")
            chat("yes")
            chat("obc")
            chat("hindu")
            chat("15000")
            chat("5")

            code, reply = chat("500")
            self.assertEqual(code, 200)
            self.assertIn("urban", reply.lower())


if __name__ == "__main__":
    unittest.main()
