from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nudge_webhook.app import create_app
from nudge_webhook.config import Config
from nudge_webhook.db import connect


class TestTask12MfiAutoload(unittest.TestCase):
    def test_autoloads_dataset_when_empty(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dataset_path = str(repo_root / "datasets" / "mfi_rates.csv")

        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            cfg = Config(
                port=5000,
                railway_environment=None,
                db_path=db_path,
                twilio_account_sid=None,
                twilio_auth_token=None,
                twilio_validate_signature=False,
                claude_api_key=None,
                claude_model="claude-3-5-sonnet-latest",
                nudge_cooldown_minutes=0,
                nudge_max_per_day=10,
                nudge_max_per_week=10,
                baseline_policy_enabled=False,
                mfi_dataset_path=dataset_path,
                mfi_autoload=True,
            )
            app = create_app(cfg)
            _ = app.test_client()

            conn = connect(db_path)
            try:
                c = int(conn.execute("SELECT COUNT(*) AS c FROM mfi_districts").fetchone()["c"])
            finally:
                conn.close()

            self.assertGreater(c, 0)

