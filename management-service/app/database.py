from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ManagementDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.lock, self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, article_id TEXT NOT NULL,
                    status TEXT NOT NULL, current_stage TEXT, created_at TEXT NOT NULL,
                    started_at TEXT, completed_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
                    correlation_id TEXT NOT NULL, trace_id TEXT NOT NULL,
                    idempotency_key TEXT, config_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, error_code TEXT, error_summary TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS job_stages (
                    stage_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
                    status TEXT NOT NULL, attempt INTEGER NOT NULL, started_at TEXT NOT NULL,
                    completed_at TEXT, duration_ms INTEGER, summary_json TEXT NOT NULL,
                    error_code TEXT, error_summary TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, service TEXT NOT NULL,
                    level TEXT NOT NULL, event_type TEXT NOT NULL, job_id TEXT,
                    correlation_id TEXT, request_id TEXT, article_id TEXT, stage TEXT,
                    duration_ms INTEGER, attributes_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL, service TEXT NOT NULL,
                    level TEXT NOT NULL, event_type TEXT NOT NULL,
                    operation TEXT, status TEXT, job_id TEXT, correlation_id TEXT,
                    request_id TEXT, article_id TEXT, stage TEXT, trace_id TEXT,
                    span_id TEXT, parent_span_id TEXT, started_at TEXT,
                    completed_at TEXT, duration_ms INTEGER,
                    time_to_first_token_ms INTEGER, input_tokens INTEGER,
                    output_tokens INTEGER, input_tokens_estimated INTEGER,
                    output_tokens_estimated INTEGER,
                    throughput_tokens_per_second REAL, model TEXT, provider TEXT,
                    finish_reason TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
                    error_category TEXT, error_detail TEXT,
                    attributes_json TEXT NOT NULL, debug_content_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_telemetry_job ON telemetry_events(job_id);
                CREATE INDEX IF NOT EXISTS idx_telemetry_trace ON telemetry_events(trace_id);
                CREATE INDEX IF NOT EXISTS idx_telemetry_type ON telemetry_events(event_type);
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY, description TEXT NOT NULL,
                    dataset_name TEXT, article_ids_json TEXT NOT NULL,
                    model_config_json TEXT NOT NULL, prompt_versions_json TEXT NOT NULL,
                    pipeline_parameters_json TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS experiment_jobs (
                    experiment_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    article_id TEXT NOT NULL, PRIMARY KEY (experiment_id, job_id),
                    UNIQUE (experiment_id, article_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_evaluations (
                    evaluation_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL UNIQUE,
                    evaluated_items INTEGER NOT NULL, true_positives INTEGER NOT NULL,
                    false_positives INTEGER NOT NULL, false_negatives INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                    article_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    started_at TEXT, completed_at TEXT,
                    execution_mode TEXT NOT NULL DEFAULT 'per_article'
                );
                CREATE TABLE IF NOT EXISTS task_jobs (
                    task_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    article_id TEXT NOT NULL, PRIMARY KEY (task_id, job_id),
                    UNIQUE (task_id, article_id)
                );
                CREATE INDEX IF NOT EXISTS idx_experiment_jobs_job ON experiment_jobs(job_id);
                CREATE INDEX IF NOT EXISTS idx_task_jobs_job ON task_jobs(job_id);
                """
            )
            self._migrate_jobs(connection)
            self._migrate_job_stages(connection)
            task_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "execution_mode" not in task_columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'per_article'"
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_idempotency "
                "ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
            # A process restart cannot safely resume a blocking HTTP/LLM operation.
            connection.execute(
                "UPDATE jobs SET status='FAILED', completed_at=?, error_code='MANAGEMENT_RESTART', "
                "error_summary='Management service restarted while the job was active' "
                "WHERE status IN ('PENDING','RUNNING','RETRYING')",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.execute(
                """UPDATE tasks
                   SET status=CASE
                       WHEN EXISTS (
                           SELECT 1 FROM task_jobs tj JOIN jobs j ON j.job_id=tj.job_id
                           WHERE tj.task_id=tasks.task_id
                             AND j.status IN ('PENDING','RUNNING','RETRYING')
                       ) THEN 'RUNNING'
                       WHEN EXISTS (
                           SELECT 1 FROM task_jobs tj JOIN jobs j ON j.job_id=tj.job_id
                           WHERE tj.task_id=tasks.task_id AND j.status='CANCELLED'
                       ) THEN 'CANCELLED'
                       WHEN EXISTS (
                           SELECT 1 FROM task_jobs tj JOIN jobs j ON j.job_id=tj.job_id
                           WHERE tj.task_id=tasks.task_id AND j.status='FAILED'
                       ) THEN 'FAILED'
                       ELSE 'COMPLETED'
                   END,
                   completed_at=CASE
                       WHEN EXISTS (
                           SELECT 1 FROM task_jobs tj JOIN jobs j ON j.job_id=tj.job_id
                           WHERE tj.task_id=tasks.task_id
                             AND j.status IN ('PENDING','RUNNING','RETRYING')
                       ) THEN NULL
                       ELSE COALESCE(completed_at,?)
                   END
                   WHERE EXISTS (
                       SELECT 1 FROM task_jobs tj WHERE tj.task_id=tasks.task_id
                   )""",
                (datetime.now(timezone.utc).isoformat(),),
            )

    @staticmethod
    def _migrate_jobs(connection: sqlite3.Connection) -> None:
        """Upgrade the original Phase 0 schema without discarding job history."""
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        additions: dict[str, str] = {
            "current_stage": "TEXT",
            "created_at": "TEXT",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "trace_id": "TEXT",
            "idempotency_key": "TEXT",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "error_summary": "TEXT",
        }
        for column, definition in additions.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")

        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "requested_at" in columns:
            connection.execute(
                "UPDATE jobs SET created_at=COALESCE(created_at, requested_at) "
                "WHERE created_at IS NULL"
            )
        connection.execute(
            "UPDATE jobs SET created_at=COALESCE(created_at, datetime('now')), "
            "trace_id=COALESCE(trace_id, correlation_id), "
            "status=CASE WHEN status='QUEUED' THEN 'PENDING' ELSE status END"
        )
        if "error_message" in columns:
            connection.execute(
                "UPDATE jobs SET error_summary=COALESCE(error_summary, error_message)"
            )

    @staticmethod
    def _migrate_job_stages(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(job_stages)").fetchall()
        }
        if "error_summary" not in columns:
            connection.execute("ALTER TABLE job_stages ADD COLUMN error_summary TEXT")
        if "error_message" in columns:
            connection.execute(
                "UPDATE job_stages SET error_summary=COALESCE(error_summary, error_message)"
            )

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in ("config_json", "metadata_json", "summary_json", "attributes_json", "debug_content_json"):
            if key in value:
                value[key.removesuffix("_json")] = json.loads(value.pop(key) or "{}")
        for key in ("cancel_requested",):
            if key in value:
                value[key] = bool(value[key])
        return value
