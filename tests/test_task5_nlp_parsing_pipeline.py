from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nudge_webhook.db import connect, init_and_migrate
from nudge_webhook.nlp import (
    BorrowIntentValidationError,
    persist_borrow_intent_event,
    strict_json_object,
    validate_borrow_intent_payload,
)


class TestTask5NlpParsingPipeline(unittest.TestCase):
    def test_schema_validation_accepts_and_normalises(self) -> None:
        payload = validate_borrow_intent_payload(
            {
                "schema_version": 1,
                "intent": True,
                "confidence": 0.82,
                "amount_inr": 5000,
                "tenure_days": 30,
                "interest_rate_apr": 48.0,
                "lender_name": "Local lender",
                "lender_type": "informal",
                "negotiation_stage": "asking",
            }
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["intent"], True)
        self.assertEqual(payload["amount_inr"], 5000.0)

        with self.assertRaises(BorrowIntentValidationError):
            validate_borrow_intent_payload({**payload, "extra": 1})

    def test_strict_json_object_rejects_non_json(self) -> None:
        with self.assertRaises(BorrowIntentValidationError):
            strict_json_object("```json\n{\"a\":1}\n```")

        self.assertEqual(strict_json_object('{"a": 1}')["a"], 1)

    def test_persist_parsed_event_stores_extracted_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)

            conn = connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO users(phone_e164, consent_status) VALUES (?, 'opted_in')", ("+555",))
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+555",)).fetchone()["id"])
                cursor = conn.execute(
                    """
                    INSERT INTO raw_messages(user_id, direction, channel, from_addr, to_addr, body, payload_json)
                    VALUES (?, 'inbound', 'whatsapp', 'whatsapp:+555', 'whatsapp:+222', 'need 5k loan', ?)
                    """,
                    (user_id, json.dumps({"stub": True})),
                )
                raw_message_id = int(cursor.lastrowid)
                conn.commit()
            finally:
                conn.close()

            event_id = persist_borrow_intent_event(
                db_path,
                user_id=user_id,
                raw_message_id=raw_message_id,
                model="test-model",
                payload={
                    "schema_version": 1,
                    "intent": True,
                    "confidence": 0.9,
                    "amount_inr": 5000,
                    "tenure_days": 30,
                    "interest_rate_apr": 60,
                    "lender_name": None,
                    "lender_type": "unknown",
                    "negotiation_stage": "considering",
                },
            )
            self.assertGreater(event_id, 0)

            conn = connect(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT event_type, confidence, intent, amount_inr, tenure_days, interest_rate_apr, lender_type
                    FROM parsed_events
                    WHERE id = ?
                    """,
                    (event_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["event_type"]), "borrow_intent")
                self.assertAlmostEqual(float(row["confidence"]), 0.9, places=4)
                self.assertEqual(int(row["intent"]), 1)
                self.assertEqual(float(row["amount_inr"]), 5000.0)
                self.assertEqual(int(row["tenure_days"]), 30)
                self.assertEqual(float(row["interest_rate_apr"]), 60.0)
                self.assertEqual(str(row["lender_type"]), "unknown")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

