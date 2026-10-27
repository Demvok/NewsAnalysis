from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

os.environ["MANAGEMENT_DATA_DIR"] = tempfile.mkdtemp()
os.environ["MANAGEMENT_DATABASE_PATH"] = str(Path(os.environ["MANAGEMENT_DATA_DIR"]) / "management.db")

from app.database import ManagementDatabase  # noqa: E402
from app.models import JobCreateRequest, JobStatus  # noqa: E402
from app.orchestration import JobCoordinator  # noqa: E402


class FakeClient:
    def article(self, article_id, headers):
        return {"article_title": "Title", "article_content": "Content", "published_at": None}

    def analyze(self, payload, headers):
        return {"speakers": [{"citations": []}]}


class ManagementPhase1Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = ManagementDatabase(Path(self.directory.name) / "management.db")
        self.database.initialize()
        self.coordinator = JobCoordinator(self.database, FakeClient(), 1)

    def tearDown(self):
        self.coordinator.executor.shutdown(wait=True)
        self.directory.cleanup()

    def test_idempotency_returns_the_same_job(self):
        request = JobCreateRequest(article_id=uuid4())
        first = self.coordinator.create(request, "same-request")
        second = self.coordinator.create(request, "same-request")
        self.assertEqual(first["job_id"], second["job_id"])

    def test_restart_marks_active_jobs_failed(self):
        request = JobCreateRequest(article_id=uuid4())
        job = self.coordinator.create(request, "restart-request")
        with self.database.lock, self.database.connection() as connection:
            connection.execute("UPDATE jobs SET status=? WHERE job_id=?", (JobStatus.RUNNING.value, job["job_id"]))
        restarted = ManagementDatabase(self.database.path)
        restarted.initialize()
        recovered = self.coordinator.get(job["job_id"])
        self.assertEqual(recovered["status"], JobStatus.FAILED.value)
        self.assertEqual(recovered["error_code"], "MANAGEMENT_RESTART")

    def test_completed_job_has_pipeline_history(self):
        request = JobCreateRequest(article_id=uuid4())
        job = self.coordinator.create(request, "history-request")
        self.coordinator.executor.shutdown(wait=True)
        completed = self.coordinator.get(job["job_id"])
        self.assertEqual(completed["status"], JobStatus.COMPLETED.value)
        self.assertEqual(len(self.coordinator.stages(job["job_id"])), 2)

    def test_legacy_database_schema_is_migrated(self):
        legacy_directory = tempfile.TemporaryDirectory()
        legacy_path = Path(legacy_directory.name) / "management.db"
        import sqlite3
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, article_id TEXT NOT NULL,
                    status TEXT NOT NULL, requested_at TEXT NOT NULL, started_at TEXT,
                    completed_at TEXT, correlation_id TEXT NOT NULL, config_json TEXT NOT NULL,
                    error_code TEXT, error_message TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO jobs VALUES (
                    'legacy-job', 'article_analysis',
                    '00000000-0000-0000-0000-000000000001', 'QUEUED',
                    '2026-01-01T00:00:00+00:00', NULL, NULL, 'legacy-correlation',
                    '{}', NULL, NULL, 0
                );
                """
            )
            connection.commit()
        finally:
            connection.close()
        migrated = ManagementDatabase(legacy_path)
        migrated.initialize()
        with migrated.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id='legacy-job'").fetchone()
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["error_code"], "MANAGEMENT_RESTART")
        self.assertEqual(row["created_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(row["trace_id"], "legacy-correlation")
        legacy_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
