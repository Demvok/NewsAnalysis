from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse, Response

from .clients import ServiceClient
from .config import Settings
from .database import ManagementDatabase
from .models import (
    EventRequest,
    EvaluationCreateRequest,
    ExperimentComparisonRequest,
    ExperimentCreateRequest,
    ExperimentResponse,
    ExperimentStatus,
    JobCreateRequest,
    JobResponse,
    JobStatus,
    KnowledgeSummaryResponse,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdate,
    ServiceDependency,
    StageResponse,
    SystemHealthResponse,
    TaskCreateRequest,
    TaskResponse,
)


def _experiment_metrics(connection, experiment_id: str) -> dict[str, Any]:
    jobs = connection.execute(
        """SELECT j.* FROM jobs j JOIN experiment_jobs ej ON ej.job_id=j.job_id
           WHERE ej.experiment_id=?""", (experiment_id,)
    ).fetchall()
    job_ids = [row["job_id"] for row in jobs]
    metrics: dict[str, Any] = {
        "article_count": len(jobs),
        "completed_articles": sum(row["status"] == "COMPLETED" for row in jobs),
        "failed_articles": sum(row["status"] == "FAILED" for row in jobs),
        "retry_count": sum(row["retry_count"] for row in jobs),
        "processing_time_ms_per_article": None,
        "llm_calls_per_article": None,
        "input_tokens_per_article": None,
        "output_tokens_per_article": None,
        "latency_ms": {"average": None, "p95": None},
        "throughput_tokens_per_second": None,
        "failure_rate": None,
        "retry_rate": None,
        "stages": [],
        "analysis_counts": {
            "speakers": 0,
            "citations_extracted": 0,
            "topics_resolved": 0,
            "attitudes_persisted": 0,
            "inconsistency_comparisons": 0,
            "low_confidence_results": None,
        },
        "quality": None,
    }
    evaluation = connection.execute(
        "SELECT * FROM experiment_evaluations WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    if evaluation:
        tp, fp, fn = evaluation["true_positives"], evaluation["false_positives"], evaluation["false_negatives"]
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        metrics["quality"] = {
            "evaluated_items": evaluation["evaluated_items"],
            "precision": precision,
            "recall": recall,
            "f1": (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and precision + recall else None,
            "source": "manual_ground_truth_evaluation",
        }
    if not job_ids:
        return metrics
    placeholders = ",".join("?" for _ in job_ids)
    telemetry = connection.execute(
        f"SELECT * FROM telemetry_events WHERE job_id IN ({placeholders})",
        job_ids,
    ).fetchall()
    llm = [row for row in telemetry if row["event_type"] == "llm_call"]
    for row in telemetry:
        if row["event_type"] != "pipeline_stage" or row["status"] != "completed":
            continue
        attributes = json.loads(row["attributes_json"] or "{}")
        counts = metrics["analysis_counts"]
        counts["speakers"] += attributes.get("speaker_count", 0) or 0
        counts["citations_extracted"] += attributes.get("citation_count", 0) or 0
        counts["topics_resolved"] += attributes.get("topic_count", 0) or 0
        counts["attitudes_persisted"] += attributes.get("attitude_count", 0) or 0
        counts["inconsistency_comparisons"] += attributes.get("comparison_count", 0) or 0
    durations = [row["duration_ms"] for row in llm if row["duration_ms"] is not None]
    processing = [
        (datetime.fromisoformat(row["completed_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds() * 1000
        for row in jobs if row["started_at"] and row["completed_at"]
    ]
    measured_input = sum(row["input_tokens"] or 0 for row in llm)
    measured_output = sum(row["output_tokens"] or 0 for row in llm)
    throughputs = [row["throughput_tokens_per_second"] for row in llm if row["throughput_tokens_per_second"] is not None]
    metrics.update({
        "processing_time_ms_per_article": sum(processing) / len(processing) if processing else None,
        "llm_calls_per_article": len(llm) / len(jobs),
        "input_tokens_per_article": measured_input / len(jobs) if any(row["input_tokens"] is not None for row in llm) else None,
        "output_tokens_per_article": measured_output / len(jobs) if any(row["output_tokens"] is not None for row in llm) else None,
        "latency_ms": {
            "average": sum(durations) / len(durations) if durations else None,
            "p95": sorted(durations)[min(len(durations) - 1, int((len(durations) - 1) * 0.95))] if durations else None,
        },
        "throughput_tokens_per_second": sum(throughputs) / len(throughputs) if throughputs else None,
        "failure_rate": sum(row["status"] == "FAILED" for row in jobs) / len(jobs),
        "retry_rate": sum(row["retry_count"] > 0 for row in jobs) / len(jobs),
    })
    stage_rows = connection.execute(
        f"""SELECT stage, COUNT(*) AS count, AVG(duration_ms) AS average_duration_ms,
                   SUM(duration_ms) AS total_duration_ms
            FROM telemetry_events WHERE event_type='pipeline_stage' AND status='completed'
            AND job_id IN ({placeholders}) GROUP BY stage ORDER BY MIN(timestamp), stage""",
        job_ids,
    ).fetchall()
    metrics["stages"] = [dict(row) for row in stage_rows]
    return metrics


def experiment_response(connection, row) -> ExperimentResponse:
    job_rows = connection.execute(
        "SELECT job_id FROM experiment_jobs WHERE experiment_id=? ORDER BY rowid",
        (row["experiment_id"],),
    ).fetchall()
    return ExperimentResponse(
        experiment_id=row["experiment_id"],
        description=row["description"],
        dataset_name=row["dataset_name"],
        article_ids=json.loads(row["article_ids_json"]),
        model_config_snapshot=json.loads(row["model_config_json"]),
        prompt_versions=json.loads(row["prompt_versions_json"]),
        pipeline_parameters=json.loads(row["pipeline_parameters_json"]),
        status=row["status"],
        created_at=parse_datetime(row["created_at"]),
        started_at=parse_datetime(row["started_at"]),
        completed_at=parse_datetime(row["completed_at"]),
        job_ids=[item["job_id"] for item in job_rows],
        metrics=_experiment_metrics(connection, row["experiment_id"]),
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


@app.get("/api/articles", tags=["Tasks"])
def available_articles(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        return client.articles(limit)
    except Exception as exc:
        logger.warning("article_listing_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Article storage is unavailable") from exc


@app.get("/api/knowledge/summary", response_model=KnowledgeSummaryResponse, tags=["Knowledge"])
def knowledge_summary() -> KnowledgeSummaryResponse:
    try:
        return KnowledgeSummaryResponse.model_validate(client.knowledge_summary())
    except Exception as exc:
        logger.warning("knowledge_summary_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Analytical storage is unavailable") from exc


@app.get("/api/knowledge/{resource}", tags=["Knowledge"])
def knowledge_records(
    resource: str,
    query: str | None = Query(default=None, min_length=1),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    try:
        if query:
            return client.search_knowledge(resource, query, limit)
        return client.knowledge(resource, limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning(
            "knowledge_records_failed resource=%s error_type=%s",
            resource,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="Analytical storage is unavailable") from exc


@app.get("/api/semantic-search/{resource}", tags=["Knowledge"])
def semantic_search(resource: str, query: str = Query(min_length=2), limit: int = Query(25, ge=1, le=100)) -> list[dict[str, Any]]:
    try:
        return client.semantic_search(resource, query, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("semantic_search_failed resource=%s error_type=%s", resource, type(exc).__name__)
        raise HTTPException(status_code=503, detail=f"Semantic search is unavailable: {exc}") from exc


@app.get("/api/data-options/{resource}/{field}", tags=["Data management"])
def data_options(resource: str, field: str, query: str = Query(default="", max_length=200)) -> list[str]:
    try:
        return client.browse_options(resource, field, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("data_options_failed resource=%s field=%s error_type=%s", resource, field, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Analytical storage is unavailable") from exc


def _record_identifier(resource: str, record: dict[str, Any]) -> str | None:
    if resource == "attitudes":
        topic = record.get("topic_uuid")
        citation = record.get("citation_uuid")
        return f"{topic}/{citation}" if topic and citation else None
    fields = {
        "articles": "article_id",
        "people": "person_uuid",
        "citations": "citation_uuid",
        "topics": "topic_uuid",
        "inconsistencies": "inconsistency_uuid",
    }
    value = record.get(fields.get(resource, ""))
    return str(value) if value else None


def _knowledge_detail(resource: str, identifier: str) -> dict[str, Any]:
    record = client.knowledge_detail(resource, identifier)
    if resource == "articles":
        record["citations"] = [
            item for item in client.knowledge("citations", 500)
            if str(item.get("origin_article_id")) == identifier
        ]
    elif resource == "citations":
        article_id = record.get("origin_article_id")
        person_id = record.get("person_uuid")
        record["article"] = client.knowledge_detail("articles", str(article_id)) if article_id else None
        record["person"] = client.knowledge_detail("people", str(person_id)) if person_id else None
        record["attitudes"] = [
            item for item in client.knowledge("attitudes", 500)
            if str(item.get("citation_uuid")) == identifier
        ]
    elif resource == "topics":
        attitudes = [
            item for item in client.knowledge("attitudes", 500)
            if str(item.get("topic_uuid")) == identifier
        ]
        citations = {str(item.get("citation_uuid")): item for item in client.knowledge("citations", 500)}
        people = {str(item.get("person_uuid")): item for item in client.knowledge("people", 500)}
        record["attitudes"] = attitudes
        record["citations"] = [citations[str(item["citation_uuid"])] for item in attitudes if str(item.get("citation_uuid")) in citations]
        record["people"] = [
            people[str(citations[str(item["citation_uuid"])].get("person_uuid"))]
            for item in attitudes
            if str(item.get("citation_uuid")) in citations
            and str(citations[str(item["citation_uuid"])].get("person_uuid")) in people
        ]
        record["articles"] = [
            article for article in client.knowledge("articles", 500)
            if str(article.get("article_id")) in {
                str(citation.get("origin_article_id")) for citation in record["citations"]
            }
        ]
    elif resource == "people":
        citations = [
            item for item in client.knowledge("citations", 500)
            if str(item.get("person_uuid")) == identifier
        ]
        record["citations"] = citations
        citation_ids = {str(item.get("citation_uuid")) for item in citations}
        record["attitudes"] = [
            item for item in client.knowledge("attitudes", 500)
            if str(item.get("citation_uuid")) in citation_ids
        ]
    elif resource == "attitudes":
        citation_id = identifier.split("/", 1)[1]
        record["citation"] = client.knowledge_detail("citations", citation_id)
        person_id = record["citation"].get("person_uuid")
        record["person"] = client.knowledge_detail("people", str(person_id)) if person_id else None
        record["topic"] = client.knowledge_detail("topics", identifier.split("/", 1)[0])
    elif resource == "inconsistencies":
        for suffix, field in (("a", "citation_a_uuid"), ("b", "citation_b_uuid")):
            citation_id = record.get(field)
            record[f"citation_{suffix}"] = client.knowledge_detail("citations", str(citation_id)) if citation_id else None
    return record


@app.get("/api/data/{resource}", tags=["Data management"])
def data_records(
    resource: str,
    query: str | None = Query(default=None, min_length=1),
    semantic_query: str | None = Query(default=None, min_length=2),
    status: str | None = Query(default=None, min_length=1),
    category: str | None = Query(default=None, min_length=1),
    category_value: str | None = Query(default=None, min_length=1),
    person_uuid: str | None = Query(default=None, min_length=1),
    topic_uuid: str | None = Query(default=None, min_length=1),
    person_query: str | None = Query(default=None, min_length=1),
    topic_query: str | None = Query(default=None, min_length=1),
    sort_by: str | None = Query(default=None, min_length=1),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    min_similarity: float = Query(0.0, ge=-1, le=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    try:
        if resource in {"attitudes", "inconsistencies"}:
            browse = client.browse_knowledge(resource, 500, 0, {
                key: value for key, value in (
                    ("status", category_value if category == "status" else (status if resource in {"attitudes", "inconsistencies"} else None)),
                    ("stance", category_value if resource == "attitudes" and category == "stance" else None),
                    ("classification", category_value if resource == "inconsistencies" and category == "classification" else None),
                    ("person_uuid", person_uuid),
                    ("topic_uuid", topic_uuid),
                    ("person_query", person_query),
                    ("topic_query", topic_query),
                    ("sort_by", sort_by),
                    ("sort_dir", sort_dir if sort_by else None),
                ) if value
            })
            records = browse["items"]
            total_from_storage = browse["total"]
            stance_counts = browse.get("stance_counts", {})
        elif semantic_query:
            records = client.semantic_search(resource, semantic_query, 100, min_similarity)
            total_from_storage = len(records)
            stance_counts = {}
        else:
            records = client.search_knowledge(resource, query, 500) if query else client.knowledge(resource, 500)
            total_from_storage = len(records)
            stance_counts = {}
        if query:
            needle = query.casefold()
            records = [item for item in records if needle in json.dumps(item, default=str).casefold()]
        if status and resource not in {"attitudes", "inconsistencies"}:
            records = [item for item in records if str(item.get("status", "")).casefold() == status.casefold()]
        if category and category_value and resource not in {"attitudes", "inconsistencies"}:
            records = [item for item in records if str(item.get(category, "")).casefold() == category_value.casefold()]
        total = len(records) if query or semantic_query or (
            resource not in {"attitudes", "inconsistencies"} and (category or status)
        ) else total_from_storage
        start = (page - 1) * page_size
        return {
            "items": records[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "stance_counts": stance_counts,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("data_records_failed resource=%s error_type=%s", resource, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Analytical storage is unavailable") from exc


@app.post("/api/data/database/{action}", tags=["Data management"])
def data_database_action(action: str) -> dict[str, Any]:
    try:
        return client.database_action(action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("database_action_failed action=%s error_type=%s", action, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Database operation failed") from exc


@app.post("/api/data/import-temp", tags=["Data management"])
def data_import_temp() -> dict[str, Any]:
    try:
        return client.import_temp_articles()
    except Exception as exc:
        logger.warning("temp_import_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Temporary article import failed") from exc


@app.get("/api/data/{resource}/{identifier:path}", tags=["Data management"])
def data_record_detail(resource: str, identifier: str) -> dict[str, Any]:
    try:
        return _knowledge_detail(resource, identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("data_detail_failed resource=%s error_type=%s", resource, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Analytical storage is unavailable") from exc


@app.put("/api/data/{resource}/{identifier:path}", tags=["Data management"])
def update_data_record(
    resource: str,
    identifier: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    try:
        return client.mutate_knowledge(resource, identifier, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("data_update_failed resource=%s error_type=%s", resource, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not update analytical record") from exc


@app.delete("/api/data/{resource}/{identifier:path}", status_code=204, tags=["Data management"])
def delete_data_record(resource: str, identifier: str) -> Response:
    try:
        client.delete_knowledge(resource, identifier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("data_delete_failed resource=%s error_type=%s", resource, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not delete analytical record") from exc
    return Response(status_code=204)


@app.post("/api/tasks", response_model=TaskResponse, status_code=202, tags=["Tasks"])
def create_task(request: TaskCreateRequest) -> TaskResponse:
    try:
        return coordinator.create_task(request)
    except Exception as exc:
        logger.exception("task_creation_failed")
        raise HTTPException(status_code=502, detail="Could not create processing task") from exc


@app.post("/api/tasks/fresh", response_model=TaskResponse, status_code=202, tags=["Tasks"])
def create_fresh_task(
    name: str = Query("Fresh articles", min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
) -> TaskResponse:
    try:
        articles = client.articles(limit)
        with database.connection() as connection:
            existing = {
                row["article_id"] for row in connection.execute(
                    "SELECT DISTINCT article_id FROM jobs WHERE status IN ('PENDING','RUNNING','RETRYING','COMPLETED')"
                ).fetchall()
            }
        article_ids = [item["article_id"] for item in articles if item.get("article_id") and item["article_id"] not in existing]
        if not article_ids:
            raise HTTPException(status_code=409, detail="No unprocessed articles are available")
        return coordinator.create_task(TaskCreateRequest(name=name, article_ids=article_ids))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("fresh_task_creation_failed")
        raise HTTPException(status_code=503, detail="Could not discover fresh articles") from exc


@app.get("/api/tasks", response_model=list[TaskResponse], tags=["Tasks"])
def list_tasks(limit: int = Query(50, ge=1, le=200)) -> list[TaskResponse]:
    return [TaskResponse.model_validate(item) for item in coordinator.list_tasks(limit)]


@app.get("/api/tasks/{task_id}", response_model=TaskResponse, tags=["Tasks"])
def get_task(task_id: UUID) -> TaskResponse:
    try:
        return TaskResponse.model_validate(coordinator.task(str(task_id)))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


@app.get("/api/tasks/{task_id}/analytics", tags=["Tasks"])
def task_analytics(task_id: UUID) -> dict[str, Any]:
    with database.connection() as connection:
        task = connection.execute("SELECT task_id FROM tasks WHERE task_id=?", (str(task_id),)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        jobs = connection.execute("SELECT job_id FROM task_jobs WHERE task_id=?", (str(task_id),)).fetchall()
        job_ids = [row["job_id"] for row in jobs]
        if not job_ids:
            return {"stages": [], "llm": {}, "totals": {}}
        placeholders = ",".join("?" for _ in job_ids)
        rows = connection.execute(
            f"SELECT * FROM telemetry_events WHERE job_id IN ({placeholders}) ORDER BY timestamp",
            job_ids,
        ).fetchall()
    stage_map: dict[str, dict[str, Any]] = {}
    totals = {"events": len(rows), "citations": 0, "topics": 0, "people": 0, "attitudes": 0, "created_records": 0, "retrieved_records": 0}
    llm_calls = 0
    output_tokens = 0
    input_tokens = 0
    for row in rows:
        attributes = json.loads(row["attributes_json"] or "{}")
        if row["event_type"] == "llm_call":
            llm_calls += 1
            input_tokens += row["input_tokens"] or row["input_tokens_estimated"] or 0
            output_tokens += row["output_tokens"] or row["output_tokens_estimated"] or 0
        if row["event_type"] != "pipeline_stage":
            continue
        stage_name = row["stage"] or "Unspecified"
        stage = stage_map.setdefault(stage_name, {"stage": stage_name, "events": 0, "duration_ms": 0, "citations": 0, "topics": 0, "people": 0, "attitudes": 0, "created_records": 0, "retrieved_records": 0, "completed": 0})
        stage["events"] += 1
        stage["duration_ms"] += row["duration_ms"] or 0
        stage["completed"] += row["status"] == "completed"
        for key in ("citation_count", "topic_count", "speaker_count", "attitude_count"):
            output_key = {"citation_count": "citations", "topic_count": "topics", "speaker_count": "people", "attitude_count": "attitudes"}[key]
            value = attributes.get(key, 0) or 0
            stage[output_key] += value
            totals[output_key] += value
        for key in ("created_records", "created_new", "retrieved_records", "retrieved_from_db"):
            value = attributes.get(key, 0) or 0
            target = "created_records" if key in {"created_records", "created_new"} else "retrieved_records"
            stage[target] += value
            totals[target] += value
    return {"stages": list(stage_map.values()), "llm": {"calls": llm_calls, "input_tokens": input_tokens, "output_tokens": output_tokens, "tok_per_second": output_tokens / max(sum(item["duration_ms"] for item in stage_map.values()) / 1000, 1)}, "totals": totals}


@app.post("/api/tasks/{task_id}/cancel", response_model=TaskResponse, tags=["Tasks"])
def cancel_task(task_id: UUID) -> TaskResponse:
    try:
        return TaskResponse.model_validate(coordinator.cancel_task(str(task_id)))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


@app.delete("/api/tasks/{task_id}", status_code=204, tags=["Tasks"])
def delete_task(task_id: UUID) -> Response:
    try:
        coordinator.delete_task(str(task_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


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


@app.delete("/api/jobs/{job_id}", status_code=204, tags=["Jobs"])
def delete_job(job_id: UUID) -> Response:
    try:
        coordinator.delete(str(job_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


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


def _safe_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe_snapshot(item)
            for key, item in value.items()
            if not any(term in key.casefold() for term in ("secret", "password", "api_key", "apikey", "credential"))
        }
    if isinstance(value, list):
        return [_safe_snapshot(item) for item in value]
    return value


def _refresh_experiment_status(connection, experiment_id: str) -> None:
    rows = connection.execute(
        """SELECT j.status FROM jobs j JOIN experiment_jobs ej ON ej.job_id=j.job_id
           WHERE ej.experiment_id=?""", (experiment_id,)
    ).fetchall()
    if not rows or any(row["status"] in {"PENDING", "RUNNING", "RETRYING"} for row in rows):
        return
    status = "FAILED" if any(row["status"] == "FAILED" for row in rows) else "COMPLETED"
    connection.execute(
        "UPDATE experiments SET status=?, completed_at=COALESCE(completed_at,?) WHERE experiment_id=?",
        (status, datetime.now(timezone.utc).isoformat(), experiment_id),
    )


@app.post("/api/experiments", response_model=ExperimentResponse, status_code=201, tags=["Experiments"])
def create_experiment(request: ExperimentCreateRequest) -> ExperimentResponse:
    experiment_id = str(uuid4())
    created = datetime.now(timezone.utc).isoformat()
    with database.lock, database.connection() as connection:
        connection.execute(
            """INSERT INTO experiments
               (experiment_id,description,dataset_name,article_ids_json,model_config_json,
                prompt_versions_json,pipeline_parameters_json,status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                experiment_id, request.description, request.dataset_name,
                json.dumps([str(item) for item in request.article_ids]),
                json.dumps(_safe_snapshot(request.model_config_snapshot)),
                json.dumps(_safe_snapshot(request.prompt_versions)),
                json.dumps(_safe_snapshot(request.pipeline_parameters)),
                ExperimentStatus.CREATED.value, created,
            ),
        )
        row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        return experiment_response(connection, row)


@app.get("/api/experiments", response_model=list[ExperimentResponse], tags=["Experiments"])
def list_experiments(limit: int = Query(50, ge=1, le=200)) -> list[ExperimentResponse]:
    with database.lock, database.connection() as connection:
        rows = connection.execute("SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        for row in rows:
            _refresh_experiment_status(connection, row["experiment_id"])
        return [experiment_response(connection, connection.execute(
            "SELECT * FROM experiments WHERE experiment_id=?", (row["experiment_id"],)
        ).fetchone()) for row in rows]


@app.get("/api/experiments/{experiment_id}", response_model=ExperimentResponse, tags=["Experiments"])
def get_experiment(experiment_id: UUID) -> ExperimentResponse:
    with database.lock, database.connection() as connection:
        _refresh_experiment_status(connection, str(experiment_id))
        row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (str(experiment_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return experiment_response(connection, row)


@app.post("/api/experiments/{experiment_id}/start", response_model=ExperimentResponse, status_code=202, tags=["Experiments"])
def start_experiment(experiment_id: UUID) -> ExperimentResponse:
    experiment_key = str(experiment_id)
    with database.lock, database.connection() as connection:
        row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_key,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if row["status"] not in {"CREATED", "FAILED"}:
            raise HTTPException(status_code=409, detail="Experiment has already started")
        config = json.loads(row["pipeline_parameters_json"])
        article_ids = json.loads(row["article_ids_json"])
        connection.execute(
            "UPDATE experiments SET status=?, started_at=?, completed_at=NULL WHERE experiment_id=?",
            (ExperimentStatus.RUNNING.value, datetime.now(timezone.utc).isoformat(), experiment_key),
        )
    for article_id in article_ids:
        try:
            request = JobCreateRequest(
                article_id=article_id,
                inconsistency_window_days=config.get("inconsistency_window_days", 365),
            )
            job = coordinator.create(request, f"experiment:{experiment_key}:article:{article_id}")
            with database.lock, database.connection() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO experiment_jobs(experiment_id,job_id,article_id) VALUES(?,?,?)",
                    (experiment_key, job["job_id"], article_id),
                )
                metadata = dict(job.get("metadata") or {})
                metadata.update({"experiment_id": experiment_key, "experiment_description": row["description"]})
                connection.execute("UPDATE jobs SET metadata_json=? WHERE job_id=?", (json.dumps(metadata), job["job_id"]))
        except Exception:
            logger.exception("experiment_job_create_failed experiment_id=%s article_id=%s", experiment_key, article_id)
            with database.lock, database.connection() as connection:
                connection.execute("UPDATE experiments SET status=? WHERE experiment_id=?", (ExperimentStatus.FAILED.value, experiment_key))
    return get_experiment(experiment_id)


@app.post("/api/experiments/compare", tags=["Experiments"])
def compare_experiments(request: ExperimentComparisonRequest) -> dict[str, Any]:
    with database.lock, database.connection() as connection:
        result = []
        for experiment_id in request.experiment_ids:
            row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (str(experiment_id),)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Experiment not found: {experiment_id}")
            _refresh_experiment_status(connection, str(experiment_id))
            refreshed = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (str(experiment_id),)).fetchone()
            item = experiment_response(connection, refreshed).model_dump(mode="json")
            result.append({
                "experiment_id": item["experiment_id"],
                "description": item["description"],
                "status": item["status"],
                "metrics": item["metrics"],
            })
        return {"experiments": result, "quality_metrics_note": "Quality metrics are included only when ground-truth evaluation data exists."}


@app.post("/api/experiments/{experiment_id}/evaluation", response_model=ExperimentResponse, tags=["Experiments"])
def add_evaluation(experiment_id: UUID, request: EvaluationCreateRequest) -> ExperimentResponse:
    if request.experiment_id != experiment_id:
        raise HTTPException(status_code=400, detail="Path and body experiment IDs differ")
    with database.lock, database.connection() as connection:
        if connection.execute("SELECT 1 FROM experiments WHERE experiment_id=?", (str(experiment_id),)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        connection.execute(
            """INSERT INTO experiment_evaluations
               (evaluation_id,experiment_id,evaluated_items,true_positives,false_positives,false_negatives,created_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(experiment_id) DO UPDATE SET
               evaluated_items=excluded.evaluated_items,true_positives=excluded.true_positives,
               false_positives=excluded.false_positives,false_negatives=excluded.false_negatives,
               created_at=excluded.created_at""",
            (str(uuid4()), str(experiment_id), request.evaluated_items, request.true_positives,
             request.false_positives, request.false_negatives, datetime.now(timezone.utc).isoformat()),
        )
        row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (str(experiment_id),)).fetchone()
        return experiment_response(connection, row)


@app.get("/api/experiments/{experiment_id}/export", tags=["Experiments"])
def export_experiment(experiment_id: UUID, format: str = Query("json", pattern="^(json|csv)$")):
    experiment = get_experiment(experiment_id).model_dump(mode="json")
    if format == "json":
        return experiment
    metrics = experiment["metrics"]
    fields = ["experiment_id", "description", "status", "article_count", "completed_articles",
              "failed_articles", "processing_time_ms_per_article", "llm_calls_per_article",
              "input_tokens_per_article", "output_tokens_per_article", "failure_rate", "retry_rate"]
    values = [experiment["experiment_id"], experiment["description"], experiment["status"],
              *[metrics.get(field) for field in fields[3:]]]
    import csv
    import io
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(fields)
    writer.writerow(values)
    return Response(content=buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="experiment-{experiment_id}.csv"'})


@app.post("/internal/events", status_code=202, tags=["Telemetry"])
def receive_event(event: EventRequest) -> dict[str, str]:
    event_id = str(event.event_id or uuid4())
    with database.lock, database.connection() as connection:
        retention_cutoff = datetime.now(timezone.utc).timestamp() - settings.telemetry_retention_days * 86400
        connection.execute(
            "DELETE FROM telemetry_events WHERE strftime('%s', timestamp) < ?",
            (int(retention_cutoff),),
        )
        connection.execute(
            "DELETE FROM events WHERE strftime('%s', timestamp) < ?",
            (int(retention_cutoff),),
        )
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
    article_id: UUID | None = None,
    stage: str | None = None,
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
    if article_id:
        query += " AND article_id=?"; params.append(str(article_id))
    if stage:
        query += " AND stage=?"; params.append(stage)
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


@app.delete("/api/logs", status_code=204, tags=["Diagnostics"])
def delete_logs(
    service: str | None = None,
    job_id: UUID | None = None,
    article_id: UUID | None = None,
    stage: str | None = None,
    trace_id: UUID | None = None,
) -> Response:
    clauses = ["1=1"]
    params: list[Any] = []
    for column, value in (
        ("service", service),
        ("job_id", str(job_id) if job_id else None),
        ("article_id", str(article_id) if article_id else None),
        ("stage", stage),
        ("trace_id", str(trace_id) if trace_id else None),
    ):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    with database.lock, database.connection() as connection:
        matching = connection.execute(
            f"SELECT event_id FROM telemetry_events WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall()
        event_ids = [row["event_id"] for row in matching]
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            connection.execute(
                f"DELETE FROM telemetry_events WHERE event_id IN ({placeholders})",
                event_ids,
            )
            connection.execute(
                f"DELETE FROM events WHERE event_id IN ({placeholders})",
                event_ids,
            )
    return Response(status_code=204)


@app.get("/api/metrics/llm", tags=["Telemetry"])
def llm_metrics(
    since: datetime | None = None,
    until: datetime | None = None,
    model: str | None = None,
    job_id: UUID | None = None,
    article_id: UUID | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    query = "SELECT * FROM telemetry_events WHERE event_type='llm_call' AND status IN ('completed','failed')"
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
        active_query = (
            "SELECT sequence FROM telemetry_events "
            "WHERE event_type='llm_call' AND status='started' "
            "AND NOT EXISTS (SELECT 1 FROM telemetry_events finished "
            "WHERE finished.event_type='llm_call' "
            "AND finished.span_id=telemetry_events.span_id "
            "AND finished.status IN ('completed','failed')) "
            "AND (job_id IS NULL OR job_id IN "
            "(SELECT job_id FROM jobs WHERE status='RUNNING'))"
        )
        active_params: list[Any] = []
        if since:
            active_query += " AND timestamp>=?"; active_params.append(since.isoformat())
        if until:
            active_query += " AND timestamp<=?"; active_params.append(until.isoformat())
        for column, value in (("model", model), ("job_id", str(job_id) if job_id else None),
                              ("article_id", str(article_id) if article_id else None), ("stage", stage)):
            if value:
                active_query += f" AND {column}=?"; active_params.append(value)
        active_rows = list(connection.execute(active_query, active_params))
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
        "active_call_count": len(active_rows),
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
               GROUP BY stage ORDER BY MIN(timestamp), stage LIMIT ?""", (limit,)
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
def event_stream(
    last_event_id: int | None = Query(default=None, ge=0),
    last_event_header: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
):
    start = last_event_id if last_event_id is not None else (last_event_header or 0)
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
