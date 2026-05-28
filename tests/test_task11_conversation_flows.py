from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nudge_webhook.bot as bot_module
from nudge_webhook.bot import InboundMessage, process_twilio_inbound
from nudge_webhook.config import Config
from nudge_webhook.db import connect, init_and_migrate
from nudge_webhook.nudge_content import lender_detail_fallback


def _make_cfg(
    db_path: str,
    *,
    verbose_replies: bool = False,
    baseline_policy_enabled: bool = True,
    policy_mode: str = "baseline",
) -> Config:
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
        baseline_policy_enabled=bool(baseline_policy_enabled),
        policy_mode=str(policy_mode),
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
    for lender in ("A", "B", "C"):
        conn.execute("INSERT OR IGNORE INTO mfi_lenders(name) VALUES (?)", (lender,))
    district_id = int(conn.execute("SELECT id FROM mfi_districts WHERE name = ?", (district,)).fetchone()["id"])
    for lender, rate in (("A", 24.0), ("B", 18.0), ("C", 20.0)):
        lender_id = int(conn.execute("SELECT id FROM mfi_lenders WHERE name = ?", (lender,)).fetchone()["id"])
        conn.execute(
            "INSERT OR REPLACE INTO mfi_rates(district_id, lender_id, rate_apr) VALUES (?, ?, ?)",
            (district_id, lender_id, rate),
        )


def _seed_single_mfi(conn, *, district: str = "D") -> None:
    conn.execute("INSERT OR IGNORE INTO mfi_districts(name) VALUES (?)", (district,))
    conn.execute("INSERT OR IGNORE INTO mfi_lenders(name) VALUES ('Solo')")
    district_id = int(conn.execute("SELECT id FROM mfi_districts WHERE name = ?", (district,)).fetchone()["id"])
    lender_id = int(conn.execute("SELECT id FROM mfi_lenders WHERE name = 'Solo'").fetchone()["id"])
    conn.execute(
        "INSERT OR REPLACE INTO mfi_rates(district_id, lender_id, rate_apr) VALUES (?, ?, ?)",
        (district_id, lender_id, 21.0),
    )


def _seed_named_mfi(conn, *, district: str = "D") -> None:
    conn.execute("INSERT OR IGNORE INTO mfi_districts(name) VALUES (?)", (district,))
    lenders = (
        ("Midland Microfin Limited", 22.50),
        ("Satin Creditcare Network Limited", 25.49),
        ("Other Regulated Option", 28.00),
    )
    for lender, _ in lenders:
        conn.execute("INSERT OR IGNORE INTO mfi_lenders(name) VALUES (?)", (lender,))
    district_id = int(conn.execute("SELECT id FROM mfi_districts WHERE name = ?", (district,)).fetchone()["id"])
    for lender, rate in lenders:
        lender_id = int(conn.execute("SELECT id FROM mfi_lenders WHERE name = ?", (lender,)).fetchone()["id"])
        conn.execute(
            "INSERT OR REPLACE INTO mfi_rates(district_id, lender_id, rate_apr) VALUES (?, ?, ?)",
            (district_id, lender_id, float(rate)),
        )


