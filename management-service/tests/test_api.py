from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["MANAGEMENT_DATA_DIR"] = tempfile.mkdtemp()
os.environ["MANAGEMENT_DATABASE_PATH"] = str(Path(os.environ["MANAGEMENT_DATA_DIR"]) / "management.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.models import ServiceDependency  # noqa: E402


class ManagementApiTests(unittest.TestCase):
    def test_health_distinguishes_unknown_llm_provider(self):
        dependencies = [
            ServiceDependency(name="analysis-layer", status="HEALTHY", availability="AVAILABLE", latency_ms=1, checked_at="2026-01-01T00:00:00+00:00"),
            ServiceDependency(name="data-storage", status="DEGRADED", availability="API_AVAILABLE_DATABASE_UNAVAILABLE", latency_ms=1, checked_at="2026-01-01T00:00:00+00:00"),
            ServiceDependency(name="llm-provider", status="UNKNOWN", availability="NOT_PROBED", details={}, checked_at="2026-01-01T00:00:00+00:00"),
        ]
        with patch.object(main.client, "dependencies", return_value=dependencies):
            response = TestClient(main.app).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "DEGRADED")
        self.assertEqual(response.json()["dependencies"][2]["status"], "UNKNOWN")

    def test_job_request_rejects_unknown_fields(self):
        response = TestClient(main.app).post(
            "/api/jobs",
            json={"article_id": "00000000-0000-0000-0000-000000000001", "unexpected": True},
        )
        self.assertEqual(response.status_code, 422)

    def test_settings_update_returns_typed_safe_fields(self):
        client = TestClient(main.app)
        response = client.patch("/api/settings", json={"request_timeout_seconds": 12})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request_timeout_seconds"], 12)
        self.assertNotIn("database_url", response.json())


if __name__ == "__main__":
    unittest.main()
