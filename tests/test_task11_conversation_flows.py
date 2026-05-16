from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nudge_webhook.bot as bot_module
from nudge_webhook.bot import InboundMessage, process_twilio_inbound
from nudge_webhook.config import Config
from nudge_webhook.db import connect, init_and_migrate


def _make_cfg(db_path: str, *, verbose_replies: bool = False) -> Config:
    return Config(
        port=5000,
        railway_environment=None,
        db_path=db_path,
        twilio_account_sid=None,
        twilio_auth_token=None,
        twilio_validate_signature=False,
        claude_api_key=None,
        claude_model="test",
        nudge_cooldown_minutes=0,
        nudge_max_per_day=100,
        nudge_max_per_week=500,
        baseline_policy_enabled=True,
        policy_mode="baseline",
        verbose_replies=bool(verbose_replies),
    )


def _inbound(from_e164: str, body: str) -> InboundMessage:
    return InboundMessage(
        from_addr=f"whatsapp:{from_e164}",
        to_addr="whatsapp:+222",
        body=body,
        twilio_message_sid=None,
        payload={"From": f"whatsapp:{from_e164}", "Body": body},
    )


def _seed_minimal_mfi(conn, *, district: str = "D") -> None:
    conn.execute("INSERT OR IGNORE INTO mfi_districts(name) VALUES (?)", (district,))
    conn.execute("INSERT OR IGNORE INTO mfi_lenders(name) VALUES ('A')")
    district_id = int(conn.execute("SELECT id FROM mfi_districts WHERE name = ?", (district,)).fetchone()["id"])
    lender_id = int(conn.execute("SELECT id FROM mfi_lenders WHERE name='A'").fetchone()["id"])
    conn.execute(
        "INSERT OR REPLACE INTO mfi_rates(district_id, lender_id, rate_apr) VALUES (?, ?, ?)",
        (district_id, lender_id, 24.0),
    )


class TestTask11ConversationFlows(unittest.TestCase):
    def test_districts_paging_with_more(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                for i in range(65):
                    conn.execute("INSERT OR IGNORE INTO mfi_districts(name) VALUES (?)", (f"D{i:03d}",))
                conn.commit()
            finally:
                conn.close()

            cfg = _make_cfg(db_path)
            now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
            from_e164 = "+15550001111"

            process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)

            first = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICTS"), now=now)
            self.assertIn("D000", first)
            self.assertIn("D029", first)
            self.assertNotIn("D030", first)
            self.assertIn("reply more", first.lower())

            second = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "MORE"), now=now)
            self.assertIn("D030", second)

    def test_missing_field_clarifying_question_then_resume(self) -> None:
        def stub_call_json_with_retries(cfg: Config, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], str] | None:
            _ = (cfg, system_prompt, user_prompt)
            return (
                {
                    "schema_version": 1,
                    "intent": True,
                    "confidence": 0.9,
                    "amount_inr": 5000,
                    "tenure_days": 30,
                    "interest_rate_apr": None,
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
                init_and_migrate(db_path)

                conn = connect(db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    _seed_minimal_mfi(conn, district="D")
                    conn.commit()
                finally:
                    conn.close()

                cfg = _make_cfg(db_path)
                now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
                from_e164 = "+15550002222"

                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)
                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICT D"), now=now)

                r1 = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "Need 5000 for 30 days"), now=now)
                self.assertIn("interest", r1.lower())

                conn = connect(db_path)
                try:
                    user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", (from_e164,)).fetchone()["id"])
                    events = int(
                        conn.execute(
                            "SELECT COUNT(*) AS c FROM parsed_events WHERE user_id = ? AND event_type='borrow_intent'",
                            (user_id,),
                        ).fetchone()["c"]
                    )
                    self.assertEqual(events, 0)
                finally:
                    conn.close()

                r2 = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "5% monthly"), now=now)
                self.assertIn("very costly", r2.lower())

                conn = connect(db_path)
                try:
                    user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", (from_e164,)).fetchone()["id"])
                    row = conn.execute(
                        """
                        SELECT interest_rate_apr
                        FROM parsed_events
                        WHERE user_id = ? AND event_type='borrow_intent'
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertAlmostEqual(float(row["interest_rate_apr"]), 60.0, places=4)
                finally:
                    conn.close()
        finally:
            bot_module.call_json_with_retries = original_call_json

    def test_correction_command_updates_last_parsed_event(self) -> None:
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
                init_and_migrate(db_path)

                conn = connect(db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    _seed_minimal_mfi(conn, district="D")
                    conn.commit()
                finally:
                    conn.close()

                cfg = _make_cfg(db_path)
                now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
                from_e164 = "+15550003333"

                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)
                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICT D"), now=now)

                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "Need 5000 for 30 days at 5% monthly"), now=now)
                reply = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "CORRECT rate=2% monthly"), now=now)
                self.assertIn("updated", reply.lower())

                conn = connect(db_path)
                try:
                    user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", (from_e164,)).fetchone()["id"])
                    row = conn.execute(
                        """
                        SELECT interest_rate_apr
                        FROM parsed_events
                        WHERE user_id = ? AND event_type='borrow_intent'
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertAlmostEqual(float(row["interest_rate_apr"]), 24.0, places=4)
                finally:
                    conn.close()
        finally:
            bot_module.call_json_with_retries = original_call_json

    def test_verbose_loan_echo_in_status(self) -> None:
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
                init_and_migrate(db_path)

                conn = connect(db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    _seed_minimal_mfi(conn, district="D")
                    conn.commit()
                finally:
                    conn.close()

                cfg = _make_cfg(db_path, verbose_replies=True)
                now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
                from_e164 = "+15550004444"

                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)
                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICT D"), now=now)
                reply = process_twilio_inbound(
                    cfg, db_path=db_path, inbound=_inbound(from_e164, "Need 5000 for 30 days at 5% monthly"), now=now
                )
                self.assertIn("[status]", reply)
                self.assertIn("loan=amount=", reply)
        finally:
            bot_module.call_json_with_retries = original_call_json


if __name__ == "__main__":
    unittest.main()

