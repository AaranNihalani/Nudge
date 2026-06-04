from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import nudge_webhook.bot as bot_module
from nudge_webhook.app import create_app
from nudge_webhook.config import Config
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
        claude_api_key=None,
        claude_model="claude-3-5-sonnet-latest",
        nudge_cooldown_minutes=0,
        nudge_max_per_day=25,
        nudge_max_per_week=100,
        baseline_policy_enabled=True,
        policy_mode="baseline",
    )


def _chat(client, session_id: str, message: str) -> str:
    r = client.post("/api/chat", json={"session_id": session_id, "message": message})
    return r.get_json()["reply"]


class TestTask10E2ELocalFixtures(unittest.TestCase):
    def test_end_to_end_smoke(self) -> None:
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
        session = "user-e2e-001"
        try:
            with tempfile.TemporaryDirectory() as td:
                db_path = str(Path(td) / "test.sqlite3")
                dataset_path = str(Path(td) / "mfi_rates_test.csv")
                _write_test_dataset_csv(dataset_path)
                init_and_migrate(db_path)
                load_dataset_into_sqlite(db_path, dataset_path, replace=True)

                app = create_app(_make_config(db_path))
                client = app.test_client()

                r0 = _chat(client, session, "hello")
                self.assertIn("reply start", r0.lower())

                r1 = _chat(client, session, "START")
                self.assertIn("opted in", r1.lower())
                self.assertIn("district", r1.lower())

                r2 = _chat(client, session, "Kampala")
                self.assertIn("district set", r2.lower())

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

                r3 = _chat(client, session, "I need 5000 for 30 days, interest 5% monthly, from a local moneylender")
                self.assertIn("very costly", r3.lower())
                self.assertIn("Kampala", r3)

                conn = connect(db_path)
                try:
                    user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", (f"web:{session}",)).fetchone()["id"])
                    event_count = int(
                        conn.execute("SELECT COUNT(*) AS c FROM parsed_events WHERE user_id = ? AND event_type='borrow_intent'", (user_id,)).fetchone()["c"]
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

        finally:
            bot_module.call_json_with_retries = original_call_json


if __name__ == "__main__":
    unittest.main()
