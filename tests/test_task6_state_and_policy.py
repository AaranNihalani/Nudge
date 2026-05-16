from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nudge_webhook.bot import InboundMessage, process_twilio_inbound
from nudge_webhook.config import Config
from nudge_webhook.db import connect, init_and_migrate
from nudge_webhook.policy_baseline import decide_baseline
from nudge_webhook.state import compute_user_state


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


class TestTask6StateAndBaselinePolicy(unittest.TestCase):
    def test_compute_state_features(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)

            now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
            borrowed_at = now - timedelta(days=5)
            nudge_at = now - timedelta(days=2)
            inbound_after_nudge_at = now - timedelta(days=1)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO users(phone_e164, consent_status, district) VALUES (?, 'opted_in', ?)",
                    ("+555", "TestDistrict"),
                )
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+555",)).fetchone()["id"])

                conn.execute(
                    """
                    INSERT INTO parsed_events(
                        user_id, raw_message_id, event_type, event_json, confidence, model,
                        intent, amount_inr, tenure_days, interest_rate_apr, lender_name, lender_type, negotiation_stage, parsed_at
                    )
                    VALUES (?, NULL, 'borrow_intent', ?, 0.9, 'test', 1, 5000, 30, 60, NULL, 'unknown', 'borrowed', ?)
                    """,
                    (user_id, json.dumps({"stub": True}), _ts(borrowed_at)),
                )

                conn.execute(
                    """
                    INSERT INTO nudges(user_id, nudge_type, content, policy_name, policy_version, sent_at)
                    VALUES (?, 'suggest_lender', 'hi', 'test', 'v1', ?)
                    """,
                    (user_id, _ts(nudge_at)),
                )

                conn.execute(
                    """
                    INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json, received_at)
                    VALUES (?, 'inbound', 'whatsapp', 'whatsapp:+555', 'whatsapp:+222', 'thanks', ?, ?)
                    """,
                    (user_id, json.dumps({"stub": True}), _ts(inbound_after_nudge_at)),
                )

                conn.commit()
            finally:
                conn.close()

            state = compute_user_state(db_path, user_id=user_id, now=now)
            self.assertIsNotNone(state.days_since_borrow)
            self.assertAlmostEqual(float(state.days_since_borrow or 0.0), 5.0, places=3)
            self.assertEqual(float(state.implied_apr or 0.0), 60.0)
            self.assertIsNotNone(state.debt_burden_proxy)
            self.assertGreater(float(state.debt_burden_proxy or 0.0), 0.0)
            self.assertEqual(state.nudges.total, 1)
            self.assertEqual(state.engagement.engaged_nudges_30d, 1)
            self.assertEqual(state.engagement.nudges_30d, 1)
            self.assertAlmostEqual(float(state.engagement.engagement_rate_30d or 0.0), 1.0, places=3)

    def test_baseline_policy_alert_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)

            now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
            intent_at = now - timedelta(days=1)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO users(phone_e164, consent_status, district) VALUES (?, 'opted_in', ?)", ("+555", "D"))
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+555",)).fetchone()["id"])

                conn.execute("INSERT INTO mfi_districts(name) VALUES ('D')")
                conn.execute("INSERT INTO mfi_lenders(name) VALUES ('A')")
                conn.execute("INSERT INTO mfi_lenders(name) VALUES ('B')")
                district_id = int(conn.execute("SELECT id FROM mfi_districts WHERE name='D'").fetchone()["id"])
                lender_a = int(conn.execute("SELECT id FROM mfi_lenders WHERE name='A'").fetchone()["id"])
                lender_b = int(conn.execute("SELECT id FROM mfi_lenders WHERE name='B'").fetchone()["id"])
                conn.execute(
                    "INSERT INTO mfi_rates(district_id, lender_id, rate_apr) VALUES (?, ?, ?)",
                    (district_id, lender_a, 24.0),
                )
                conn.execute(
                    "INSERT INTO mfi_rates(district_id, lender_id, rate_apr) VALUES (?, ?, ?)",
                    (district_id, lender_b, 30.0),
                )

                conn.execute(
                    """
                    INSERT INTO parsed_events(
                        user_id, raw_message_id, event_type, event_json, confidence, model,
                        intent, amount_inr, tenure_days, interest_rate_apr, lender_name, lender_type, negotiation_stage, parsed_at
                    )
                    VALUES (?, NULL, 'borrow_intent', ?, 0.9, 'test', 1, 5000, 30, 70, NULL, 'unknown', 'asking', ?)
                    """,
                    (user_id, json.dumps({"stub": True}), _ts(intent_at)),
                )
                conn.commit()

                state = compute_user_state(db_path, user_id=user_id, now=now)
                decision = decide_baseline(conn, state=state)
                self.assertEqual(decision.action, "alert")
                self.assertIn("~70", decision.content)
            finally:
                conn.close()

    def test_bot_feature_flag_uses_baseline_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)

            now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
            intent_at = now - timedelta(days=1)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO users(phone_e164, consent_status, district) VALUES (?, 'opted_in', ?)", ("+555", "D"))
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+555",)).fetchone()["id"])

                conn.execute("INSERT INTO mfi_districts(name) VALUES ('D')")
                conn.execute("INSERT INTO mfi_lenders(name) VALUES ('A')")
                district_id = int(conn.execute("SELECT id FROM mfi_districts WHERE name='D'").fetchone()["id"])
                lender_a = int(conn.execute("SELECT id FROM mfi_lenders WHERE name='A'").fetchone()["id"])
                conn.execute(
                    "INSERT INTO mfi_rates(district_id, lender_id, rate_apr) VALUES (?, ?, ?)",
                    (district_id, lender_a, 24.0),
                )

                conn.execute(
                    """
                    INSERT INTO parsed_events(
                        user_id, raw_message_id, event_type, event_json, confidence, model,
                        intent, amount_inr, tenure_days, interest_rate_apr, lender_name, lender_type, negotiation_stage, parsed_at
                    )
                    VALUES (?, NULL, 'borrow_intent', ?, 0.9, 'test', 1, 5000, 30, 70, NULL, 'unknown', 'asking', ?)
                    """,
                    (user_id, json.dumps({"stub": True}), _ts(intent_at)),
                )
                conn.commit()
            finally:
                conn.close()

            cfg = Config(
                port=5000,
                railway_environment=None,
                db_path=db_path,
                twilio_account_sid=None,
                twilio_auth_token=None,
                twilio_validate_signature=False,
                claude_api_key=None,
                claude_model="test",
                nudge_cooldown_minutes=0,
                nudge_max_per_day=10,
                nudge_max_per_week=50,
                baseline_policy_enabled=True,
            )

            inbound = InboundMessage(
                from_addr="whatsapp:+555",
                to_addr="whatsapp:+222",
                body="hello",
                twilio_message_sid=None,
                payload={"From": "whatsapp:+555", "Body": "hello"},
            )
            reply = process_twilio_inbound(cfg, db_path=db_path, inbound=inbound, now=now)
            self.assertIn("~70", reply)


if __name__ == "__main__":
    unittest.main()
