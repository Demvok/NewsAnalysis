from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .clients import ServiceClient
from .database import ManagementDatabase
from .models import JobCreateRequest, JobStatus, StageStatus

logger = logging.getLogger("newsanalysis.management.orchestration")


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


class JobCoordinator:
    def __init__(self, database: ManagementDatabase, client: ServiceClient, max_workers: int):
        self.database = database
        self.client = client
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="management-job")

    def create(
        self,
        request: JobCreateRequest,
        idempotency_key: str | None,
        initial_status: JobStatus = JobStatus.PENDING,
    ) -> dict[str, Any]:
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
                    json.dumps({"source": "management-api"}),
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
        self.executor.submit(self.run, job_id)
        return job

    def run(self, job_id: str) -> None:
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] == JobStatus.CANCELLED.value or row["cancel_requested"]:
                return
            connection.execute(
                "UPDATE jobs SET status=?, started_at=? WHERE job_id=?",
                (JobStatus.RUNNING.value, timestamp(), job_id),
            )
        headers = {"x-job-id": job_id, "x-correlation-id": row["correlation_id"], "x-request-id": row["trace_id"]}
        current_stage: str | None = None
        try:
            current_stage = "article_retrieval"
            self._stage(job_id, "article_retrieval", StageStatus.RUNNING)
            article = self.client.article(row["article_id"], headers)
            self._stage(job_id, "article_retrieval", StageStatus.COMPLETED, {"article_id": row["article_id"]})
            if self._cancel_requested(job_id):
                self._cancel_after_stage(job_id)
                return
            current_stage = "analysis"
            self._stage(job_id, "analysis", StageStatus.RUNNING)
            result = self.client.analyze({
                "article_id": row["article_id"],
                "article_title": article.get("article_title") or "",
                "article_content": article.get("article_content") or "",
                "published_at": article.get("published_at"),
                "inconsistency_window_days": json.loads(row["config_json"])["inconsistency_window_days"],
            }, headers)
            self._stage(job_id, "analysis", StageStatus.COMPLETED, {
                "speaker_count": len(result.get("speakers", [])),
                "citation_count": sum(len(item.get("citations", [])) for item in result.get("speakers", [])),
            })
            self._finish(job_id, JobStatus.COMPLETED)
        except Exception as exc:
            logger.exception("job_failed job_id=%s error_type=%s", job_id, type(exc).__name__)
            if current_stage:
                summary = safe_error_summary(exc)
                self._stage(job_id, current_stage, StageStatus.FAILED, error_code=type(exc).__name__, error_summary=summary)
                self._finish(job_id, JobStatus.FAILED, type(exc).__name__, summary)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.database.lock, self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] == JobStatus.PENDING.value:
                connection.execute("UPDATE jobs SET status=?, completed_at=? WHERE job_id=?", (JobStatus.CANCELLED.value, timestamp(), job_id))
            elif row["status"] == JobStatus.RUNNING.value:
                connection.execute("UPDATE jobs SET cancel_requested=1 WHERE job_id=?", (job_id,))
        return self.get(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        current = self.get(job_id)
        if current["status"] not in {JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
            raise ValueError("Only FAILED or CANCELLED jobs can be retried")
        request = JobCreateRequest.model_validate(current["config"])
        retry = self.create(
            request,
            f"retry:{job_id}:{current['retry_count'] + 1}",
            initial_status=JobStatus.RETRYING,
        )
        with self.database.lock, self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, retry_count=? WHERE job_id=?",
                (JobStatus.RETRYING.value, current["retry_count"] + 1, retry["job_id"]),
            )
        return self.get(retry["job_id"])

    def get(self, job_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self.database.decode(row)

    def list(self, status: str | None, article_id: str | None, limit: int) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            query += " AND status=?"; params.append(status)
        if article_id:
            query += " AND article_id=?"; params.append(article_id)
        query += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with self.database.connection() as connection:
            return [self.database.decode(row) for row in connection.execute(query, params)]

    def stages(self, job_id: str) -> list[dict[str, Any]]:
        self.get(job_id)
        with self.database.connection() as connection:
            return [self.database.decode(row) for row in connection.execute("SELECT * FROM job_stages WHERE job_id=? ORDER BY started_at", (job_id,))]

    def _cancel_requested(self, job_id: str) -> bool:
        with self.database.connection() as connection:
            return bool(connection.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0])

    def _cancel_after_stage(self, job_id: str) -> None:
        self._finish(job_id, JobStatus.CANCELLED, "CANCEL_REQUESTED", "Cancellation requested after the active stage completed")

    def _finish(self, job_id: str, status: JobStatus, code: str | None = None, summary: str | None = None) -> None:
        with self.database.lock, self.database.connection() as connection:
            connection.execute("UPDATE jobs SET status=?, completed_at=?, error_code=?, error_summary=? WHERE job_id=?", (status.value, timestamp(), code, summary, job_id))

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
                connection.execute(
                    """INSERT INTO job_stages
                       (stage_id,job_id,stage,status,attempt,started_at,completed_at,
                        duration_ms,summary_json,error_code,error_summary)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid4()), job_id, name, status.value, 1, started, completed, duration_ms, json.dumps(summary or {}), error_code, error_summary),
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
