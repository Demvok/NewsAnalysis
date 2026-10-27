from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .clients import ServiceClient
from .config import Settings
from .database import ManagementDatabase
from .models import (
    EventRequest,
    JobCreateRequest,
    JobResponse,
    JobStatus,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdate,
    ServiceDependency,
    StageResponse,
    SystemHealthResponse,
)
from .orchestration import JobCoordinator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("newsanalysis.management")

settings = Settings.from_env()
database = ManagementDatabase(settings.database_path)
database.initialize()
client = ServiceClient(settings)
coordinator = JobCoordinator(database, client, settings.max_concurrent_jobs)

app = FastAPI(
    title="NewsAnalysis Management Service",
    description="Operational API for health, job coordination, pipeline history, and safe runtime settings.",
    version="0.2.0",
)


def parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def job_response(value: dict[str, Any]) -> JobResponse:
    return JobResponse(
        job_id=value["job_id"], job_type=value["job_type"], article_id=value["article_id"],
        status=value["status"], current_stage=value.get("current_stage"),
        created_at=parse_datetime(value["created_at"]), started_at=parse_datetime(value.get("started_at")),
        completed_at=parse_datetime(value.get("completed_at")), retry_count=value["retry_count"],
        error_code=value.get("error_code"), error_summary=value.get("error_summary"),
        correlation_id=value["correlation_id"], trace_id=value["trace_id"],
        metadata=value.get("metadata", {}),
    )


def stage_response(value: dict[str, Any]) -> StageResponse:
    return StageResponse(
        stage_id=value["stage_id"], job_id=value["job_id"], stage=value["stage"],
        status=value["status"], attempt=value["attempt"],
        started_at=parse_datetime(value["started_at"]),
        completed_at=parse_datetime(value.get("completed_at")),
        duration_ms=value.get("duration_ms"), summary=value.get("summary", {}),
        error_code=value.get("error_code"), error_summary=value.get("error_summary"),
    )


@app.exception_handler(KeyError)
async def not_found(_: Request, exception: KeyError):
    return JSONResponse(status_code=404, content={"detail": f"Resource not found: {exception.args[0]}"})


@app.get("/health", response_model=SystemHealthResponse, tags=["Health"])
def health() -> SystemHealthResponse:
    dependencies = client.dependencies()
    status = "HEALTHY" if all(item.status == "HEALTHY" for item in dependencies[:2]) else "DEGRADED"
    return SystemHealthResponse(status=status, service="management-service", version=app.version, dependencies=dependencies)


@app.get("/api/services", response_model=list[ServiceDependency], tags=["Health"])
def services() -> list[ServiceDependency]:
    return client.dependencies()


@app.get("/api/services/{service_name}", response_model=ServiceDependency, tags=["Health"])
def service_status(service_name: str) -> ServiceDependency:
    targets = {"analysis-layer": settings.analysis_url, "data-storage": settings.storage_url}
    if service_name == "llm-provider":
        return client.dependencies()[-1]
    if service_name not in targets:
        raise HTTPException(status_code=404, detail="Unknown service")
    return client.health(service_name, targets[service_name])


