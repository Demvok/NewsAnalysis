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
from .logger import get_logger
from .models import ExecutionMode, JobCreateRequest, JobStatus, StageStatus, TaskCreateRequest, TaskStatus

logger = get_logger("orchestration")


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
            self.executor.submit(self.run, job_id)
        return job

    def run(self, job_id: str) -> None:
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
        headers = {"x-job-id": job_id, "x-correlation-id": row["correlation_id"], "x-request-id": row["trace_id"]}
        current_stage: str | None = None
        try:
            logger.info("job_started job_id=%s article_id=%s mode=%s", job_id, row["article_id"], json.loads(row["config_json"]).get("execution_mode", ExecutionMode.PER_ARTICLE.value))
            current_stage = "article_retrieval"
            self._stage(job_id, "article_retrieval", StageStatus.RUNNING)
            article = self.client.article(row["article_id"], headers)
            self._stage(job_id, "article_retrieval", StageStatus.COMPLETED, {"article_id": row["article_id"]})
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
                logger.info("job_completed job_id=%s", job_id)
                return
            config = json.loads(row["config_json"])
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
            self._stage(job_id, current_stage, StageStatus.RUNNING)
            extraction_response = self.client.analysis_stage(
                "citation_extraction", extraction_payload, headers
            )
            extraction = extraction_response.get("result")
            if not isinstance(extraction, dict):
                raise ValueError("citation extraction response did not contain a result")
            self._stage(job_id, current_stage, StageStatus.COMPLETED, {
                "speaker_count": len(extraction.get("speakers", [])),
                "citation_count": sum(len(item.get("citations", [])) for item in extraction.get("speakers", [])),
            })
            if self._cancel_requested(job_id):
                self._cancel_after_stage(job_id)
                return

            current_stage = "extraction_persistence"
            self._stage(job_id, current_stage, StageStatus.RUNNING)
            persistence = self.client.analysis_stage(
                current_stage,
                {"article_id": row["article_id"], "extraction": extraction},
                headers,
            )
            self._stage(job_id, current_stage, StageStatus.COMPLETED, persistence.get("stats") or {})
            self._set_article_status(row["article_id"], "CITATIONS_EXTRACTED")
            if self._cancel_requested(job_id):
                self._cancel_after_stage(job_id)
                return

            current_stage = "citation_analysis"
            self._stage(job_id, current_stage, StageStatus.RUNNING)
            analysis = self.client.analysis_stage(
                current_stage,
                {"analysis": {
                    "article_id": row["article_id"],
                    "extraction": extraction,
                    "published_at": article.get("published_at"),
                    "inconsistency_window_days": config["inconsistency_window_days"],
                }},
                headers,
            )
            self._stage(job_id, current_stage, StageStatus.COMPLETED, analysis.get("stats") or {})
            self._set_article_status(row["article_id"], "ANALYZED")
            self._finish(job_id, JobStatus.COMPLETED)
            logger.info("job_completed job_id=%s", job_id)
        except Exception as exc:
            logger.exception("job_failed job_id=%s error_type=%s", job_id, type(exc).__name__)
            if current_stage:
                summary = safe_error_summary(exc)
                self._stage(job_id, current_stage, StageStatus.FAILED, error_code=type(exc).__name__, error_summary=summary)
                self._finish(job_id, JobStatus.FAILED, type(exc).__name__, summary)
                logger.error("job_failed job_id=%s stage=%s error_type=%s", job_id, current_stage, type(exc).__name__)

    def run_bundled(self, task_id: str, job_ids: list[str]) -> None:
        """Run independently persisted analysis stages in reserve batch mode.

        The analysis service owns the analytical implementation; management only
        coordinates its stage APIs and records each article's status.
        """
        logger.info("bundled_task_started task_id=%s job_count=%s", task_id, len(job_ids))
        rows: dict[str, Any] = {}
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
                articles[job_id] = self.client.article(
                    row["article_id"],
                    {"x-job-id": job_id, "x-correlation-id": row["correlation_id"], "x-request-id": row["trace_id"]},
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
            extraction_response = self.client.analysis_stage_bulk(
                "citation_extraction", extraction_payload,
                {"x-task-id": task_id, "x-request-id": str(uuid4())},
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
            self.client.analysis_stage_bulk(
                "extraction_persistence", persistence_payload,
                {"x-task-id": task_id, "x-request-id": str(uuid4())},
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
            result = self.client.analysis_stage_bulk(
                "citation_analysis", analysis_payload,
                {"x-task-id": task_id, "x-request-id": str(uuid4())},
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
                current = self.get(job_id)
                if current["status"] in {JobStatus.RUNNING.value, JobStatus.PENDING.value, JobStatus.RETRYING.value}:
                    self._stage(job_id, current.get("current_stage") or "bundled_analysis", StageStatus.FAILED,
                                error_code=type(exc).__name__, error_summary=summary)
                    self._finish(job_id, JobStatus.FAILED, type(exc).__name__, summary)

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
        request = JobCreateRequest.model_validate(current["config"])
        retry = self.create(
            request,
            f"retry:{job_id}:{current['retry_count'] + 1}",
            initial_status=JobStatus.RETRYING,
            schedule=False,
        )
        with self.database.lock, self.database.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, retry_count=? WHERE job_id=?",
                (JobStatus.RETRYING.value, current["retry_count"] + 1, retry["job_id"]),
            )
        self.executor.submit(self.run, retry["job_id"])
        return self.get(retry["job_id"])

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

    def list(self, status: str | None, article_id: str | None, limit: int) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            query += " AND status=?"; params.append(status)
        if article_id:
            query += " AND article_id=?"; params.append(article_id)
        query += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with self.database.connection() as connection:
            return [self.database.decode(row) for row in connection.execute(query, params)]

    def create_task(self, request: TaskCreateRequest) -> dict[str, Any]:
        task_id = str(uuid4())
        created = timestamp()
        article_ids = [str(item) for item in request.article_ids]
        with self.database.connection() as connection:
            blocked_statuses = "('PENDING','RUNNING','RETRYING')" if request.redo else "('PENDING','RUNNING','RETRYING','COMPLETED')"
            existing = {
                row["article_id"] for row in connection.execute(
                    """SELECT DISTINCT article_id FROM jobs
                       WHERE article_id IN (%s)
                       AND status IN %s"""
                    % (",".join("?" for _ in article_ids), blocked_statuses), article_ids
                ).fetchall()
            }
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
            with self.database.lock, self.database.connection() as connection:
                connection.execute(
                    "INSERT INTO task_jobs(task_id,job_id,article_id) VALUES(?,?,?)",
                    (task_id, job["job_id"], str(article_id)),
                )
                connection.execute(
                    "UPDATE jobs SET metadata_json=? WHERE job_id=?",
                    (json.dumps({"source": "management-task", "task_id": task_id, "execution_mode": request.execution_mode.value}), job["job_id"]),
                )
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
            self.executor.submit(self.run_bundled, task_id, job_ids)
        else:
            for job_id in job_ids:
                self.executor.submit(self.run, job_id)
        return self.task(task_id)

    def task(self, task_id: str) -> dict[str, Any]:
        with self.database.lock, self.database.connection() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(task_id)
            jobs = connection.execute(
                """SELECT j.* FROM jobs j JOIN task_jobs tj ON tj.job_id=j.job_id
                   WHERE tj.task_id=? ORDER BY j.created_at""", (task_id,)
            ).fetchall()
            citations = speakers = topics = attitudes = 0
            for job in jobs:
                events = connection.execute(
                    """SELECT attributes_json FROM telemetry_events
                       WHERE job_id=? AND event_type='pipeline_stage' AND status='completed'""",
                    (job["job_id"],),
                ).fetchall()
                for event in events:
                    attributes = json.loads(event["attributes_json"] or "{}")
                    citations += attributes.get("citation_count", 0) or 0
                    speakers += attributes.get("speaker_count", 0) or 0
                    topics += attributes.get("topic_count", 0) or 0
                    attitudes += attributes.get("attitude_count", 0) or 0
            statuses = [job["status"] for job in jobs]
            completed = statuses.count(JobStatus.COMPLETED.value)
            failed = statuses.count(JobStatus.FAILED.value)
            running = statuses.count(JobStatus.RUNNING.value)
            waiting = statuses.count(JobStatus.PENDING.value) + statuses.count(JobStatus.RETRYING.value)
            self._refresh_task_status(connection, task_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return {
                "task_id": task["task_id"], "name": task["name"], "status": task["status"],
                "article_ids": json.loads(task["article_ids_json"]),
                "job_ids": [job["job_id"] for job in jobs],
                "total_articles": len(jobs), "completed_articles": completed,
                "failed_articles": failed, "running_articles": running,
                "waiting_articles": waiting, "citations": citations, "speakers": speakers,
                "execution_mode": task["execution_mode"], "citations": citations, "speakers": speakers,
                "topics": topics, "attitudes": attitudes,
                "created_at": task["created_at"], "started_at": task["started_at"],
                "completed_at": task["completed_at"],
            }

    def list_tasks(self, limit: int) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            ids = [row["task_id"] for row in connection.execute(
                "SELECT task_id FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()]
        return [self.task(task_id) for task_id in ids]

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        for job_id in task["job_ids"]:
            try:
                self.cancel(job_id)
            except KeyError:
                continue
        return self.task(task_id)

    def delete_task(self, task_id: str) -> None:
        task = self.task(task_id)
        active = {"PENDING", "RUNNING", "RETRYING"}
        if task["status"] in active:
            raise ValueError("Only completed, failed, or cancelled tasks can be deleted")
        with self.database.lock, self.database.connection() as connection:
            job_ids = [
                row["job_id"]
                for row in connection.execute(
                    "SELECT job_id FROM task_jobs WHERE task_id=?", (task_id,)
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
            row = connection.execute("SELECT article_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            article_id = row["article_id"] if row else None
            connection.execute(
                "UPDATE jobs SET status=?, current_stage=NULL, completed_at=?, "
                "error_code=?, error_summary=? WHERE job_id=?",
                (status.value, timestamp(), code, summary, job_id),
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
            }.get(status)
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
            task_status = TaskStatus.FAILED.value
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