class TestTask11ConversationFlows(unittest.TestCase):
    def test_lender_detail_without_amount_prompts_for_loan_details(self) -> None:
        reply = lender_detail_fallback(
            option={
                "lender": "Solo",
                "rate_apr": 24.0,
                "district": "D",
                "option_count": 1,
            },
            rank=1,
            district="D",
        )
        self.assertIn("If you tell me the loan amount and how long you need it for", reply)
        self.assertIn("Reply with something like: 5000 for 30 days.", reply)
        self.assertNotIn("How does that payment feel", reply)

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

    def test_policy_off_still_parses_loan_and_returns_costed_options(self) -> None:
        def stub_call_json_with_retries(cfg: Config, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], str] | None:
            _ = (cfg, system_prompt, user_prompt)
            return (
                {
                    "schema_version": 1,
                    "intent": True,
                    "confidence": 0.95,
                    "amount_inr": 500000,
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

                cfg = _make_cfg(db_path, baseline_policy_enabled=False, policy_mode="off")
                now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
                from_e164 = "+15550001112"

                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)
                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICT D"), now=now)

                reply = process_twilio_inbound(
                    cfg, db_path=db_path, inbound=_inbound(from_e164, "Need 5 lakh for 30 days with moneylender"), now=now
                )
                self.assertIn("1) B", reply)
                self.assertIn("repay about INR", reply)
                self.assertIn("interest about INR", reply)

                conn = connect(db_path)
                try:
                    user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", (from_e164,)).fetchone()["id"])
                    row = conn.execute(
                        """
                        SELECT amount_inr, tenure_days, lender_type
                        FROM parsed_events
                        WHERE user_id = ? AND event_type = 'borrow_intent'
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(float(row["amount_inr"]), 500000.0)
                    self.assertEqual(int(row["tenure_days"]), 30)
                    self.assertEqual(str(row["lender_type"]), "moneylender")
                finally:
                    conn.close()
        finally:
            bot_module.call_json_with_retries = original_call_json

    def test_lender_selection_by_partial_name_does_not_trigger_intent_false(self) -> None:
        def stub_call_json_with_retries(cfg: Config, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], str] | None:
            _ = (cfg, system_prompt, user_prompt)
            return (
                {
                    "schema_version": 1,
                    "intent": True,
                    "confidence": 0.95,
                    "amount_inr": 5000000,
                    "tenure_days": 3650,
                    "interest_rate_apr": 50.0,
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
                    _seed_named_mfi(conn, district="D")
                    conn.commit()
                finally:
                    conn.close()

                cfg = _make_cfg(db_path)
                now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
                from_e164 = "+15550005555"

                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)
                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICT D"), now=now)

                reply = process_twilio_inbound(
                    cfg,
                    db_path=db_path,
                    inbound=_inbound(from_e164, "need 50 lakh for 10 years from moneylender with less than 50% apr"),
                    now=now,
                )
                self.assertIn("top local regulated options", reply.lower())
                self.assertIn("1) Midland Microfin Limited", reply)

                opened = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "ok, tell me about midland"), now=now)
                self.assertIn("Option 1: Midland Microfin Limited", opened)
                self.assertNotIn("intent=false", opened.lower())

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
                    self.assertIsNone(row["interest_rate_apr"])
                finally:
                    conn.close()
        finally:
            bot_module.call_json_with_retries = original_call_json

    def test_selected_option_amount_followup_refreshes_cost_breakdown(self) -> None:
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

            cfg = _make_cfg(db_path, baseline_policy_enabled=False, policy_mode="off")
            now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
            from_e164 = "+15550001113"

            process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)
            process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICT D"), now=now)
            process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "hello"), now=now)

            option_reply = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "1"), now=now)
            self.assertIn("Reply with something like: 5000 for 30 days.", option_reply)

            refreshed = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "5 lakh for 30 days"), now=now)
            self.assertIn("Option 1: B", refreshed)
            self.assertIn("INR 90,000 interest over a year", refreshed)
            self.assertIn("total repayment is about INR 507,397 before fees", refreshed)

    def test_missing_interest_still_persists_and_suggests_top_local_options(self) -> None:
        def stub_call_json_with_retries(cfg: Config, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], str] | None:
            _ = (cfg, system_prompt, user_prompt)
            return (
                {
                    "schema_version": 1,
                    "intent": True,
                    "confidence": 0.9,
                    "amount_inr": 500000,
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

                r1 = process_twilio_inbound(
                    cfg, db_path=db_path, inbound=_inbound(from_e164, "Need 5 lakh for 30 days with moneylender"), now=now
                )
                self.assertIn("top local regulated options", r1.lower())
                self.assertIn("reply 1, 2, or 3", r1.lower())
                self.assertIn("1) B", r1)
                self.assertIn("2) C", r1)
                self.assertIn("3) A", r1)
                self.assertIn("repay about INR", r1)
                self.assertIn("interest about INR", r1)
                self.assertIn("These estimates assume APR-only simple interest", r1)
                self.assertNotIn("quoted rate", r1.lower())
                self.assertNotIn("save ~", r1.lower())

                r2 = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "2"), now=now)
                self.assertIn("Option 2: C", r2)
                self.assertIn("Indicative rate", r2)
                self.assertIn("20% APR works out to about INR 100,000 interest over a year", r2)
                self.assertIn("INR 8,333 interest per month", r2)
                self.assertIn("total repayment is about INR 508,219 before fees", r2)
                self.assertIn("These numbers use APR only", r2)
                self.assertIn("per month", r2)
                self.assertIn("I don’t have a verified phone/email", r2)

                r3 = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "That monthly payment is too high"), now=now)
                self.assertIn("too high", r3.lower())
                self.assertIn("Reply 1, 2, or 3", r3)

                conn = connect(db_path)
                try:
                    user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", (from_e164,)).fetchone()["id"])
                    events = int(
                        conn.execute(
                            "SELECT COUNT(*) AS c FROM parsed_events WHERE user_id = ? AND event_type='borrow_intent'",
                            (user_id,),
                        ).fetchone()["c"]
                    )
                    self.assertEqual(events, 1)
                    feedback = int(
                        conn.execute(
                            "SELECT COUNT(*) AS c FROM user_actions WHERE user_id = ? AND action_type = 'lender_option_feedback'",
                            (user_id,),
                        ).fetchone()["c"]
                    )
                    self.assertEqual(feedback, 1)
                    row = conn.execute(
                        """
                        SELECT amount_inr, interest_rate_apr
                        FROM parsed_events
                        WHERE user_id = ? AND event_type='borrow_intent'
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(float(row["amount_inr"]), 500000.0)
                    self.assertIsNone(row["interest_rate_apr"])
                    draft = conn.execute(
                        "SELECT borrow_draft_json FROM user_sessions WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    self.assertIsNotNone(draft)
                    self.assertIsNone(draft["borrow_draft_json"])
                finally:
                    conn.close()
        finally:
            bot_module.call_json_with_retries = original_call_json

    def test_single_lender_option_does_not_prompt_for_missing_options(self) -> None:
        def stub_call_json_with_retries(cfg: Config, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], str] | None:
            _ = (cfg, system_prompt, user_prompt)
            return (
                {
                    "schema_version": 1,
                    "intent": True,
                    "confidence": 0.9,
                    "amount_inr": 500000,
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
                    _seed_single_mfi(conn, district="D")
                    conn.commit()
                finally:
                    conn.close()

                cfg = _make_cfg(db_path)
                now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
                from_e164 = "+15550002223"

                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "START"), now=now)
                process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "DISTRICT D"), now=now)

                reply = process_twilio_inbound(
                    cfg, db_path=db_path, inbound=_inbound(from_e164, "Need 5 lakh for 30 days with moneylender"), now=now
                )
                self.assertIn("1) Solo", reply)
                self.assertIn("Reply 1 to explore this option", reply)
                self.assertNotIn("Reply 1, 2, or 3", reply)
                self.assertNotIn("2)", reply)

                invalid = process_twilio_inbound(cfg, db_path=db_path, inbound=_inbound(from_e164, "2"), now=now)
                self.assertIn("I found 1 option", invalid)
                self.assertIn("Reply 1", invalid)
                self.assertNotIn("1, 2, or 3", invalid)
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
                self.assertNotIn("[status]", reply)
        finally:
            bot_module.call_json_with_retries = original_call_json


if __name__ == "__main__":
    unittest.main()
