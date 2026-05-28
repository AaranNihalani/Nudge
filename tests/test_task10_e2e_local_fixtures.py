from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nudge_webhook.bot as bot_module
from nudge_webhook.app import create_app
from nudge_webhook.config import Config
from nudge_webhook.daily_runner import run_daily_decisions
from nudge_webhook.db import connect, init_and_migrate
from nudge_webhook.mfi import load_dataset_into_sqlite


def _write_test_dataset_csv(path: str) -> None:
    Path(path).write_text(
        "\n".join(
            [
                "district,lender,rate_apr,effective_date,source",
                "Kampala,GreenField Finance,20.5,2025-01-01,test",
                "Kampala,Sunrise MFI,18.0,2025-01-01,test",
                "Kampala,Unity Credit,18.0,2025-01-01,test",
                "Gulu,GreenField Finance,19.5,2025-01-01,test",
                "Gulu,RiverBank Microcredit,19.5,2025-01-01,test",
                "Gulu,Valley Lending,22.0,2025-01-01,test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _make_config(db_path: str) -> Config:
    return Config(
        port=5000,
        railway_environment=None,
        db_path=db_path,
        twilio_account_sid=None,
        twilio_auth_token=None,
        twilio_validate_signature=False,
        claude_api_key=None,
        claude_model="claude-3-5-sonnet-latest",
        nudge_cooldown_minutes=0,
        nudge_max_per_day=25,
        nudge_max_per_week=100,
        baseline_policy_enabled=True,
        policy_mode="baseline",
    )


def _load_twilio_fixture(name: str) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "tests" / "fixtures" / "twilio" / name
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return {str(k): str(v) for k, v in payload.items()}


class TestTask10E2ELocalFixtures(unittest.TestCase):
    def test_end_to_end_smoke_with_recorded_twilio_payloads(self) -> None:
        def stub_call_json_with_retries(cfg: Config, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], str] | None:
            _ = (cfg, system_prompt, user_prompt)
            return (
                {
                    "schema_version": 1,
                    "intent": True,
                    "confidence": 0.9,
                    "amount_inr": 5000,
                    "tenure_days": 30,
                    "interest_rate_apr": 60.0,
                    "lender_name": None,
                    "lender_type": "moneylender",
                    "negotiation_stage": "asking",
                },
                "fixture-llm",
            )

        original_call_json = bot_module.call_json_with_retries
        bot_module.call_json_with_retries = stub_call_json_with_retries
        try:
            with tempfile.TemporaryDirectory() as td:
                db_path = str(Path(td) / "test.sqlite3")
                dataset_path = str(Path(td) / "mfi_rates_test.csv")
                _write_test_dataset_csv(dataset_path)
                init_and_migrate(db_path)
                load_dataset_into_sqlite(db_path, dataset_path, replace=True)

                app = create_app(_make_config(db_path))
                client = app.test_client()

                r0 = client.post("/twilio", data=_load_twilio_fixture("whatsapp_inbound_hello.json"))
                self.assertIn("reply start", r0.data.decode("utf-8").lower())

                r1 = client.post("/twilio", data=_load_twilio_fixture("whatsapp_inbound_start.json"))
                self.assertIn("opted in", r1.data.decode("utf-8").lower())
                self.assertIn("district", r1.data.decode("utf-8").lower())

                r2 = client.post("/twilio", data=_load_twilio_fixture("whatsapp_inbound_district.json"))
                self.assertIn("district set", r2.data.decode("utf-8").lower())

                alternatives = client.get("/mfi/alternatives?district=Kampala&current_rate=60&n=3")
                payload = alternatives.get_json()
                self.assertIsInstance(payload, dict)
                results = payload.get("results")
                self.assertIsInstance(results, list)
                self.assertGreater(len(results), 0)

                top_local = client.get("/mfi/alternatives?district=Kampala&n=3")
                top_payload = top_local.get_json()
                self.assertIsInstance(top_payload, dict)
                top_results = top_payload.get("results")
                self.assertIsInstance(top_results, list)
                self.assertEqual([r["lender"] for r in top_results], ["Sunrise MFI", "Unity Credit", "GreenField Finance"])

                r3 = client.post("/twilio", data=_load_twilio_fixture("whatsapp_inbound_borrow_message.json"))
                body = r3.data.decode("utf-8")
                self.assertIn("very costly", body.lower())
                self.assertIn("Kampala", body)

                conn = connect(db_path)
                try:
                    user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+15551230001",)).fetchone()["id"])
                    event_count = int(
                        conn.execute("SELECT COUNT(*) AS c FROM parsed_events WHERE user_id = ? AND event_type='borrow_intent'", (user_id,)).fetchone()[
                            "c"
                        ]
                    )
                    self.assertGreaterEqual(event_count, 1)

                    nudge = conn.execute(
                        "SELECT nudge_type, policy_name, trigger FROM nudges WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                        (user_id,),
                    ).fetchone()
                    self.assertIsNotNone(nudge)
                    self.assertEqual(str(nudge["nudge_type"]), "alert")
                    self.assertEqual(str(nudge["policy_name"]), "baseline-threshold")
                    self.assertEqual(str(nudge["trigger"]), "event")
                finally:
                    conn.close()

                now = datetime.now(timezone.utc).replace(microsecond=0)
                run = run_daily_decisions(_make_config(db_path), db_path=db_path, now=now)
                self.assertFalse(run.skipped)

                conn = connect(db_path)
                try:
                    queued = conn.execute(
                        """
                        SELECT delivery_status, trigger
                        FROM nudges
                        WHERE user_id = ?
                            AND trigger = 'daily'
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    ).fetchone()
                    self.assertIsNotNone(queued)
                    self.assertEqual(str(queued["delivery_status"]), "queued")
                finally:
                    conn.close()
        finally:
            bot_module.call_json_with_retries = original_call_json


if __name__ == "__main__":
    unittest.main()
