from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import math
import inspect
from types import SimpleNamespace
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .clients import ServiceClient, UpstreamTimeoutError
from .database import ManagementDatabase
from .logger import get_logger
from .models import ExecutionMode, JobCreateRequest, JobStatus, StageStatus, TaskCreateRequest, TaskStatus
from .timing import monotonic_ms, queue_wait_ms, wall_duration_ms, worker_identity

logger = get_logger("orchestration")
MAX_AUTOMATIC_RETRIES = 2


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_error_summary(error: BaseException) -> str:
    response = getattr(error, "response", None)
    if response is not None and getattr(response, "status_code", None) is not None:
        return f"Upstream service returned HTTP {response.status_code}"
    if "Timeout" in type(error).__name__:
        return "Upstream request timed out"
    value = re.sub(
        r"(?i)(password|api[_-]?key|token|secret|credential)=\S+",
        r"\1=[REDACTED]",
        str(error),
    )
    return value[:500]

def error_code(error: BaseException) -> str:
    if isinstance(error, (TimeoutError, UpstreamTimeoutError)):
        return "TIMEOUT"
    return type(error).__name__


class JobCoordinator:
    def __init__(self, database: ManagementDatabase, client: ServiceClient, max_workers: int):
        self.database = database
        self.client = client
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="management-job")
        self.max_workers = max_workers
        self._shutting_down = False
        self._task_run_lock = threading.Lock()
        self.settings = getattr(client, "settings", SimpleNamespace(
            max_job_attempts=MAX_AUTOMATIC_RETRIES + 1,
            max_job_duration_seconds=3600.0,
            stage_timeout_seconds=1800.0,
            retry_backoff_seconds=1.0,
            retry_max_delay_seconds=60.0,
        ))

    def recover(self) -> None:
        """Resume durable queued work and retry jobs interrupted by a restart."""
        with self.database.lock, self.database.connection() as connection:
            interrupted = connection.execute(
                "SELECT job_id FROM jobs WHERE status='FAILED' AND error_code='MANAGEMENT_RESTART' "
                "AND retry_count < ? AND (started_at IS NULL OR "
                "julianday(?) - julianday(started_at) < ? / 86400.0)",
                (self.settings.max_job_attempts - 1, timestamp(),
                 self.settings.max_job_duration_seconds),
            ).fetchall()
            queued = connection.execute(
                "SELECT job_id FROM jobs WHERE status='PENDING'"
            ).fetchall()
            for row in interrupted:
                connection.execute(
                    "UPDATE jobs SET status='RETRYING', retry_count=retry_count+1, "
                    "completed_at=NULL WHERE job_id=? AND status='FAILED'",
                    (row["job_id"],),
                )
            job_ids = [row["job_id"] for row in interrupted] + [row["job_id"] for row in queued]
            task_ids = {
                row["task_id"]
                for row in connection.execute(
                    "SELECT DISTINCT task_id FROM task_jobs WHERE job_id IN (%s)"
                    % ",".join("?" for _ in job_ids),
                    job_ids,
                ).fetchall()
            } if job_ids else set()
            for task_id in task_ids:
                self._refresh_task_status(connection, task_id)
        for job_id in job_ids:
            self._submit(job_id)

    def _submit(self, job_id: str) -> None:
        """Submit work to the worker pool; run() performs the durable claim."""
        if self._shutting_down:
            return
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        task_id = json.loads(row["metadata_json"] or "{}").get("task_id") if row else None
        if task_id:
            self.executor.submit(self._run_task_job, job_id)
        else:
            self.executor.submit(self.run, job_id)

    def _run_task_job(self, job_id: str) -> None:
        with self._task_run_lock:
            self.run(job_id)

    def shutdown(self) -> None:
        """Stop accepting work and request cooperative cancellation of active work."""
        self._shutting_down = True
        with self.database.lock, self.database.connection() as connection:
            now = timestamp()
            connection.execute(
                "UPDATE jobs SET status='CANCELLED', completed_at=?, cancel_requested=1 "
                "WHERE status IN ('PENDING','RETRYING')", (now,)
            )
            connection.execute("UPDATE jobs SET cancel_requested=1 WHERE status='RUNNING'")
            for row in connection.execute(
                "SELECT DISTINCT task_id FROM task_jobs tj JOIN jobs j ON j.job_id=tj.job_id "
                "WHERE j.status IN ('PENDING','RUNNING','RETRYING')"
            ).fetchall():
                self._refresh_task_status(connection, row["task_id"])
        self.executor.shutdown(wait=False, cancel_futures=True)

    def create(
        self,
        request: JobCreateRequest,
        idempotency_key: str | None,
        initial_status: JobStatus = JobStatus.PENDING,
        schedule: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if request.execution_mode == ExecutionMode.BUNDLED and schedule:
            raise ValueError("bundled execution mode is only supported for tasks")
        with self.database.lock, self.database.connection() as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    return self.database.decode(existing)
            job_id, correlation_id, trace_id = str(uuid4()), str(uuid4()), str(uuid4())
            created = timestamp()
            try:
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
                }
                insert_columns = [
                    "job_id", "job_type", "article_id", "status", "created_at",
                    "correlation_id", "trace_id", "idempotency_key", "config_json",
                    "metadata_json",
                ]
                values: list[Any] = [
                    job_id, "article_analysis", str(request.article_id),
                    initial_status.value, created, correlation_id, trace_id,
                    idempotency_key, json.dumps(request.model_dump(mode="json")),
                    json.dumps({"source": "management-api", **(metadata or {})}),
                ]
                if "requested_at" in columns:
                    insert_columns.append("requested_at")
                    values.append(created)
                placeholders = ",".join("?" for _ in insert_columns)
                connection.execute(
                    f"INSERT INTO jobs({','.join(insert_columns)}) VALUES({placeholders})",
                    values,
                )
            except Exception:
                if idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
                    ).fetchone()
                    if existing:
                        return self.database.decode(existing)
                raise
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        assert row is not None
        job = self.database.decode(row)
        if schedule:
            self._submit(job_id)
        return job

    def run(self, job_id: str) -> None:
        job_started = time.perf_counter()
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] == JobStatus.CANCELLED.value or row["cancel_requested"]:
                return
            updated = connection.execute(
                "UPDATE jobs SET status=?, started_at=COALESCE(started_at,?) "
                "WHERE job_id=? AND status IN (?,?) AND cancel_requested=0",
                (JobStatus.RUNNING.value, timestamp(), job_id,
                 JobStatus.PENDING.value, JobStatus.RETRYING.value),
            )
            if updated.rowcount != 1:
                return
        attempt_number = int(row["retry_count"]) + 1
        config = json.loads(row["config_json"])
        deadline = time.perf_counter() + self.settings.max_job_duration_seconds
        if row["started_at"]:
            deadline = time.perf_counter() + max(
                0.0,
                self.settings.max_job_duration_seconds
                - max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(row["started_at"])).total_seconds()),
            )
        headers = {
            "x-job-id": job_id,
            "x-correlation-id": row["correlation_id"],
            "x-request-id": row["trace_id"],
            "x-job-attempt": str(attempt_number),
            "x-job-retry-count": str(row["retry_count"]),
            "x-job-created-at": row["created_at"],
            "x-queue-wait-ms": str(queue_wait_ms(row["created_at"], timestamp())),
        }
        queue_wait = int(headers["x-queue-wait-ms"])
        with self.database.lock, self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET queue_wait_ms=?, worker_identity=? WHERE job_id=?",
                (queue_wait, worker_identity(), job_id),
            )
            active_jobs = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status='RUNNING'"
            ).fetchone()["count"]
        current_stage: str | None = None
        try:
            logger.info(
                "job_started job_id=%s article_id=%s mode=%s attempt=%d queue_wait_ms=%d "
                "active_jobs=%d configured_workers=%d process_id=%d thread_id=%d worker_identity=%s",
                job_id, row["article_id"],
                json.loads(row["config_json"]).get("execution_mode", ExecutionMode.PER_ARTICLE.value),
                attempt_number, queue_wait, active_jobs, self.max_workers,
                os.getpid(), threading.get_ident(), worker_identity(),
            )
            current_stage = "article_retrieval"
            article_completed = self._stage_completed(job_id, current_stage)
            if not article_completed:
                self._stage(job_id, current_stage, StageStatus.RUNNING)
            article = self._article(row["article_id"], headers, self._remaining(deadline, "article_retrieval"))
            if not article_completed:
                self._stage(job_id, current_stage, StageStatus.COMPLETED, {"article_id": row["article_id"]})
            self._set_article_status(row["article_id"], "INGESTED")
            if self._cancel_requested(job_id):
                self._cancel_after_stage(job_id)
                return
            if not hasattr(self.client, "analysis_stage"):
                current_stage = "analysis"
                self._stage(job_id, current_stage, StageStatus.RUNNING)
                result = self.client.analyze({
                    "article_id": row["article_id"],
                    "article_title": article.get("article_title") or "",
                    "article_content": article.get("article_content") or "",
                    "published_at": article.get("published_at"),
                    "inconsistency_window_days": json.loads(row["config_json"])["inconsistency_window_days"],
                }, headers)
                self._stage(job_id, current_stage, StageStatus.COMPLETED, {
                    "speaker_count": len(result.get("speakers", [])),
                    "citation_count": sum(len(item.get("citations", [])) for item in result.get("speakers", [])),
                })
                self._set_article_status(row["article_id"], "ANALYZED")
                self._finish(job_id, JobStatus.COMPLETED)
                logger.info(
                    "job_completed job_id=%s duration_ms=%d attempt=%d worker_identity=%s",
                    job_id, monotonic_ms(job_started), attempt_number, worker_identity(),
                )
                return
            extraction_payload = {
                "article": {
                    "article_id": row["article_id"],
                    "article_title": article.get("article_title") or "",
                    "article_content": article.get("article_content") or "",
                    "published_at": article.get("published_at"),
                    "inconsistency_window_days": config["inconsistency_window_days"],
                }
            }
            current_stage = "citation_extraction"
            if self._stage_completed(job_id, current_stage):
                extraction = self._stage_result(job_id, current_stage)
                if not isinstance(extraction, dict):
                    raise ValueError("completed citation extraction has no persisted result")
            else:
                self._stage(job_id, current_stage, StageStatus.RUNNING)
                extraction_response = self._analysis_stage(
                    "citation_extraction", extraction_payload, headers,
                    self._remaining(deadline, current_stage),
                )
                extraction = extraction_response.get("result")
                if not isinstance(extraction, dict):
                    raise ValueError("citation extraction response did not contain a result")
                self._save_stage_result(job_id, current_stage, extraction)
                self._stage(job_id, current_stage, StageStatus.COMPLETED, {
                    "speaker_count": len(extraction.get("speakers", [])),
                    "citation_count": sum(len(item.get("citations", [])) for item in extraction.get("speakers", [])),
                })
            if self._cancel_requested(job_id):
                self._cancel_after_stage(job_id)
                return

            current_stage = "extraction_persistence"
            if not self._stage_completed(job_id, current_stage):
                self._stage(job_id, current_stage, StageStatus.RUNNING)
                persistence = self._analysis_stage(
                    current_stage,
                    {"article_id": row["article_id"], "extraction": extraction},
                    headers,
                    self._remaining(deadline, current_stage),
                )
                self._stage(job_id, current_stage, StageStatus.COMPLETED, persistence.get("stats") or {})
            self._set_article_status(row["article_id"], "CITATIONS_EXTRACTED")
            if self._cancel_requested(job_id):
                self._cancel_after_stage(job_id)
                return

            current_stage = "citation_analysis"
            if not self._stage_completed(job_id, current_stage):
                self._stage(job_id, current_stage, StageStatus.RUNNING)
                analysis = self._analysis_stage(
                    current_stage,
                    {"analysis": {
                        "article_id": row["article_id"],
                        "extraction": extraction,
                        "published_at": article.get("published_at"),
                        "inconsistency_window_days": config["inconsistency_window_days"],
                    }},
                    headers,
                    self._remaining(deadline, current_stage),
                )
                self._stage(job_id, current_stage, StageStatus.COMPLETED, analysis.get("stats") or {})
            self._set_article_status(row["article_id"], "ANALYZED")
            self._finish(job_id, JobStatus.COMPLETED)
            logger.info(
                "job_completed job_id=%s duration_ms=%d attempt=%d worker_identity=%s",
                job_id, monotonic_ms(job_started), attempt_number, worker_identity(),
            )
        except Exception as exc:
            logger.exception(
                "job_failed job_id=%s error_type=%s duration_ms=%d attempt=%d worker_identity=%s",
                job_id, type(exc).__name__, monotonic_ms(job_started),
                attempt_number, worker_identity(),
            )
            summary = safe_error_summary(exc)
            if current_stage:
                try:
                    self._stage(job_id, current_stage, StageStatus.FAILED, error_code=error_code(exc), error_summary=summary)
                except Exception:
                    logger.exception("job_failure_stage_persist_failed job_id=%s", job_id)
            self._schedule_retry_or_finish(job_id, error_code(exc), summary)
            if current_stage:
                logger.error("job_failed job_id=%s stage=%s error_type=%s", job_id, current_stage, type(exc).__name__)

    def _remaining(self, deadline: float, stage: str) -> float:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError(f"job deadline exhausted before {stage}")
        return min(self.settings.stage_timeout_seconds, remaining)

    def _article(self, article_id: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        if "timeout" in inspect.signature(self.client.article).parameters:
            return self.client.article(article_id, headers, timeout)
        return self.client.article(article_id, headers)

    def _analysis_stage(
        self, stage: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        if "timeout" in inspect.signature(self.client.analysis_stage).parameters:
            return self.client.analysis_stage(stage, payload, headers, timeout)
        return self.client.analysis_stage(stage, payload, headers)

    def _analysis_stage_bulk(
        self, stage: str, payload: list[dict[str, Any]], headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        if "timeout" in inspect.signature(self.client.analysis_stage_bulk).parameters:
            return self.client.analysis_stage_bulk(stage, payload, headers, timeout)
        return self.client.analysis_stage_bulk(stage, payload, headers)

    def _stage_completed(self, job_id: str, stage: str) -> bool:
        with self.database.connection() as connection:
            return connection.execute(
                "SELECT 1 FROM job_stages WHERE job_id=? AND stage=? AND status=? LIMIT 1",
                (job_id, stage, StageStatus.COMPLETED.value),
            ).fetchone() is not None

    def _stage_result(self, job_id: str, stage: str) -> Any:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        metadata = json.loads(row["metadata_json"] or "{}") if row else {}
        return metadata.get("_stage_results", {}).get(stage)

    def _save_stage_result(self, job_id: str, stage: str, result: Any) -> None:
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            metadata = json.loads(row["metadata_json"] or "{}")
            stage_results = metadata.setdefault("_stage_results", {})
            stage_results[stage] = result
            connection.execute(
                "UPDATE jobs SET metadata_json=? WHERE job_id=?",
                (json.dumps(metadata), job_id),
            )

    def _schedule_retry_or_finish(self, job_id: str, code: str, summary: str) -> None:
        should_retry = False
        final_status: JobStatus | None = None
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute(
                "SELECT retry_count, cancel_requested, status, started_at FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None or row["status"] != JobStatus.RUNNING.value:
                return
            elapsed = (
                (datetime.now(timezone.utc) - datetime.fromisoformat(row["started_at"])).total_seconds()
                if row["started_at"] else 0
            )
            delay = min(
                self.settings.retry_max_delay_seconds,
                self.settings.retry_backoff_seconds * (2 ** max(0, int(row["retry_count"]) - 1)),
            )
            can_retry = (
                not row["cancel_requested"]
                and row["retry_count"] + 1 < self.settings.max_job_attempts
                and elapsed + delay < self.settings.max_job_duration_seconds
            )
            if can_retry:
                updated = connection.execute(
                    "UPDATE jobs SET status=?, retry_count=retry_count+1, completed_at=NULL, "
                    "error_code=?, error_summary=? WHERE job_id=? AND status=? AND cancel_requested=0",
                    (JobStatus.RETRYING.value, code, summary, job_id, JobStatus.RUNNING.value),
                )
                should_retry = updated.rowcount == 1
            else:
                final_status = JobStatus.CANCELLED if row["cancel_requested"] else JobStatus.FAILED
                connection.execute(
                    "UPDATE jobs SET status=?, current_stage=NULL, completed_at=?, "
                    "error_code=?, error_summary=? WHERE job_id=? AND status=?",
                    (
                        final_status.value,
                        timestamp(),
                        code,
                        summary,
                        job_id,
                        JobStatus.RUNNING.value,
                    ),
                )
                task = connection.execute(
                    "SELECT task_id FROM task_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if task:
                    self._refresh_task_status(connection, task["task_id"])
        if should_retry:
            if delay:
                time.sleep(delay)
            self._submit(job_id)
            logger.warning("job_retry_scheduled job_id=%s", job_id)
        elif final_status is not None:
            row = self.get(job_id)
            self._set_article_status(row["article_id"], final_status.value)

    def run_bundled(self, task_id: str, job_ids: list[str]) -> None:
        """Run independently persisted analysis stages in reserve batch mode.

        The analysis service owns the analytical implementation; management only
        coordinates its stage APIs and records each article's status.
        """
        logger.info("bundled_task_started task_id=%s job_count=%s", task_id, len(job_ids))
        rows: dict[str, Any] = {}
        task_deadline = time.perf_counter() + self.settings.max_job_duration_seconds
        try:
            with self.database.lock, self.database.connection() as connection:
                for job_id in job_ids:
                    row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                    if row is None:
                        raise KeyError(job_id)
                    rows[job_id] = row
                    connection.execute(
                        "UPDATE jobs SET status=?, started_at=? WHERE job_id=? AND status IN (?,?)",
                        (JobStatus.RUNNING.value, timestamp(), job_id,
                         JobStatus.PENDING.value, JobStatus.RETRYING.value),
                    )

            articles: dict[str, dict[str, Any]] = {}
            for job_id, row in rows.items():
                if self._cancel_requested(job_id):
                    self._finish(job_id, JobStatus.CANCELLED, "CANCEL_REQUESTED", "Cancellation requested before bundled execution")
                    continue
                self._stage(job_id, "article_retrieval", StageStatus.RUNNING)
                articles[job_id] = self._article(
                    row["article_id"],
                    {"x-job-id": job_id, "x-correlation-id": row["correlation_id"], "x-request-id": row["trace_id"]},
                    self._remaining(task_deadline, "article_retrieval"),
                )
                self._stage(job_id, "article_retrieval", StageStatus.COMPLETED, {"article_id": row["article_id"]})
                self._set_article_status(row["article_id"], "INGESTED")

            active = [job_id for job_id in job_ids if job_id in articles and not self._cancel_requested(job_id)]
            extraction_payload = [
                {"article": {
                    "article_id": rows[job_id]["article_id"],
                    "article_title": articles[job_id].get("article_title") or "",
                    "article_content": articles[job_id].get("article_content") or "",
                    "published_at": articles[job_id].get("published_at"),
                    "inconsistency_window_days": json.loads(rows[job_id]["config_json"])["inconsistency_window_days"],
                }}
                for job_id in active
            ]
            self._run_bundled_stage(active, "citation_extraction")
            extraction_response = self._analysis_stage_bulk(
                "citation_extraction", extraction_payload,
                {"x-task-id": task_id, "x-request-id": str(uuid4())},
                self._remaining(task_deadline, "citation_extraction"),
            )
            extractions = extraction_response.get("results", [])
            if len(extractions) != len(active):
                raise ValueError("bundled extraction response count does not match request")

            persistence_payload = []
            for job_id, item in zip(active, extractions):
                extraction = item.get("result")
                if not extraction:
                    raise ValueError(f"bundled extraction returned no result for job {job_id}")
                persistence_payload.append({
                    "article_id": rows[job_id]["article_id"],
                    "extraction": extraction,
                })
            self._complete_bundled_stage(active, "citation_extraction", {"item_count": len(active)})
            self._run_bundled_stage(active, "extraction_persistence")
            self._analysis_stage_bulk(
                "extraction_persistence", persistence_payload,
                {"x-task-id": task_id, "x-request-id": str(uuid4())},
                self._remaining(task_deadline, "extraction_persistence"),
            )
            self._complete_bundled_stage(active, "extraction_persistence", {"item_count": len(active)})
            for job_id in active:
                self._set_article_status(rows[job_id]["article_id"], "CITATIONS_EXTRACTED")

            analysis_payload = []
            for job_id, item in zip(active, extractions):
                config = json.loads(rows[job_id]["config_json"])
                analysis_payload.append({
                    "analysis": {
                        "article_id": rows[job_id]["article_id"],
                        "extraction": item["result"],
                        "published_at": articles[job_id].get("published_at"),
                        "inconsistency_window_days": config["inconsistency_window_days"],
                    }
                })
            self._run_bundled_stage(active, "citation_analysis")
            result = self._analysis_stage_bulk(
                "citation_analysis", analysis_payload,
                {"x-task-id": task_id, "x-request-id": str(uuid4())},
                self._remaining(task_deadline, "citation_analysis"),
            )
            self._complete_bundled_stage(active, "citation_analysis", {
                "item_count": len(active),
                "result_count": len(result.get("results", [])),
            })
            for job_id in active:
                self._set_article_status(rows[job_id]["article_id"], "ANALYZED")
            for job_id in active:
                self._finish(job_id, JobStatus.COMPLETED)
            logger.info("bundled_task_completed task_id=%s job_count=%s", task_id, len(active))
        except Exception as exc:
            summary = safe_error_summary(exc)
            logger.exception("bundled_task_failed task_id=%s error_type=%s", task_id, type(exc).__name__)
            for job_id in job_ids:
                try:
                    current = self.get(job_id)
                    if current["status"] in {JobStatus.RUNNING.value, JobStatus.PENDING.value, JobStatus.RETRYING.value}:
                        self._stage(job_id, current.get("current_stage") or "bundled_analysis", StageStatus.FAILED,
                                    error_code=type(exc).__name__, error_summary=summary)
                        self._schedule_retry_or_finish(job_id, type(exc).__name__, summary)
                except Exception:
                    logger.exception("bundled_job_failure_persist_failed job_id=%s", job_id)

    def _run_bundled_stage(self, job_ids: list[str], stage: str) -> None:
        for job_id in job_ids:
            self._stage(job_id, stage, StageStatus.RUNNING)

    def _complete_bundled_stage(self, job_ids: list[str], stage: str, summary: dict[str, Any]) -> None:
        for job_id in job_ids:
            self._stage(job_id, stage, StageStatus.COMPLETED, summary)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] == JobStatus.PENDING.value:
                connection.execute("UPDATE jobs SET status=?, completed_at=? WHERE job_id=?", (JobStatus.CANCELLED.value, timestamp(), job_id))
            elif row["status"] == JobStatus.RUNNING.value:
                connection.execute("UPDATE jobs SET cancel_requested=1 WHERE job_id=?", (job_id,))
            elif row["status"] == JobStatus.RETRYING.value:
                connection.execute(
                    "UPDATE jobs SET status=?, completed_at=?, cancel_requested=1 WHERE job_id=?",
                    (JobStatus.CANCELLED.value, timestamp(), job_id),
                )
            task = connection.execute(
                "SELECT task_id FROM task_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if task:
                self._refresh_task_status(connection, task["task_id"])
        return self.get(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        current = self.get(job_id)
        if current["status"] not in {JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
            raise ValueError("Only FAILED or CANCELLED jobs can be retried")
        if current["retry_count"] + 1 >= self.settings.max_job_attempts:
            raise ValueError("Maximum job attempts exhausted")
        if current.get("started_at"):
            elapsed = (
                datetime.now(timezone.utc) - datetime.fromisoformat(current["started_at"])
            ).total_seconds()
            if elapsed >= self.settings.max_job_duration_seconds:
                raise ValueError("Job deadline exhausted")
        with self.database.lock, self.database.connection() as connection:
            updated = connection.execute(
                "UPDATE jobs SET status=?, retry_count=retry_count+1, cancel_requested=0, "
                "completed_at=NULL, error_code=NULL, error_summary=NULL "
                "WHERE job_id=? AND status IN (?,?)",
                (JobStatus.RETRYING.value, job_id, JobStatus.FAILED.value, JobStatus.CANCELLED.value),
            )
        if updated.rowcount != 1:
            raise ValueError("Job changed state before retry could be scheduled")
        self._submit(job_id)
        return self.get(job_id)

    def _set_article_status(self, article_id: str, status: str) -> None:
        update = getattr(self.client, "update_article_status", None)
        if callable(update):
            update(article_id, status)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self.database.decode(row)

    def list(self, status: str | None, article_id: str | None, task_id: str | None, limit: int) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            query += " AND status=?"; params.append(status)
        if article_id:
            query += " AND article_id=?"; params.append(article_id)
        if task_id:
            query += " AND metadata_json LIKE ?"; params.append(f'%"task_id": "{task_id}"%')
        query += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with self.database.connection() as connection:
            return [self.database.decode(row) for row in connection.execute(query, params)]

    def create_task(self, request: TaskCreateRequest) -> dict[str, Any]:
        with self.database.lock:
            return self._create_task(request)

    def _create_task(self, request: TaskCreateRequest) -> dict[str, Any]:
        task_id = str(uuid4())
        created = timestamp()
        article_ids = [str(item) for item in request.article_ids]
        with self.database.connection() as connection:
            active_task = connection.execute(
                "SELECT task_id FROM tasks WHERE status IN ('PENDING','RUNNING') LIMIT 1"
            ).fetchone()
            if active_task:
                raise ValueError("Only one processing task can run at a time")
            blocked_statuses = "('PENDING','RUNNING','RETRYING')" if request.redo else "('PENDING','RUNNING','RETRYING','COMPLETED')"
            existing: set[str] = set()
            for start in range(0, len(article_ids), 500):
                batch = article_ids[start:start + 500]
                existing.update(
                    row["article_id"] for row in connection.execute(
                        """SELECT DISTINCT article_id FROM jobs
                           WHERE article_id IN (%s)
                           AND status IN %s"""
                        % (",".join("?" for _ in batch), blocked_statuses), batch
                    ).fetchall()
                )
        article_ids = [item for item in article_ids if item not in existing]
        if not article_ids:
            raise ValueError("All selected articles already have active or completed jobs")
        with self.database.lock, self.database.connection() as connection:
            connection.execute(
                """INSERT INTO tasks(task_id,name,status,article_ids_json,created_at,execution_mode)
                   VALUES(?,?,?,?,?,?)""",
                (task_id, request.name, TaskStatus.PENDING.value,
                 json.dumps(article_ids), created, request.execution_mode.value),
            )
        created_job_ids: list[str] = []
        try:
            for article_id in article_ids:
                job = self.create(
                    JobCreateRequest(
                        article_id=article_id,
                        inconsistency_window_days=request.inconsistency_window_days,
                        execution_mode=request.execution_mode,
                    ),
                    f"task:{task_id}:article:{article_id}",
                    schedule=False,
                    metadata={"source": "management-task", "task_id": task_id, "execution_mode": request.execution_mode.value},
                )
                created_job_ids.append(job["job_id"])
                with self.database.lock, self.database.connection() as connection:
                    connection.execute(
                        "INSERT INTO task_jobs(task_id,job_id,article_id) VALUES(?,?,?)",
                        (task_id, job["job_id"], str(article_id)),
                    )
                    connection.execute(
                        "UPDATE jobs SET metadata_json=? WHERE job_id=?",
                        (json.dumps({"source": "management-task", "task_id": task_id, "execution_mode": request.execution_mode.value}), job["job_id"]),
                    )
        except Exception:
            logger.exception("task_job_creation_failed task_id=%s", task_id)
            with self.database.lock, self.database.connection() as connection:
                for job_id in created_job_ids:
                    connection.execute("DELETE FROM task_jobs WHERE job_id=?", (job_id,))
                    connection.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
                connection.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
            raise
        with self.database.lock, self.database.connection() as connection:
            connection.execute(
                "UPDATE tasks SET status=?, started_at=? WHERE task_id=?",
                (TaskStatus.RUNNING.value, timestamp(), task_id),
            )
            job_ids = [
                row["job_id"] for row in connection.execute(
                    "SELECT job_id FROM task_jobs WHERE task_id=?", (task_id,)
                ).fetchall()
            ]
        if request.execution_mode == ExecutionMode.BUNDLED:
            self.executor.submit(self._run_bundled_task, task_id, job_ids)
        else:
            for job_id in job_ids:
                self._submit(job_id)
        return self.task(task_id)

    def _run_bundled_task(self, task_id: str, job_ids: list[str]) -> None:
        with self._task_run_lock:
            self.run_bundled(task_id, job_ids)

    def task(self, task_id: str, include_job_ids: bool = True) -> dict[str, Any]:
        with self.database.lock, self.database.connection() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(task_id)
            jobs = connection.execute(
                """SELECT j.job_id FROM jobs j JOIN task_jobs tj ON tj.job_id=j.job_id
                   WHERE tj.task_id=? ORDER BY j.created_at""", (task_id,)
            ).fetchall()
            summary = connection.execute(
                """WITH task_job_summary AS (
                       SELECT COUNT(*) AS total,
                              SUM(j.status='COMPLETED') AS completed,
                              SUM(j.status='FAILED') AS failed,
                              SUM(j.status='RUNNING') AS running,
                              SUM(j.status IN ('PENDING','RETRYING')) AS waiting,
                              SUM(j.retry_count > 0) AS retried
                       FROM task_jobs tj JOIN jobs j ON j.job_id=tj.job_id
                       WHERE tj.task_id=?
                   ),
                   telemetry_summary AS (
                       SELECT COALESCE(SUM(json_extract(te.attributes_json, '$.citation_count')), 0) AS citations,
                              COALESCE(SUM(json_extract(te.attributes_json, '$.speaker_count')), 0) AS speakers,
                              COALESCE(SUM(json_extract(te.attributes_json, '$.topic_count')), 0) AS topics,
                              COALESCE(SUM(json_extract(te.attributes_json, '$.attitude_count')), 0) AS attitudes
                       FROM telemetry_events te JOIN task_jobs tj ON tj.job_id=te.job_id
                       WHERE tj.task_id=? AND te.event_type='pipeline_stage'
                         AND te.status='completed'
                   )
                   SELECT * FROM task_job_summary CROSS JOIN telemetry_summary""",
                (task_id, task_id),
            ).fetchone()
            token_summary = connection.execute(
                """SELECT
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(input_tokens_estimated), 0) AS input_tokens_estimated,
                       COALESCE(SUM(output_tokens_estimated), 0) AS output_tokens_estimated
                   FROM telemetry_events
                   WHERE job_id IN (SELECT job_id FROM task_jobs WHERE task_id=?)
                     AND event_type='llm_call'
                     AND status IN ('completed', 'failed')""",
                (task_id,),
            ).fetchone()
            self._refresh_task_status(connection, task_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            now = datetime.now(timezone.utc)
            elapsed_ms = None
            if task["started_at"]:
                started = datetime.fromisoformat(task["started_at"])
                completed = datetime.fromisoformat(task["completed_at"]) if task["completed_at"] else now
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                elapsed_ms = max(0, int((completed - started).total_seconds() * 1000))
            total = summary["total"] or 0
            retry_rate = (summary["retried"] or 0) / total if total else 0
            completed_jobs = connection.execute(
                """SELECT started_at, completed_at FROM jobs
                   WHERE status IN ('COMPLETED', 'FAILED')
                     AND started_at IS NOT NULL AND completed_at IS NOT NULL
                   ORDER BY completed_at DESC LIMIT 50"""
            ).fetchall()
            durations = []
            for row in completed_jobs:
                started = datetime.fromisoformat(row["started_at"])
                completed = datetime.fromisoformat(row["completed_at"])
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                durations.append(max(0, (completed - started).total_seconds() * 1000))
            recent = list(reversed(durations[-20:]))
            weights = range(1, len(recent) + 1)
            baseline = sum(value * weight for value, weight in zip(recent, weights)) / sum(weights) if recent else None
            completed_count = summary["completed"] or 0
            remaining = total - completed_count - (summary["failed"] or 0)
            input_tokens = token_summary["input_tokens"] or 0
            output_tokens = token_summary["output_tokens"] or 0
            input_tokens_estimated = token_summary["input_tokens_estimated"] or 0
            output_tokens_estimated = token_summary["output_tokens_estimated"] or 0
            token_source = "measured" if input_tokens or output_tokens else (
                "estimated" if input_tokens_estimated or output_tokens_estimated else "none"
            )
            if elapsed_ms is not None and completed_count:
                estimated_total = elapsed_ms * total / completed_count
                estimate_source = "observed_task_rate"
            elif baseline is not None:
                estimated_total = baseline * total
                estimate_source = "moving_average"
            else:
                estimated_total = None
                estimate_source = None
            return {
                "task_id": task["task_id"], "name": task["name"], "status": task["status"],
                "article_ids": json.loads(task["article_ids_json"]) if include_job_ids else [],
                "job_ids": [job["job_id"] for job in jobs] if include_job_ids else [],
                "total_articles": total, "completed_articles": summary["completed"] or 0,
                "failed_articles": summary["failed"] or 0, "running_articles": summary["running"] or 0,
                "waiting_articles": summary["waiting"] or 0,
                "execution_mode": task["execution_mode"], "citations": summary["citations"] or 0,
                "speakers": summary["speakers"] or 0, "topics": summary["topics"] or 0,
                "attitudes": summary["attitudes"] or 0,
                "created_at": task["created_at"], "started_at": task["started_at"],
                "completed_at": task["completed_at"],
                "retry_rate": retry_rate,
                "execution_time_ms": elapsed_ms,
                "execution_time_per_job_ms": elapsed_ms / len(jobs) if elapsed_ms is not None and jobs else None,
                "elapsed_time_ms": elapsed_ms,
                "estimated_total_time_ms": estimated_total,
                "remaining_time_ms": max(0, estimated_total - elapsed_ms) if estimated_total is not None and elapsed_ms is not None and remaining else 0 if estimated_total is not None and not remaining else None,
                "estimate_source": estimate_source,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "input_tokens_estimated": input_tokens_estimated,
                "output_tokens_estimated": output_tokens_estimated,
                "token_source": token_source,
            }

    def list_tasks(self, limit: int) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            ids = [row["task_id"] for row in connection.execute(
                "SELECT task_id FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()]
        return [self.task(task_id, include_job_ids=False) for task_id in ids]

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        for job_id in task["job_ids"]:
            try:
                self.cancel(job_id)
            except KeyError:
                continue
        return self.task(task_id)

    def cancel_all(self) -> dict[str, int]:
        """Request cancellation for every queued or active job."""
        with self.database.lock, self.database.connection() as connection:
            now = timestamp()
            queued = connection.execute(
                "UPDATE jobs SET status='CANCELLED', completed_at=?, cancel_requested=1 "
                "WHERE status IN ('PENDING','RETRYING')", (now,)
            ).rowcount
            running = connection.execute(
                "UPDATE jobs SET cancel_requested=1 WHERE status='RUNNING'"
            ).rowcount
            for row in connection.execute(
                "SELECT DISTINCT task_id FROM task_jobs WHERE job_id IN "
                "(SELECT job_id FROM jobs WHERE status='CANCELLED' OR cancel_requested=1)"
            ).fetchall():
                self._refresh_task_status(connection, row["task_id"])
        return {"queued_cancelled": queued, "running_cancel_requested": running}

    def delete_task(self, task_id: str) -> None:
        task = self.task(task_id)
        active = {"PENDING", "RUNNING", "RETRYING"}
        if task["status"] in active:
            raise ValueError("Only completed, failed, or cancelled tasks can be deleted")
        with self.database.lock, self.database.connection() as connection:
            job_ids = [
                row["job_id"]
                for row in connection.execute(
                    "SELECT job_id FROM jobs WHERE metadata_json LIKE ?", (f'%"task_id": "{task_id}"%',)
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM telemetry_events WHERE job_id IN (SELECT job_id FROM task_jobs WHERE task_id=?)",
                (task_id,),
            )
            connection.execute(
                "DELETE FROM events WHERE job_id IN (SELECT job_id FROM task_jobs WHERE task_id=?)",
                (task_id,),
            )
            connection.execute(
                "DELETE FROM job_stages WHERE job_id IN (SELECT job_id FROM task_jobs WHERE task_id=?)",
                (task_id,),
            )
            connection.execute("DELETE FROM task_jobs WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
            for job_id in job_ids:
                connection.execute("DELETE FROM experiment_jobs WHERE job_id=?", (job_id,))
                connection.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))

    def stages(self, job_id: str) -> list[dict[str, Any]]:
        self.get(job_id)
        with self.database.connection() as connection:
            return [self.database.decode(row) for row in connection.execute("SELECT * FROM job_stages WHERE job_id=? ORDER BY started_at", (job_id,))]

    def delete(self, job_id: str) -> None:
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] in {
                JobStatus.PENDING.value,
                JobStatus.RUNNING.value,
                JobStatus.RETRYING.value,
            }:
                raise ValueError("Only completed, failed, or cancelled jobs can be deleted")
            connection.execute("DELETE FROM experiment_jobs WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM job_stages WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM telemetry_events WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM events WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))

    def _cancel_requested(self, job_id: str) -> bool:
        with self.database.connection() as connection:
            return bool(connection.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0])

    def _cancel_after_stage(self, job_id: str) -> None:
        self._finish(job_id, JobStatus.CANCELLED, "CANCEL_REQUESTED", "Cancellation requested after the active stage completed")

    def _finish(self, job_id: str, status: JobStatus, code: str | None = None, summary: str | None = None) -> None:
        article_id: str | None = None
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute(
                "SELECT article_id, cancel_requested, status FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            article_id = row["article_id"] if row else None
            if row is None or row["status"] not in {
                JobStatus.RUNNING.value,
                JobStatus.RETRYING.value,
            }:
                return
            effective_status = (
                JobStatus.CANCELLED
                if row["cancel_requested"] and status == JobStatus.COMPLETED
                else status
            )
            completed_at = timestamp()
            started_at = connection.execute(
                "SELECT started_at FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()["started_at"]
            duration = wall_duration_ms(started_at, completed_at) if started_at else None
            connection.execute(
                "UPDATE jobs SET status=?, current_stage=NULL, completed_at=?, "
                "duration_ms=?, error_code=?, error_summary=? WHERE job_id=?",
                (
                    effective_status.value,
                    completed_at,
                    duration,
                    code,
                    summary,
                    job_id,
                ),
            )
            task = connection.execute(
                "SELECT task_id FROM task_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if task:
                self._refresh_task_status(connection, task["task_id"])
        if article_id:
            final_status = {
                JobStatus.COMPLETED: "COMPLETED",
                JobStatus.FAILED: "FAILED",
                JobStatus.CANCELLED: "CANCELLED",
            }.get(effective_status)
            if final_status:
                self._set_article_status(article_id, final_status)

    @staticmethod
    def _refresh_task_status(connection, task_id: str) -> None:
        jobs = connection.execute(
            """SELECT j.status FROM jobs j
               JOIN task_jobs tj ON tj.job_id=j.job_id
               WHERE tj.task_id=?""",
            (task_id,),
        ).fetchall()
        if not jobs:
            return
        statuses = [row["status"] for row in jobs]
        if any(status in {JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.RETRYING.value}
               for status in statuses):
            task_status = TaskStatus.RUNNING.value
            completed_at = None
        elif any(status == JobStatus.CANCELLED.value for status in statuses):
            task_status = TaskStatus.CANCELLED.value
            completed_at = timestamp()
        elif any(status == JobStatus.FAILED.value for status in statuses):
            task_status = TaskStatus.PARTIAL.value if any(status == JobStatus.COMPLETED.value for status in statuses) else TaskStatus.FAILED.value
            completed_at = timestamp()
        else:
            task_status = TaskStatus.COMPLETED.value
            completed_at = timestamp()
        if completed_at is None:
            connection.execute(
                "UPDATE tasks SET status=?, completed_at=NULL WHERE task_id=?",
                (task_status, task_id),
            )
        else:
            connection.execute(
                "UPDATE tasks SET status=?, completed_at=COALESCE(completed_at,?) WHERE task_id=?",
                (task_status, completed_at, task_id),
            )

    def _stage(
        self,
        job_id: str,
        name: str,
        status: StageStatus,
        summary: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        started = timestamp()
        completed = timestamp() if status != StageStatus.RUNNING else None
        duration_ms = None
        with self.database.lock, self.database.connection() as connection:
            existing = connection.execute(
                "SELECT stage_id FROM job_stages WHERE job_id=? AND stage=? AND status=? ORDER BY started_at DESC LIMIT 1",
                (job_id, name, StageStatus.RUNNING.value),
            ).fetchone()
            if existing is None:
                attempt = connection.execute(
                    "SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt "
                    "FROM job_stages WHERE job_id=? AND stage=?",
                    (job_id, name),
                ).fetchone()["next_attempt"]
                connection.execute(
                    """INSERT INTO job_stages
                       (stage_id,job_id,stage,status,attempt,started_at,completed_at,
                        duration_ms,summary_json,error_code,error_summary)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid4()), job_id, name, status.value, attempt, started, completed, duration_ms, json.dumps(summary or {}), error_code, error_summary),
                )
            else:
                running = connection.execute(
                    "SELECT started_at FROM job_stages WHERE stage_id=?",
                    (existing["stage_id"],),
                ).fetchone()
                if completed and running and running["started_at"]:
                    duration_ms = max(
                        0,
                        int(
                            (
                                datetime.fromisoformat(completed)
                                - datetime.fromisoformat(running["started_at"])
                            ).total_seconds()
                            * 1000
                        ),
                    )
                connection.execute(
                    "UPDATE job_stages SET status=?, completed_at=?, duration_ms=?, summary_json=? WHERE stage_id=?",
                    (status.value, completed, duration_ms, json.dumps(summary or {}), existing["stage_id"]),
                )
                if error_code or error_summary:
                    connection.execute(
                        "UPDATE job_stages SET error_code=?, error_summary=? WHERE stage_id=?",
                        (error_code, error_summary, existing["stage_id"]),
                    )
            connection.execute("UPDATE jobs SET current_stage=? WHERE job_id=?", (name if status == StageStatus.RUNNING else None, job_id))
            active_stages = connection.execute(
                "SELECT COUNT(*) AS count FROM job_stages WHERE status='RUNNING'"
            ).fetchone()["count"]
        logger.info(
            "stage_transition job_id=%s stage=%s status=%s active_stages=%d "
            "process_id=%d thread_id=%d configured_workers=%d",
            job_id, name, status.value, active_stages, os.getpid(),
            threading.get_ident(), self.max_workers,
        )
