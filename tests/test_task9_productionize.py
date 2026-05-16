from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nudge_webhook.config import Config
from nudge_webhook.daily_runner import run_daily_decisions
from nudge_webhook.db import connect, init_and_migrate
from nudge_webhook.metrics_export import export_metrics_zip
from nudge_webhook.policy_serving import decide_policy
from nudge_webhook.state import compute_user_state


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


class TestTask9Productionize(unittest.TestCase):
    def test_migration_adds_admin_tables_and_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            info = init_and_migrate(db_path)
            self.assertGreaterEqual(int(info.schema_version), 4)

            conn = connect(db_path)
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('admin_runs','system_kv')"
                ).fetchall()
                self.assertEqual({str(r["name"]) for r in row}, {"admin_runs", "system_kv"})

                cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(nudges)").fetchall()}
                self.assertIn("trigger", cols)
            finally:
                conn.close()

    def test_policy_auto_falls_back_to_baseline_when_no_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)
            now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
            intent_at = now - timedelta(days=1)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO users(phone_e164, consent_status, district) VALUES (?, 'opted_in', ?)", ("+111", "D"))
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+111",)).fetchone()["id"])
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
                baseline_policy_enabled=False,
                policy_mode="auto",
                rl_model_dir=None,
                rl_model_path=None,
                rl_active_version=None,
                twilio_from_addr=None,
                default_channel="whatsapp",
                admin_token=None,
                anon_salt="salt",
            )

            state = compute_user_state(db_path, user_id=user_id, now=now)
            conn = connect(db_path)
            try:
                decision = decide_policy(conn, cfg=cfg, state=state)
            finally:
                conn.close()
            self.assertEqual(decision.policy_name, "baseline-threshold")

    def test_daily_runner_is_idempotent_and_queues_when_no_twilio(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)
            now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
            intent_at = now - timedelta(days=1)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO users(phone_e164, consent_status, district) VALUES (?, 'opted_in', ?)",
                    ("+222", "D"),
                )
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+222",)).fetchone()["id"])
                conn.execute(
                    "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json, received_at) VALUES (?, 'inbound', 'whatsapp', ?, ?, 'hi', ?, ?)",
                    (user_id, "whatsapp:+222", "whatsapp:+999", json.dumps({"stub": True}), _ts(now)),
                )
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
                anon_salt="salt",
            )

            first = run_daily_decisions(cfg, db_path=db_path, now=now)
            self.assertFalse(first.skipped)

            second = run_daily_decisions(cfg, db_path=db_path, now=now)
            self.assertTrue(second.skipped)

            conn = connect(db_path)
            try:
                nudge = conn.execute("SELECT delivery_status, trigger FROM nudges WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
                self.assertIsNotNone(nudge)
                self.assertEqual(str(nudge["delivery_status"]), "queued")
                self.assertEqual(str(nudge["trigger"]), "daily")
            finally:
                conn.close()

    def test_metrics_export_is_anonymized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO users(phone_e164, consent_status, district) VALUES (?, 'opted_in', ?)", ("+333", "D"))
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+333",)).fetchone()["id"])
                conn.execute(
                    "INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json) VALUES (?, 'inbound', 'whatsapp', ?, ?, 'hello', ?)",
                    (user_id, "whatsapp:+333", "whatsapp:+999", json.dumps({"stub": True})),
                )
                conn.commit()
            finally:
                conn.close()

            bundle = export_metrics_zip(db_path=db_path, anon_salt="salt")
            zf = zipfile.ZipFile(io.BytesIO(bundle.data), "r")
            user_csv = zf.read("user_metrics.csv").decode("utf-8")
            self.assertNotIn("+333", user_csv)


if __name__ == "__main__":
    unittest.main()

