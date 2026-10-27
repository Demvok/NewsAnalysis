from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

os.environ["MANAGEMENT_DATA_DIR"] = tempfile.mkdtemp()
os.environ["MANAGEMENT_DATABASE_PATH"] = str(Path(os.environ["MANAGEMENT_DATA_DIR"]) / "management.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.database import ManagementDatabase  # noqa: E402


class ManagementPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        main.database = ManagementDatabase(Path(self.directory.name) / "management.db")
        main.database.initialize()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.directory.cleanup()

    def test_duplicate_event_is_not_counted_twice(self):
        event_id = str(uuid4())
        payload = {
            "event_id": event_id,
            "service": "analysis",
            "event_type": "llm_call",
            "status": "completed",
            "duration_ms": 100,
            "input_tokens": 10,
            "output_tokens": 20,
        }
        self.assertEqual(self.client.post("/internal/events", json=payload).status_code, 202)
        self.assertEqual(self.client.post("/internal/events", json=payload).status_code, 202)
        metrics = self.client.get("/api/metrics/llm").json()
        self.assertEqual(metrics["call_count"], 1)
        self.assertEqual(metrics["output_tokens_measured"], 20)

    def test_unmeasured_tokens_are_not_presented_as_measured(self):
        response = self.client.post(
            "/internal/events",
            json={
                "service": "analysis",
                "event_type": "llm_call",
                "status": "completed",
                "duration_ms": 100,
                "input_tokens_estimated": 12,
                "output_tokens_estimated": 8,
            },
        )
        self.assertEqual(response.status_code, 202)
        metrics = self.client.get("/api/metrics/llm").json()
        self.assertEqual(metrics["input_tokens_measured"], 0)
        self.assertEqual(metrics["output_tokens_measured"], 0)
        self.assertEqual(metrics["input_tokens_estimated"], 12)
        self.assertIsNone(metrics["throughput_tokens_per_second_average"])

    def test_log_filters_use_structured_correlation_fields(self):
        trace_id = str(uuid4())
        self.client.post(
            "/internal/events",
            json={
                "service": "analysis",
                "event_type": "pipeline_stage",
                "status": "failed",
                "trace_id": trace_id,
                "error_category": "timeout",
                "error_detail": "request timed out",
            },
        )
        response = self.client.get(f"/api/logs?trace_id={trace_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["trace_id"], trace_id)
        errors = self.client.get("/api/logs/errors").json()
        self.assertEqual(errors[0]["error_key"], "timeout")


if __name__ == "__main__":
    unittest.main()