@app.get("/api/jobs", response_model=list[JobResponse], tags=["Jobs"])
def list_jobs(
    status: JobStatus | None = None,
    article_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[JobResponse]:
    return [job_response(item) for item in coordinator.list(status.value if status else None, str(article_id) if article_id else None, limit)]


@app.post("/api/jobs", response_model=JobResponse, status_code=202, tags=["Jobs"])
def create_job(
    request: JobCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobResponse:
    if request.idempotency_key and idempotency_key and request.idempotency_key != idempotency_key:
        raise HTTPException(status_code=400, detail="Request body and Idempotency-Key header differ")
    value = coordinator.create(request, idempotency_key or request.idempotency_key)
    return job_response(value)


@app.get("/api/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"])
def get_job(job_id: UUID) -> JobResponse:
    try:
        return job_response(coordinator.get(str(job_id)))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None


@app.get("/api/jobs/{job_id}/progress", response_model=JobResponse, tags=["Jobs"])
def job_progress(job_id: UUID) -> JobResponse:
    return get_job(job_id)


@app.get("/api/jobs/{job_id}/stages", response_model=list[StageResponse], tags=["Jobs"])
def job_stages(job_id: UUID) -> list[StageResponse]:
    try:
        return [stage_response(item) for item in coordinator.stages(str(job_id))]
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None


@app.post("/api/jobs/{job_id}/cancel", response_model=JobResponse, tags=["Jobs"])
def cancel_job(job_id: UUID) -> JobResponse:
    try:
        return job_response(coordinator.cancel(str(job_id)))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None


@app.post("/api/jobs/{job_id}/retry", response_model=JobResponse, status_code=202, tags=["Jobs"])
def retry_job(job_id: UUID) -> JobResponse:
    try:
        return job_response(coordinator.retry(str(job_id)))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/settings", response_model=RuntimeSettingsResponse, tags=["Settings"])
def get_settings() -> RuntimeSettingsResponse:
    return RuntimeSettingsResponse(
        request_timeout_seconds=settings.request_timeout_seconds,
        analysis_timeout_seconds=settings.analysis_timeout_seconds,
        configurable_fields=["request_timeout_seconds", "analysis_timeout_seconds"],
    )


@app.patch("/api/settings", response_model=RuntimeSettingsResponse, tags=["Settings"])
def update_settings(request: RuntimeSettingsUpdate) -> RuntimeSettingsResponse:
    updates = request.model_dump(exclude_none=True)
    if "request_timeout_seconds" in updates:
        settings.request_timeout_seconds = updates["request_timeout_seconds"]
    if "analysis_timeout_seconds" in updates:
        settings.analysis_timeout_seconds = updates["analysis_timeout_seconds"]
    logger.info("runtime_settings_updated fields=%s", sorted(updates))
    return get_settings()


@app.post("/internal/events", status_code=202, tags=["Telemetry"])
def receive_event(event: EventRequest) -> dict[str, str]:
    event_id = str(event.event_id or uuid4())
    with database.lock, database.connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id, (event.timestamp or datetime.now(timezone.utc)).isoformat(),
                event.service, event.level, event.event_type, str(event.job_id) if event.job_id else None,
                str(event.correlation_id) if event.correlation_id else None,
                str(event.request_id) if event.request_id else None,
                str(event.article_id) if event.article_id else None, event.stage,
                event.duration_ms, json.dumps(event.attributes),
            ),
        )
        connection.execute(
            """INSERT OR IGNORE INTO telemetry_events
            (event_id,timestamp,service,level,event_type,operation,status,job_id,
             correlation_id,request_id,article_id,stage,trace_id,span_id,parent_span_id,
             started_at,completed_at,duration_ms,time_to_first_token_ms,input_tokens,
             output_tokens,input_tokens_estimated,output_tokens_estimated,
             throughput_tokens_per_second,model,provider,finish_reason,retry_count,
             error_category,error_detail,attributes_json,debug_content_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id, (event.timestamp or datetime.now(timezone.utc)).isoformat(),
                event.service, event.level, event.event_type, event.operation, event.status,
                str(event.job_id) if event.job_id else None,
                str(event.correlation_id) if event.correlation_id else None,
                str(event.request_id) if event.request_id else None,
                str(event.article_id) if event.article_id else None, event.stage,
                str(event.trace_id) if event.trace_id else None, event.span_id,
                event.parent_span_id,
                event.started_at.isoformat() if event.started_at else None,
                event.completed_at.isoformat() if event.completed_at else None,
                event.duration_ms, event.time_to_first_token_ms, event.input_tokens,
                event.output_tokens, event.input_tokens_estimated,
                event.output_tokens_estimated, event.throughput_tokens_per_second,
                event.model, event.provider, event.finish_reason, event.retry_count,
                event.error_category, event.error_detail, json.dumps(event.attributes),
                json.dumps(event.debug_content) if event.debug_content else None,
            ),
        )
    return {"status": "accepted"}


@app.get("/api/pipeline/history", tags=["Pipeline"])
def pipeline_history(job_id: UUID | None = None, limit: int = Query(100, ge=1, le=1000)) -> list[StageResponse]:
    if job_id is not None:
        return [stage_response(item) for item in coordinator.stages(str(job_id))]
    with database.connection() as connection:
        rows = connection.execute("SELECT * FROM job_stages ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [stage_response(database.decode(row)) for row in rows]


def redact(value: str) -> str:
    return re.sub(r"(?i)(password|api[_-]?key|token|secret)=\S+", r"\1=[REDACTED]", value)


@app.get("/api/logs", tags=["Diagnostics"])
def logs(
    service: str | None = None,
    contains: str | None = None,
    level: str | None = None,
    job_id: UUID | None = None,
    trace_id: UUID | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    query = "SELECT * FROM telemetry_events WHERE 1=1"
    params: list[Any] = []
    if service:
        query += " AND service=?"; params.append(service)
    if level:
        query += " AND level=?"; params.append(level.upper())
    if job_id:
        query += " AND job_id=?"; params.append(str(job_id))
    if trace_id:
        query += " AND trace_id=?"; params.append(str(trace_id))
    query += " ORDER BY sequence DESC LIMIT ?"; params.append(limit)
    with database.connection() as connection:
        structured = [database.decode(row) for row in connection.execute(query, params)]
    if contains:
        structured = [
            item for item in structured
            if contains.casefold() in json.dumps(item, default=str).casefold()
        ]
    if structured:
        return structured

    # Legacy text logs are only a bounded fallback when structured records
    # cannot satisfy the request.
    roots = {"analysis": settings.analysis_log_dir, "data-storage": settings.storage_log_dir}
    if service:
        if service not in roots:
            raise HTTPException(status_code=400, detail="Unsupported log service")
        roots = {service: roots[service]}
    results: list[dict[str, Any]] = []
    for name, root in roots.items():
        if not root.exists():
            continue
        for path in root.glob("*.log"):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
                safe = redact(line)
                if not contains or contains.casefold() in safe.casefold():
                    results.append({"service": name, "file": path.name, "line": safe})
    return results[-limit:]


@app.get("/api/metrics/llm", tags=["Telemetry"])
def llm_metrics(
    since: datetime | None = None,
    until: datetime | None = None,
    model: str | None = None,
    job_id: UUID | None = None,
    article_id: UUID | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    query = "SELECT * FROM telemetry_events WHERE event_type='llm_call'"
    params: list[Any] = []
    if since:
        query += " AND timestamp>=?"; params.append(since.isoformat())
    if until:
        query += " AND timestamp<=?"; params.append(until.isoformat())
    for column, value in (("model", model), ("job_id", str(job_id) if job_id else None),
                          ("article_id", str(article_id) if article_id else None), ("stage", stage)):
        if value:
            query += f" AND {column}=?"; params.append(value)
    with database.connection() as connection:
        rows = [dict(row) for row in connection.execute(query, params)]
    durations = [row["duration_ms"] for row in rows if row["duration_ms"] is not None]
    ttft = [row["time_to_first_token_ms"] for row in rows if row["time_to_first_token_ms"] is not None]
    throughput = [row["throughput_tokens_per_second"] for row in rows if row["throughput_tokens_per_second"] is not None]
    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
        return ordered[index]
    return {
        "call_count": len(rows),
        "successful_calls": sum(row["status"] == "completed" for row in rows),
        "failed_calls": sum(row["status"] == "failed" for row in rows),
        "retry_count": sum(row["retry_count"] for row in rows),
        "input_tokens_measured": sum(row["input_tokens"] or 0 for row in rows),
        "output_tokens_measured": sum(row["output_tokens"] or 0 for row in rows),
        "input_tokens_estimated": sum(row["input_tokens_estimated"] or 0 for row in rows),
        "output_tokens_estimated": sum(row["output_tokens_estimated"] or 0 for row in rows),
        "latency_ms": {"average": sum(durations) / len(durations) if durations else None,
                       "median": percentile(durations, 0.5), "p95": percentile(durations, 0.95)},
        "time_to_first_token_ms": {"average": sum(ttft) / len(ttft) if ttft else None,
                                   "median": percentile(ttft, 0.5), "p95": percentile(ttft, 0.95)},
        "throughput_tokens_per_second_average": sum(throughput) / len(throughput) if throughput else None,
    }


@app.get("/api/metrics/stages", tags=["Telemetry"])
def stage_metrics(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    with database.connection() as connection:
        rows = connection.execute(
            """SELECT stage, COUNT(*) AS count, AVG(duration_ms) AS average_duration_ms,
                      SUM(duration_ms) AS total_duration_ms
               FROM telemetry_events WHERE event_type='pipeline_stage' AND status='completed'
               GROUP BY stage ORDER BY total_duration_ms DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/metrics/jobs", tags=["Telemetry"])
def job_metrics() -> dict[str, Any]:
    with database.connection() as connection:
        counts = connection.execute(
            "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
        ).fetchall()
        completed = connection.execute(
            "SELECT COUNT(*) AS count FROM jobs WHERE status='COMPLETED'"
        ).fetchone()["count"]
    return {
        "queue_depth": sum(row["count"] for row in counts if row["status"] in {"PENDING", "RETRYING"}),
        "throughput_completed_jobs": completed,
        "by_status": {row["status"]: row["count"] for row in counts},
    }


@app.get("/api/logs/errors", tags=["Diagnostics"])
def grouped_errors(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    with database.connection() as connection:
        rows = connection.execute(
            """SELECT COALESCE(error_category, event_type) AS error_key,
                      COUNT(*) AS count, MAX(timestamp) AS last_seen,
                      MAX(error_detail) AS sample
               FROM telemetry_events
               WHERE status='failed' OR level IN ('ERROR', 'CRITICAL')
               GROUP BY error_key ORDER BY count DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/events/stream", tags=["Telemetry"])
def event_stream(last_event_id: int | None = None):
    start = last_event_id or 0
    def generate():
        cursor = start
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            with database.connection() as connection:
                rows = connection.execute(
                    "SELECT sequence,event_id,event_type,timestamp,service,job_id,trace_id,status "
                    "FROM telemetry_events WHERE sequence>? ORDER BY sequence LIMIT 100", (cursor,)
                ).fetchall()
            if rows:
                for row in rows:
                    cursor = row["sequence"]
                    payload = json.dumps(dict(row), default=str)
                    yield f"id: {cursor}\nevent: telemetry\ndata: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"
                time.sleep(1)
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
