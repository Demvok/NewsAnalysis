from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock

from app.database import ManagementDatabase
from app.models import JobCreateRequest, JobStatus
from app.orchestration import JobCoordinator, error_code


class LimitClient:
    def __init__(self):
        self.settings = SimpleNamespace(
            max_job_attempts=2,
            max_job_duration_seconds=10.0,
            stage_timeout_seconds=1.0,
            retry_backoff_seconds=0.0,
            retry_max_delay_seconds=1.0,
        )


class ExecutionLimitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = ManagementDatabase(Path(self.directory.name) / "management.db")
        self.database.initialize()
        self.client = LimitClient()
        self.coordinator = JobCoordinator(self.database, self.client, 1)

    def tearDown(self):
        self.coordinator.executor.shutdown(wait=True)
        self.directory.cleanup()

    def test_timeout_during_each_stage_is_explicit(self):
        for stage in ("article_retrieval", "citation_extraction", "extraction_persistence", "citation_analysis"):
            with self.assertRaises(TimeoutError, msg=stage):
                self.coordinator._remaining(time.perf_counter() - 1, stage)
        self.assertEqual(error_code(TimeoutError("LLM request timed out")), "TIMEOUT")

    def test_llm_timeout_is_classified_explicitly(self):
        self.assertEqual(error_code(TimeoutError("LLM request timed out")), "TIMEOUT")

    def test_retry_exhaustion_is_terminal(self):
        job = self.coordinator.create(JobCreateRequest(article_id=uuid4()), "retry-limit", schedule=False)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, retry_count=?, started_at=? WHERE job_id=?",
                (JobStatus.RUNNING.value, 1, "2026-11-07T10:00:00+00:00", job["job_id"]),
            )
        self.coordinator._schedule_retry_or_finish(job["job_id"], "ValueError", "failed")
        self.assertEqual(self.coordinator.get(job["job_id"])["status"], JobStatus.FAILED.value)

    def test_non_retryable_provider_client_error_finishes_immediately(self):
        job = self.coordinator.create(JobCreateRequest(article_id=uuid4()), "non-retryable", schedule=False)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, retry_count=?, started_at=? WHERE job_id=?",
                (JobStatus.RUNNING.value, 0, datetime.now(timezone.utc).isoformat(), job["job_id"]),
            )
        error = Mock()
        error.response.status_code = 400
        self.coordinator._schedule_retry_or_finish(
            job["job_id"], "HTTPStatusError", "Upstream service returned HTTP 400",
            retryable=False,
        )
        current = self.coordinator.get(job["job_id"])
        self.assertEqual(current["status"], JobStatus.FAILED.value)
        self.assertEqual(current["retry_count"], 0)

    def test_retry_is_resubmitted_as_a_new_worker_job(self):
        job = self.coordinator.create(JobCreateRequest(article_id=uuid4()), "retry-submit", schedule=False)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, retry_count=?, started_at=? WHERE job_id=?",
                (JobStatus.RUNNING.value, 0, datetime.now(timezone.utc).isoformat(), job["job_id"]),
            )
        submitted = []
        self.coordinator._submit = submitted.append
        self.coordinator._schedule_retry_or_finish(job["job_id"], "RuntimeError", "temporary")
        self.assertEqual(submitted, [job["job_id"]])
        self.assertEqual(self.coordinator.get(job["job_id"])["status"], JobStatus.RETRYING.value)

    def test_retry_backoff_does_not_block_worker(self):
        job = self.coordinator.create(JobCreateRequest(article_id=uuid4()), "retry-delay", schedule=False)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, retry_count=?, started_at=? WHERE job_id=?",
                (JobStatus.RUNNING.value, 0, datetime.now(timezone.utc).isoformat(), job["job_id"]),
            )
        submitted = []
        self.coordinator._submit = submitted.append
        self.client.settings.retry_backoff_seconds = 0.05

        started = time.perf_counter()
        self.coordinator._schedule_retry_or_finish(job["job_id"], "RuntimeError", "temporary")
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.04)
        self.assertEqual(self.coordinator.get(job["job_id"])["status"], JobStatus.RETRYING.value)
        deadline = time.perf_counter() + 1
        while not submitted and time.perf_counter() < deadline:
            time.sleep(0.01)
        self.assertEqual(submitted, [job["job_id"]])

    def test_deadline_exhaustion_does_not_retry(self):
        job = self.coordinator.create(JobCreateRequest(article_id=uuid4()), "deadline-limit", schedule=False)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, started_at=? WHERE job_id=?",
                (JobStatus.RUNNING.value, "2020-01-01T00:00:00+00:00", job["job_id"]),
            )
        self.coordinator._schedule_retry_or_finish(job["job_id"], "TIMEOUT", "job deadline exhausted")
        current = self.coordinator.get(job["job_id"])
        self.assertEqual(current["status"], JobStatus.FAILED.value)
        self.assertEqual(current["error_code"], "TIMEOUT")

    def test_cancellation_during_running_stage_is_terminal(self):
        job = self.coordinator.create(JobCreateRequest(article_id=uuid4()), "cancel-limit", schedule=False)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, cancel_requested=1 WHERE job_id=?",
                (JobStatus.RUNNING.value, job["job_id"]),
            )
        self.coordinator._schedule_retry_or_finish(job["job_id"], "TIMEOUT", "cancelled while running")
        self.assertEqual(self.coordinator.get(job["job_id"])["status"], JobStatus.CANCELLED.value)
