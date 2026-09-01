from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class NotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original_paths = (main.CONFIG_PATH, main.HISTORY_PATH, main.NOTIFICATION_HISTORY_PATH)
        main.CONFIG_PATH = root / "config.json"
        main.HISTORY_PATH = root / "history.json"
        main.NOTIFICATION_HISTORY_PATH = root / "notifications.json"

    def tearDown(self) -> None:
        main.CONFIG_PATH, main.HISTORY_PATH, main.NOTIFICATION_HISTORY_PATH = self.original_paths
        self.temp.cleanup()

    def configure(self, recipients: list[str] | None = None) -> None:
        config = main._default_config()
        config["notifications"]["recipients"] = recipients or []
        main._save_config(config)

    def job(self, outcome: str = "success") -> dict:
        return {
            "id": "run-123",
            "status": "completed" if outcome != "failure" else "failed",
            "outcome": outcome,
            "trigger": "daily",
            "source": "/source",
            "destination": "/destination",
            "started_at": "2026-09-01T06:00:00-04:00",
            "finished_at": "2026-09-01T06:02:00-04:00",
            "processed_files": 12,
            "copied_files": 4,
            "linked_files": 8,
            "warning_files": 1 if outcome == "warning" else 0,
            "copied_bytes": 4096,
            "error": "Destination unavailable" if outcome == "failure" else None,
        }

    def test_success_event_records_sent_delivery(self) -> None:
        self.configure(["alex@example.com"])
        with patch.object(main, "_send_email") as sender:
            event = main._dispatch_backup_notification(self.job("success"))
        sender.assert_called_once()
        self.assertEqual(event["severity"], "success")
        self.assertEqual(event["delivery_status"], "sent")
        persisted = json.loads(main.NOTIFICATION_HISTORY_PATH.read_text())
        self.assertEqual(persisted[0]["backup_run_id"], "run-123")

    def test_missing_recipient_is_logged_without_email_attempt(self) -> None:
        self.configure([])
        with patch.object(main, "_send_email") as sender:
            event = main._dispatch_backup_notification(self.job("failure"))
        sender.assert_not_called()
        self.assertEqual(event["severity"], "failure")
        self.assertEqual(event["delivery_status"], "failed")
        self.assertIn("No notification recipients", event["delivery_error"])

    def test_warning_preference_can_suppress_delivery(self) -> None:
        config = main._default_config()
        config["notifications"].update({"recipients": ["alex@example.com"], "on_warning": False})
        main._save_config(config)
        with patch.object(main, "_send_email") as sender:
            event = main._dispatch_backup_notification(self.job("warning"))
        sender.assert_not_called()
        self.assertEqual(event["delivery_status"], "suppressed")
        self.assertIn("1 file system item", event["reason"])


if __name__ == "__main__":
    unittest.main()
