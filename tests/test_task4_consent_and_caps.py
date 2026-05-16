from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nudge_webhook.app import create_app
from nudge_webhook.config import Config
from nudge_webhook.db import connect, init_and_migrate
from nudge_webhook.mfi import load_dataset_into_sqlite


def _make_config(db_path: str, *, cooldown_minutes: int = 360, max_day: int = 2, max_week: int = 5) -> Config:
    return Config(
        port=5000,
        railway_environment=None,
        db_path=db_path,
        twilio_account_sid=None,
        twilio_auth_token=None,
        twilio_validate_signature=False,
        claude_api_key=None,
        claude_model="claude-3-5-sonnet-latest",
        nudge_cooldown_minutes=cooldown_minutes,
        nudge_max_per_day=max_day,
        nudge_max_per_week=max_week,
        baseline_policy_enabled=False,
    )


class TestTask4ConsentAndCaps(unittest.TestCase):
    def test_start_stop_and_district_flow(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dataset_path = str(repo_root / "datasets" / "mfi_rates.csv")

        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)
            load_dataset_into_sqlite(db_path, dataset_path, replace=True)

            app = create_app(_make_config(db_path))
            client = app.test_client()

            r1 = client.post(
                "/twilio",
                data={"From": "whatsapp:+111", "To": "whatsapp:+222", "Body": "hello", "MessageSid": "SM1"},
            )
            self.assertIn("reply start", r1.data.decode("utf-8").lower())

            conn = connect(db_path)
            try:
                row = conn.execute("SELECT consent_status, district FROM users WHERE phone_e164 = ?", ("+111",)).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(str(row["consent_status"]), "unknown")
                self.assertIsNone(row["district"])
            finally:
                conn.close()

            r2 = client.post(
                "/twilio",
                data={"From": "whatsapp:+111", "To": "whatsapp:+222", "Body": "START", "MessageSid": "SM2"},
            )
            self.assertIn("opted in", r2.data.decode("utf-8").lower())
            self.assertIn("district", r2.data.decode("utf-8").lower())

            r3 = client.post(
                "/twilio",
                data={"From": "whatsapp:+111", "To": "whatsapp:+222", "Body": "Kampala", "MessageSid": "SM3"},
            )
            self.assertIn("district set to Kampala", r3.data.decode("utf-8"))

            r4 = client.post(
                "/twilio",
                data={"From": "whatsapp:+111", "To": "whatsapp:+222", "Body": "STOP", "MessageSid": "SM4"},
            )
            self.assertIn("opted out", r4.data.decode("utf-8").lower())

            conn = connect(db_path)
            try:
                row = conn.execute("SELECT consent_status, district FROM users WHERE phone_e164 = ?", ("+111",)).fetchone()
                self.assertEqual(str(row["consent_status"]), "opted_out")
                self.assertEqual(str(row["district"]), "Kampala")
            finally:
                conn.close()

    def test_nudge_cooldown_blocks_second_nudge(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dataset_path = str(repo_root / "datasets" / "mfi_rates.csv")

        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)
            load_dataset_into_sqlite(db_path, dataset_path, replace=True)

            app = create_app(_make_config(db_path, cooldown_minutes=60, max_day=10, max_week=10))
            client = app.test_client()

            client.post("/twilio", data={"From": "whatsapp:+333", "To": "whatsapp:+222", "Body": "START"})
            client.post("/twilio", data={"From": "whatsapp:+333", "To": "whatsapp:+222", "Body": "Kampala"})

            first = client.post("/twilio", data={"From": "whatsapp:+333", "To": "whatsapp:+222", "Body": "ping"})
            self.assertIn("In Kampala", first.data.decode("utf-8"))

            second = client.post("/twilio", data={"From": "whatsapp:+333", "To": "whatsapp:+222", "Body": "ping2"})
            self.assertIn("low-frequency", second.data.decode("utf-8"))

            conn = connect(db_path)
            try:
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+333",)).fetchone()["id"])
                c = int(conn.execute("SELECT COUNT(*) AS c FROM nudges WHERE user_id = ?", (user_id,)).fetchone()["c"])
                self.assertEqual(c, 1)
            finally:
                conn.close()

    def test_daily_cap_blocks_after_limit(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dataset_path = str(repo_root / "datasets" / "mfi_rates.csv")

        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test.sqlite3")
            init_and_migrate(db_path)
            load_dataset_into_sqlite(db_path, dataset_path, replace=True)

            app = create_app(_make_config(db_path, cooldown_minutes=0, max_day=1, max_week=10))
            client = app.test_client()

            client.post("/twilio", data={"From": "whatsapp:+444", "To": "whatsapp:+222", "Body": "START"})
            client.post("/twilio", data={"From": "whatsapp:+444", "To": "whatsapp:+222", "Body": "Kampala"})

            first = client.post("/twilio", data={"From": "whatsapp:+444", "To": "whatsapp:+222", "Body": "ping"})
            self.assertIn("In Kampala", first.data.decode("utf-8"))

            second = client.post("/twilio", data={"From": "whatsapp:+444", "To": "whatsapp:+222", "Body": "ping2"})
            self.assertIn("low-frequency", second.data.decode("utf-8"))

            conn = connect(db_path)
            try:
                user_id = int(conn.execute("SELECT id FROM users WHERE phone_e164 = ?", ("+444",)).fetchone()["id"])
                c = int(conn.execute("SELECT COUNT(*) AS c FROM nudges WHERE user_id = ?", (user_id,)).fetchone()["c"])
                self.assertEqual(c, 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
