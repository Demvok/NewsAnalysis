from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["MANAGEMENT_DATA_DIR"] = tempfile.mkdtemp()
os.environ["MANAGEMENT_DATABASE_PATH"] = str(Path(os.environ["MANAGEMENT_DATA_DIR"]) / "management.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.database import ManagementDatabase  # noqa: E402


class TelemetryBulkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        main.database = ManagementDatabase(Path(self.directory.name) / "management.db")
        main.database.initialize()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.directory.cleanup()

    def test_bulk_ingestion_is_single_request_and_persists_all_events(self):
        response = self.client.post(
            "/internal/events/bulk",
            json=[
                {"service": "analysis", "event_type": "one"},
                {"service": "analysis", "event_type": "two", "status": "failed"},
            ],
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["accepted"], 2)
        with main.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0], 2)

    def test_malformed_bulk_event_is_rejected_without_partial_insert(self):
        response = self.client.post(
            "/internal/events/bulk",
            json=[
                {"service": "analysis", "event_type": "valid"},
                {"service": "analysis", "event_type": "invalid", "duration_ms": -1},
            ],
        )
        self.assertEqual(response.status_code, 422)
        with main.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0], 0)

    def test_bulk_ingestion_is_idempotent_for_duplicate_event_ids(self):
        event = {"event_id": "00000000-0000-0000-0000-000000000001", "service": "analysis", "event_type": "one"}
        first = self.client.post("/internal/events/bulk", json=[event])
        second = self.client.post("/internal/events/bulk", json=[event])
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        with main.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0], 1)
