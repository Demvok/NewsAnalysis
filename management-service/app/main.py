from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

DATA_DIR = Path(os.getenv("MANAGEMENT_DATA_DIR", "/app/data"))
DATABASE_PATH = Path(os.getenv("MANAGEMENT_DATABASE_PATH", str(DATA_DIR / "management.db")))
ANALYSIS_URL = os.getenv("ANALYSIS_URL", "http://analysis-layer:7000").rstrip("/")
STORAGE_URL = os.getenv("STORAGE_URL", "http://data-storage:8000").rstrip("/")
LOG_ROOTS = {
    "analysis": Path(os.getenv("ANALYSIS_LOG_DIR", "/var/log/newsanalysis/analysis")),
    "data-storage": Path(os.getenv("STORAGE_LOG_DIR", "/var/log/newsanalysis/data-storage")),
}
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MANAGEMENT_MAX_CONCURRENT_JOBS", "1")))
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="management-job")
db_lock = threading.Lock()

app = FastAPI(title="NewsAnalysis Management", version="0.1.0")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with db_lock, db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, article_id TEXT NOT NULL,
                status TEXT NOT NULL, requested_at TEXT NOT NULL, started_at TEXT,
                completed_at TEXT, correlation_id TEXT NOT NULL, config_json TEXT NOT NULL,
                error_code TEXT, error_message TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS job_stages (
                stage_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
                status TEXT NOT NULL, attempt INTEGER NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT, duration_ms INTEGER, summary_json TEXT, error_code TEXT,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, service TEXT NOT NULL,
                level TEXT, event_type TEXT NOT NULL, job_id TEXT, correlation_id TEXT,
                request_id TEXT, article_id TEXT, stage TEXT, duration_ms INTEGER,
                attributes_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
                created_at TEXT NOT NULL, configuration_json TEXT NOT NULL,
                dataset_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiment_runs (
                run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, status TEXT NOT NULL,
                job_ids_json TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
                evaluation_json TEXT
            );
            """
        )
        connection.execute("UPDATE jobs SET status='ABANDONED' WHERE status IN ('QUEUED','RUNNING')")


# Ensure direct ASGI usage and CLI smoke tests have the same schema guarantee as startup.
init_db()


class JobCreate(BaseModel):
    article_id: UUID
    inconsistency_window_days: int = Field(default=365, ge=1, le=3650)


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    configuration: dict[str, Any] = Field(default_factory=dict)
    article_ids: list[UUID] = Field(default_factory=list)


class Event(BaseModel):
    event_id: str | None = None
    timestamp: str | None = None
    service: str
    level: str = "INFO"
    event_type: str
    job_id: str | None = None
    correlation_id: str | None = None
    request_id: str | None = None
    article_id: str | None = None
    stage: str | None = None
    duration_ms: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


def row_json(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("config_json", "summary_json", "attributes_json", "configuration_json", "dataset_json", "job_ids_json", "evaluation_json"):
        if key in result:
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or "{}")
    if "cancel_requested" in result:
        result["cancel_requested"] = bool(result["cancel_requested"])
    return result


def get_job(job_id: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Job not found")
    return row_json(row)


def stage(job_id: str, name: str, status: str, started: str, summary: dict[str, Any] | None = None, error: Exception | None = None) -> None:
    completed = now() if status in {"completed", "failed", "cancelled"} else None
    duration = None
    if completed:
        duration = max(0, int((datetime.fromisoformat(completed) - datetime.fromisoformat(started)).total_seconds() * 1000))
    with db_lock, db() as connection:
        connection.execute(
            "INSERT INTO job_stages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid4()), job_id, name, status, 1, started, completed, duration,
             json.dumps(summary or {}), type(error).__name__ if error else None,
             str(error)[:500] if error else None),
        )
    emit(Event(service="management", event_type=f"stage_{status}", job_id=job_id, stage=name, duration_ms=duration, attributes=summary or {}))


def emit(event: Event) -> None:
    payload = event.model_dump()
    event_id = payload["event_id"] or str(uuid4())
    with db_lock, db() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, payload["timestamp"] or now(), payload["service"], payload["level"],
             payload["event_type"], payload["job_id"], payload["correlation_id"],
             payload["request_id"], payload["article_id"], payload["stage"],
             payload["duration_ms"], json.dumps(payload["attributes"])),
        )


def job_worker(job_id: str, request: JobCreate, correlation_id: str) -> None:
    with db() as connection:
        current = connection.execute("SELECT status, cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if current is None or current["status"] == "CANCELLED" or current["cancel_requested"]:
        return
    started = now()
    with db_lock, db() as connection:
        connection.execute("UPDATE jobs SET status='RUNNING', started_at=? WHERE job_id=?", (started, job_id))
    headers = {"x-job-id": job_id, "x-correlation-id": correlation_id, "x-request-id": correlation_id}
    try:
        stage_start = now()
        stage(job_id, "article_retrieval", "started", stage_start)
        response = httpx.get(f"{STORAGE_URL}/articles/{request.article_id}", headers=headers, timeout=30)
        response.raise_for_status()
        article = response.json()
        stage(job_id, "article_retrieval", "completed", stage_start, {"article_id": str(request.article_id)})

        with db() as connection:
            cancelled = connection.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]
        if cancelled:
            finish_job(job_id, "CANCELLED_AFTER_STAGE")
            return

        stage_start = now()
        stage(job_id, "analysis", "started", stage_start)
        payload = {
            "article_id": str(request.article_id),
            "article_title": article.get("article_title") or "",
            "article_content": article.get("article_content") or "",
            "published_at": article.get("published_at"),
            "inconsistency_window_days": request.inconsistency_window_days,
        }
        response = httpx.post(f"{ANALYSIS_URL}/analysis/citations", json=payload, headers=headers, timeout=None)
        response.raise_for_status()
        result = response.json()
        stage(job_id, "analysis", "completed", stage_start, {
            "speaker_count": len(result.get("speakers", [])),
            "citation_count": sum(len(item.get("citations", [])) for item in result.get("speakers", [])),
        })
        finish_job(job_id, "SUCCEEDED")
    except Exception as error:
        emit(Event(service="management", event_type="job_failed", job_id=job_id, correlation_id=correlation_id, attributes={"error_type": type(error).__name__}))
        with db_lock, db() as connection:
            connection.execute(
                "UPDATE jobs SET status='FAILED', completed_at=?, error_code=?, error_message=? WHERE job_id=?",
                (now(), type(error).__name__, str(error)[:500], job_id),
            )


def finish_job(job_id: str, status: str) -> None:
    with db_lock, db() as connection:
        connection.execute("UPDATE jobs SET status=?, completed_at=? WHERE job_id=?", (status, now(), job_id))


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "version": app.version}


@app.get("/api/services")
def services() -> list[dict[str, Any]]:
    result = []
    for name, url in (("analysis-layer", ANALYSIS_URL), ("data-storage", STORAGE_URL)):
        started = time.perf_counter()
        try:
            response = httpx.get(f"{url}/health", timeout=5)
            details = response.json()
            status = "healthy" if response.is_success else "degraded"
            error = None
        except Exception as exc:
            details, status, error = {}, "unavailable", type(exc).__name__
        result.append({"service": name, "status": status, "latency_ms": int((time.perf_counter() - started) * 1000), "details": details, "error": error, "checked_at": now()})
    return result


@app.post("/api/jobs", status_code=202)
def create_job(request: JobCreate) -> dict[str, Any]:
    job_id, correlation_id = str(uuid4()), str(uuid4())
    with db_lock, db() as connection:
        connection.execute(
            "INSERT INTO jobs(job_id,job_type,article_id,status,requested_at,correlation_id,config_json) VALUES(?,?,?,?,?,?,?)",
            (job_id, "article_analysis", str(request.article_id), "QUEUED", now(), correlation_id, json.dumps(request.model_dump(mode="json"))),
        )
    executor.submit(job_worker, job_id, request, correlation_id)
    return get_job(job_id)


@app.get("/api/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=500), status: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM jobs"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY requested_at DESC LIMIT ?"
    params.append(limit)
    with db() as connection:
        return [row_json(row) for row in connection.execute(query, params).fetchall()]


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    return get_job(job_id)


@app.get("/api/jobs/{job_id}/stages")
def job_stages(job_id: str) -> list[dict[str, Any]]:
    get_job(job_id)
    with db() as connection:
        return [row_json(row) for row in connection.execute("SELECT * FROM job_stages WHERE job_id=? ORDER BY started_at", (job_id,)).fetchall()]


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job["status"] == "QUEUED":
        finish_job(job_id, "CANCELLED")
    elif job["status"] == "RUNNING":
        with db_lock, db() as connection:
            connection.execute("UPDATE jobs SET cancel_requested=1, status='CANCEL_REQUESTED' WHERE job_id=?", (job_id,))
    return get_job(job_id)


@app.post("/api/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job["status"] not in {"FAILED", "ABANDONED", "CANCELLED", "CANCELLED_AFTER_STAGE"}:
        raise HTTPException(409, "Only failed or stopped jobs can be retried")
    return create_job(JobCreate(article_id=job["article_id"], inconsistency_window_days=job["config"].get("inconsistency_window_days", 365)))


@app.post("/internal/events", status_code=202)
def receive_event(event: Event) -> dict[str, str]:
    emit(event)
    return {"status": "accepted"}


@app.get("/api/telemetry/events")
def telemetry(limit: int = Query(100, ge=1, le=1000), job_id: str | None = None) -> list[dict[str, Any]]:
    query, params = "SELECT * FROM events", []
    if job_id:
        query += " WHERE job_id=?"
        params.append(job_id)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    with db() as connection:
        return [row_json(row) for row in connection.execute(query, params).fetchall()]


def redact(value: str) -> str:
    return re.sub(r"(?i)(password|api[_-]?key|token|secret)=\S+", r"\1=[REDACTED]", value)


@app.get("/api/logs")
def logs(service: Literal["analysis", "data-storage"] | None = None, limit: int = Query(200, ge=1, le=1000), contains: str | None = None) -> list[dict[str, Any]]:
    roots = {service: LOG_ROOTS[service]} if service else LOG_ROOTS
    lines: list[dict[str, Any]] = []
    for name, root in roots.items():
        if not root.exists():
            continue
        for path in root.glob("*.log"):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
                safe = redact(line)
                if contains and contains.casefold() not in safe.casefold():
                    continue
                lines.append({"service": name, "file": path.name, "line": safe})
    return lines[-limit:]


@app.post("/api/experiments", status_code=201)
def create_experiment(request: ExperimentCreate) -> dict[str, Any]:
    experiment_id = str(uuid4())
    with db_lock, db() as connection:
        connection.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?)", (experiment_id, request.name, request.description, now(), json.dumps(request.configuration), json.dumps([str(item) for item in request.article_ids])))
    with db() as connection:
        return row_json(connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone())


@app.get("/api/experiments")
def list_experiments(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    with db() as connection:
        return [row_json(row) for row in connection.execute("SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]


@app.get("/api/experiments/{experiment_id}")
def experiment(experiment_id: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Experiment not found")
    return row_json(row)


@app.post("/api/experiments/{experiment_id}/runs", status_code=202)
def run_experiment(experiment_id: str) -> dict[str, Any]:
    definition = experiment(experiment_id)
    article_ids = definition["dataset"]
    run_id = str(uuid4())
    job_ids: list[str] = []
    for article_id in article_ids:
        job = create_job(JobCreate(article_id=article_id))
        job_ids.append(job["job_id"])
    with db_lock, db() as connection:
        connection.execute(
            "INSERT INTO experiment_runs VALUES (?,?,?,?,?,?,?)",
            (run_id, experiment_id, "RUNNING", json.dumps(job_ids), now(), None, None),
        )
    return {"run_id": run_id, "experiment_id": experiment_id, "status": "RUNNING", "job_ids": job_ids}


@app.get("/api/experiments/{experiment_id}/runs")
def experiment_runs(experiment_id: str, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    experiment(experiment_id)
    with db() as connection:
        return [row_json(row) for row in connection.execute(
            "SELECT * FROM experiment_runs WHERE experiment_id=? ORDER BY started_at DESC LIMIT ?",
            (experiment_id, limit),
        ).fetchall()]


@app.get("/api/telemetry/summary")
def telemetry_summary() -> dict[str, Any]:
    with db() as connection:
        jobs = connection.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
        events = connection.execute("SELECT event_type, COUNT(*) AS count FROM events GROUP BY event_type").fetchall()
    return {"jobs": {row["status"]: row["count"] for row in jobs}, "events": {row["event_type"]: row["count"] for row in events}}


dashboard = Path(__file__).parent.parent / "dashboard" / "dist"
if dashboard.exists():
    @app.get("/", include_in_schema=False)
    def dashboard_index() -> FileResponse:
        return FileResponse(dashboard / "index.html")
