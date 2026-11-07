from __future__ import annotations

import json
import math
import os
import re
import time
import threading
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import Float, String, cast, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.database import (
    DimArticle,
    DimCitation,
    DimPerson,
    DimTopic,
    FctAttitude,
    FctInconsistency,
    check_database,
    create_database,
    engine,
    get_session,
    recreate_database,
)
from app.database.DBConnector import EMBEDDING_DIMENSION
from app.fileread import read_temp_articles
from app.schemas import (
    ArticleBulkUpdate,
    ArticlePayload,
    ArticleUpdate,
    AttitudeBulkUpdate,
    AttitudeKey,
    AttitudePayload,
    AttitudeUpdate,
    InconsistencyPayload,
    BulkAttitudeDeleteRequest,
    BulkDeleteRequest,
    CitationBulkUpdate,
    CitationPayload,
    CitationUpdate,
    EmbeddingBatchRequest,
    EmbeddingRequest,
    PersonBulkUpdate,
    PersonPayload,
    PersonUpdate,
    TopicBulkUpdate,
    TopicEmbeddingsRequest,
    TopicPayload,
    TopicUpdate,
    parse_datetime,
)
from app.logger import get_logger
from app.telemetry import emit_event, shutdown_telemetry, telemetry_metrics

logger = get_logger("main", "data_storage")
app = FastAPI(title="NewsAnalysis-DataStorage", version="1.0.0")


@app.on_event("shutdown")
def stop_telemetry() -> None:
    shutdown_telemetry()


@app.get("/telemetry/metrics", tags=["Diagnostics"])
def telemetry_metrics_endpoint() -> dict[str, int]:
    return telemetry_metrics()
_embedding_client = OpenAI(
    base_url=os.getenv("EMBEDDING_URL", "http://host.docker.internal:1234/v1"),
    api_key=os.getenv("EMBEDDING_API_KEY", "fake_api_key"),
    timeout=float(os.getenv("EMBEDDING_REQUEST_TIMEOUT_SECONDS", "1800")),
)
_embedding_model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-embeddinggemma-300m")
_default_min_cosine_similarity = float(os.getenv("EMBEDDING_MIN_COSINE_SIMILARITY", "0.0"))
if not -1 <= _default_min_cosine_similarity <= 1:
    raise ValueError("EMBEDDING_MIN_COSINE_SIMILARITY must be between -1 and 1")
_active_requests = 0
_active_requests_lock = threading.Lock()


@app.middleware("http")
async def log_api_request(request: Request, call_next) -> Response:
    request_id = request.headers.get("x-request-id", str(uuid4()))
    job_id = request.headers.get("x-job-id")
    correlation_id = request.headers.get("x-correlation-id", request_id)
    attempt_number = request.headers.get("x-job-attempt")
    retry_count = request.headers.get("x-job-retry-count")
    queue_wait = request.headers.get("x-queue-wait-ms")
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "api_request status=started request_id=%s job_id=%s correlation_id=%s method=%s path=%s query=%s",
        request_id, job_id or "-", correlation_id,
        request.method,
        request.url.path,
        request.url.query or "-",
    )
    global _active_requests
    with _active_requests_lock:
        _active_requests += 1
        active_requests = _active_requests
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "api_request status=failed request_id=%s job_id=%s correlation_id=%s method=%s path=%s duration=%.3fs",
            request_id, job_id or "-", correlation_id,
            request.method,
            request.url.path,
            time.perf_counter() - started,
        )
        raise
    finally:
        with _active_requests_lock:
            _active_requests = max(0, _active_requests - 1)
    duration = time.perf_counter() - started
    level = logger.info if response.status_code < 400 else logger.warning
    level(
        "api_request status=completed request_id=%s job_id=%s correlation_id=%s method=%s path=%s "
        "status_code=%d response_bytes=%s duration=%.3fs",
        request_id, job_id or "-", correlation_id,
        request.method,
        request.url.path,
        response.status_code,
        response.headers.get("content-length", "unknown"),
        duration,
    )
    emit_event(
        "api_request_completed",
        job_id=job_id,
        correlation_id=correlation_id,
        request_id=request_id,
        duration_ms=int(duration * 1000),
        started_at=started_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
        attempt_number=int(attempt_number) if attempt_number and attempt_number.isdigit() else None,
        retry_count=int(retry_count) if retry_count and retry_count.isdigit() else 0,
        queue_wait_ms=int(queue_wait) if queue_wait and queue_wait.isdigit() else None,
        attributes={
            "path": request.url.path,
            "status_code": response.status_code,
            "worker_identity": f"pid={os.getpid()} thread={threading.current_thread().name}",
            "process_id": os.getpid(),
            "thread_id": threading.get_ident(),
            "active_requests": active_requests,
            "configured_workers": int(os.getenv("STORAGE_WORKERS", "1")),
        },
    )
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    request_id = request.headers.get("x-request-id", str(uuid4()))
    logger.exception(
        "api_request status=unhandled_error request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
            "error_type": type(exc).__name__,
        },
        headers={"x-request-id": request_id},
    )


@app.on_event("startup")
def initialize_database() -> None:
    logger.info("database_startup status=started")
    started = time.perf_counter()
    create_database()
    logger.info("database_startup status=completed duration=%.3fs", time.perf_counter() - started)


def _validate_vector(values: list[float] | None) -> None:
    if values is not None and len(values) != EMBEDDING_DIMENSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Embedding must contain exactly {EMBEDDING_DIMENSION} values",
        )


def _affiliation_text(affiliation: Any) -> str:
    if affiliation is None:
        return ""
    if isinstance(affiliation, str):
        return affiliation.strip()
    return json.dumps(affiliation, ensure_ascii=True, sort_keys=True)


def _normalize_aliases(aliases: list[str] | None) -> list[str]:
    if aliases is None:
        raw_aliases: list[Any] = []
    elif isinstance(aliases, list):
        raw_aliases = aliases
    else:
        raise HTTPException(status_code=422, detail="aliases must be a list")

    normalized: list[str] = []
    for item in raw_aliases:
        if not isinstance(item, str):
            raise HTTPException(status_code=422, detail="Each alias must be a string")
        alias = item.strip()
        if not alias:
            continue
        if alias not in normalized:
            normalized.append(alias)
    return normalized


def _person_values(values: dict[str, Any]) -> dict[str, Any]:
    values = dict(values)
    values["aliases"] = _normalize_aliases(values.get("aliases"))
    alias_text = _person_embedding_text(
        values["aliases"], values.get("affiliation"), values.get("name")
    )
    values["aliases_vector"] = (
        _embed_texts_deduplicated([alias_text])[0] if alias_text else None
    )
    return values


def _person_embedding_text(
    aliases: list[str], affiliation: Any, name: str | None = None
) -> str:
    alias_text = "\n".join(filter(None, [name, *aliases]))
    return f"{alias_text}\n{_affiliation_text(affiliation)}".strip()


def _with_generated_vector(
    values: dict[str, Any],
    text_field: str,
    vector_field: str,
) -> dict[str, Any]:
    values = dict(values)
    if text_field in values:
        text = values[text_field]
        values[vector_field] = (
            _embed_texts_deduplicated([text])[0] if text and text.strip() else None
        )
    return values


def _as_dict(item: Any) -> dict[str, Any]:
    return {column.name: getattr(item, column.name) for column in item.__table__.columns}


def _stance_polarity(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return -1.0 if numeric < 0 else 1.0 if numeric > 0 else 0.0


def _get(model: Any, key: Any) -> dict[str, Any]:
    started = time.perf_counter()
    with get_session() as session:
        item = session.get(model, key)
        if item is None:
            raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
        session.expunge(item)
        result = _as_dict(item)
    logger.debug("db_read operation=get model=%s found=true duration=%.3fs", model.__name__, time.perf_counter() - started)
    return result


def _list(model: Any, limit: int) -> list[dict[str, Any]]:
    started = time.perf_counter()
    with get_session() as session:
        result = [_as_dict(item) for item in session.scalars(select(model).limit(limit)).all()]
    logger.debug("db_read operation=list model=%s limit=%d rows=%d duration=%.3fs", model.__name__, limit, len(result), time.perf_counter() - started)
    return result


def _create(model: Any, values: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with get_session() as session:
            item = model(**values)
            session.add(item)
            session.flush()
            session.refresh(item)
            result = _as_dict(item)
        logger.info("db_write operation=create model=%s status=completed duration=%.3fs", model.__name__, time.perf_counter() - started)
        return result
    except IntegrityError as exc:
        logger.exception("Failed to create %s", model.__name__)
        raise HTTPException(status_code=409, detail="Resource conflicts with an existing record") from exc


def _update(model: Any, key: Any, values: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with get_session() as session:
            item = session.get(model, key)
            if item is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            for field, value in values.items():
                setattr(item, field, value)
            session.flush()
            session.refresh(item)
            result = _as_dict(item)
        logger.info("db_write operation=update model=%s status=completed fields=%d duration=%.3fs", model.__name__, len(values), time.perf_counter() - started)
        return result
    except IntegrityError as exc:
        logger.exception("Failed to update %s", model.__name__)
        raise HTTPException(status_code=409, detail="Resource conflicts with an existing record") from exc


def _delete(model: Any, key: Any) -> None:
    started = time.perf_counter()
    with get_session() as session:
        item = session.get(model, key)
        if item is not None:
            session.delete(item)
    logger.info("db_write operation=delete model=%s status=completed duration=%.3fs", model.__name__, time.perf_counter() - started)


def _bulk_create(model: Any, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    started = time.perf_counter()
    try:
        with get_session() as session:
            items = [model(**item_values) for item_values in values]
            session.add_all(items)
            session.flush()
            result = [_as_dict(item) for item in items]
        logger.info("db_write operation=bulk_create model=%s rows=%d duration=%.3fs", model.__name__, len(result), time.perf_counter() - started)
        return result
    except IntegrityError as exc:
        logger.exception("Failed to create %s records in bulk", model.__name__)
        raise HTTPException(status_code=409, detail="Bulk request conflicts with existing records") from exc


def _bulk_update(model: Any, key_field: str, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    started = time.perf_counter()
    with get_session() as session:
        result = []
        for item_values in values:
            key = item_values.pop(key_field)
            item = session.get(model, key)
            if item is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} {key} not found")
            for field, value in item_values.items():
                setattr(item, field, value)
            result.append(item)
        session.flush()
        response = [_as_dict(item) for item in result]
    logger.info("db_write operation=bulk_update model=%s rows=%d duration=%.3fs", model.__name__, len(response), time.perf_counter() - started)
    return response


def _bulk_delete(model: Any, key_field: str, keys: list[Any]) -> dict[str, int]:
    started = time.perf_counter()
    with get_session() as session:
        result = session.execute(delete(model).where(getattr(model, key_field).in_(keys)))
        response = {"deleted": result.rowcount or 0}
    logger.info("db_write operation=bulk_delete model=%s requested=%d deleted=%d duration=%.3fs", model.__name__, len(keys), response["deleted"], time.perf_counter() - started)
    return response


def _bulk_delete_attitudes(keys: list[AttitudeKey]) -> dict[str, int]:
    with get_session() as session:
        conditions = [
            (FctAttitude.topic_uuid == key.topic_uuid) & (FctAttitude.citation_uuid == key.citation_uuid)
            for key in keys
        ]
        result = session.execute(delete(FctAttitude).where(or_(*conditions)))
        return {"deleted": result.rowcount or 0}


def _set_bulk_stats(response: Response, created_new: int, retrieved_from_db: int) -> None:
    response.headers["x-db-created-new"] = str(created_new)
    response.headers["x-db-retrieved-from-db"] = str(retrieved_from_db)


@app.get("/health", tags=["System"], summary="Check API and database health")
def health() -> dict[str, str]:
    try:
        database = "ok" if check_database() else "error"
    except Exception:
        logger.exception("Database health check failed")
        database = "unavailable"
    return {"message": "Data Storage API Server is running", "version": app.version, "db_status": database}


@app.get(
    "/summary",
    response_model=None,
    tags=["System"],
    summary="Summarize persisted analytical entities",
)
def storage_summary() -> dict[str, Any]:
    """Return bounded database counts for management and operational dashboards."""
    models = {
        "articles": DimArticle,
        "people": DimPerson,
        "citations": DimCitation,
        "topics": DimTopic,
        "attitudes": FctAttitude,
        "inconsistencies": FctInconsistency,
    }
    try:
        with get_session() as session:
            counts = {
                name: session.scalar(select(func.count()).select_from(model)) or 0
                for name, model in models.items()
            }
    except Exception as exc:
        logger.exception("Failed to summarize stored analytical entities")
        raise HTTPException(status_code=503, detail="Database summary unavailable") from exc
    return {"entities": counts}


@app.get("/browse/options/{resource}/{field}", response_model=None, tags=["System"], summary="List distinct browse filter values")
def browse_options(resource: str, field: str, query: str | None = Query(default=None, min_length=1), limit: int = Query(100, ge=1, le=500)) -> list[str]:
    models = {
        "articles": DimArticle,
        "people": DimPerson,
        "citations": DimCitation,
        "topics": DimTopic,
        "attitudes": FctAttitude,
        "inconsistencies": FctInconsistency,
    }
    allowed = {
        "articles": {"status", "source"},
        "people": {"status", "person_type"},
        "citations": {"status", "citation_type"},
        "topics": {"status", "general_topic_field"},
        "attitudes": {"status", "stance"},
        "inconsistencies": {"status", "classification", "severity"},
    }
    if resource not in models or field not in allowed.get(resource, set()):
        raise HTTPException(status_code=400, detail="Unsupported browse option")
    column = getattr(models[resource], field)
    with get_session() as session:
        statement = select(column).where(column.is_not(None)).distinct().order_by(column).limit(limit)
        if query:
            statement = statement.where(column.ilike(f"%{query}%"))
        return [str(value) for value in session.scalars(statement).all()]


@app.post("/database/create", status_code=201, tags=["Database"], summary="Create missing database tables")
def create_database_endpoint() -> dict[str, str]:
    create_database()
    return {"status": "created", "database": "postgres-db", "embedding_dimension": str(EMBEDDING_DIMENSION)}


@app.post("/database/recreate", status_code=201, tags=["Database"], summary="Drop and recreate all database tables")
def recreate_database_endpoint() -> dict[str, str]:
    recreate_database()
    return {"status": "recreated", "database": "postgres-db", "embedding_dimension": str(EMBEDDING_DIMENSION)}


@app.post(
    "/articles/import-temp",
    status_code=200,
    tags=["Articles - Temporary"],
    summary="Import temporary article files",
    description="Temporary, idempotent import of JSON articles from the application's _data directory.",
)
def import_temp_articles() -> dict[str, int | str]:
    try:
        source_articles = read_temp_articles()
    except (OSError, ValueError) as exc:
        logger.exception("Failed to read temporary article files")
        raise HTTPException(status_code=500, detail="Failed to read temporary article files") from exc

    imported = 0
    skipped = 0
    try:
        with get_session() as session:
            for source_article in source_articles:
                payload = ArticlePayload.model_validate(source_article)
                existing = None
                if payload.article_id is not None:
                    existing = session.get(DimArticle, payload.article_id)
                if existing is None and payload.url:
                    existing = session.scalar(select(DimArticle).where(DimArticle.url == payload.url))
                if existing is not None:
                    skipped += 1
                    continue

                statement = (
                    pg_insert(DimArticle)
                    .values(payload.model_dump(exclude_none=True))
                    .on_conflict_do_nothing()
                )
                result = session.execute(statement)
                if result.rowcount:
                    imported += 1
                else:
                    skipped += 1
    except IntegrityError as exc:
        logger.exception("Failed to import temporary articles")
        raise HTTPException(status_code=409, detail="Temporary article import conflicts with existing records") from exc
    except ValidationError as exc:
        logger.exception("Failed to import temporary articles")
        raise HTTPException(status_code=422, detail="Temporary article data is invalid") from exc

    return {"status": "completed", "imported": imported, "skipped": skipped}


@app.post("/articles", status_code=201, response_model=None, tags=["Articles"], summary="Create an article")
def create_article(payload: ArticlePayload) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    with get_session() as session:
        article = session.get(DimArticle, payload.article_id) if payload.article_id else None
        if article is None and payload.url:
            article = session.scalar(select(DimArticle).where(DimArticle.url == payload.url))
        if article is None:
            article = DimArticle(**values)
            session.add(article)
        else:
            for field, value in values.items():
                if field != "article_id":
                    setattr(article, field, value)
        session.flush()
        return _as_dict(article)


@app.get("/articles", response_model=None, tags=["Articles"], summary="List articles")
def list_articles(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = Query(default=None, min_length=1),
) -> list[dict[str, Any]]:
    with get_session() as session:
        statement = select(DimArticle)
        if status:
            statement = statement.where(DimArticle.status == status.strip().upper())
        statement = statement.order_by(DimArticle.created_at.desc()).offset(offset).limit(limit)
        return [_as_dict(item) for item in session.scalars(statement).all()]


def _browse_compact(
    model: Any,
    query: str | None,
    limit: int,
    offset: int,
    status: str | None = None,
) -> dict[str, Any]:
    searchable = {
        DimArticle: (DimArticle.article_title, DimArticle.article_content),
        DimPerson: (DimPerson.name,),
        DimCitation: (DimCitation.exact_quote, DimCitation.summarized_quote),
    }.get(model)
    with get_session() as session:
        statement = select(model)
        count_statement = select(func.count()).select_from(model)
        if query and searchable:
            pattern = f"%{query.strip()}%"
            condition = or_(*(column.ilike(pattern) for column in searchable))
            statement = statement.where(condition)
            count_statement = count_statement.where(condition)
        if status and hasattr(model, "status"):
            condition = model.status == status.strip().upper()
            statement = statement.where(condition)
            count_statement = count_statement.where(condition)
        total = session.scalar(count_statement) or 0
        rows = session.scalars(
            statement.order_by(model.created_at.desc()).offset(offset).limit(limit)
        ).all()
        items = []
        for item in rows:
            value = _as_dict(item)
            value.pop("article_content", None)
            value.pop("aliases_vector", None)
            value.pop("summarized_quote_vector", None)
            items.append(value)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/articles/browse", response_model=None, tags=["Articles"], summary="Browse compact articles")
def browse_articles(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    query: str | None = Query(default=None, min_length=1),
    status: str | None = Query(default=None, min_length=1),
) -> dict[str, Any]:
    return _browse_compact(DimArticle, query, limit, offset, status)


@app.get("/articles/search", response_model=None, tags=["Articles"], summary="Search articles")
def search_articles(
    query: str = Query(min_length=1),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    with get_session() as session:
        statement = (
            select(DimArticle)
            .where(or_(DimArticle.article_title.ilike(pattern), DimArticle.article_content.ilike(pattern)))
            .limit(limit)
        )
        return [_as_dict(item) for item in session.scalars(statement).all()]


@app.get("/articles/{article_id:uuid}", response_model=None, tags=["Articles"], summary="Get an article")
def get_article(article_id: UUID) -> dict[str, Any]:
    return _get(DimArticle, article_id)


@app.put("/articles/{article_id:uuid}", response_model=None, tags=["Articles"], summary="Replace an article")
def update_article(article_id: UUID, payload: ArticleUpdate) -> dict[str, Any]:
    return _update(DimArticle, article_id, payload.model_dump(exclude_unset=True, exclude={"article_id"}))


@app.delete("/articles/bulk", tags=["Articles - Bulk"], summary="Delete articles in bulk")
def delete_articles_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimArticle, "article_id", payload.ids)


@app.delete("/articles/{article_id:uuid}", status_code=204, response_model=None, tags=["Articles"], summary="Delete an article")
def delete_article(article_id: UUID) -> None:
    _delete(DimArticle, article_id)


@app.post("/articles/bulk", status_code=201, response_model=None, tags=["Articles - Bulk"], summary="Create articles in bulk")
def create_articles_bulk(payload: list[ArticlePayload], response: Response) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    created_new = 0
    retrieved_from_db = 0
    try:
        with get_session() as session:
            for item in payload:
                values = item.model_dump(exclude_none=True)
                existing = None
                if item.article_id is not None:
                    existing = session.get(DimArticle, item.article_id)
                if existing is None and item.url:
                    existing = session.scalar(select(DimArticle).where(DimArticle.url == item.url))
                if existing is None:
                    existing = DimArticle(**values)
                    session.add(existing)
                    session.flush()
                    created_new += 1
                else:
                    retrieved_from_db += 1
                results.append(_as_dict(existing))
        _set_bulk_stats(response, created_new, retrieved_from_db)
        logger.info(
            "db_write operation=bulk_upsert model=DimArticle created_new=%d retrieved_from_db=%d",
            created_new,
            retrieved_from_db,
        )
        return results
    except IntegrityError as exc:
        logger.exception("Failed to create articles in bulk")
        raise HTTPException(status_code=409, detail="Bulk request conflicts with existing records") from exc


@app.patch("/articles/bulk", response_model=None, tags=["Articles - Bulk"], summary="Update articles in bulk")
def update_articles_bulk(payload: list[ArticleBulkUpdate]) -> list[dict[str, Any]]:
    return _bulk_update(DimArticle, "article_id", [item.model_dump(exclude_unset=True) for item in payload])


@app.post("/people", status_code=201, response_model=None, tags=["People"], summary="Create a person")
def create_person(payload: PersonPayload) -> dict[str, Any]:
    values = _person_values(payload.model_dump(exclude_none=True))
    with get_session() as session:
        person = session.get(DimPerson, payload.person_uuid) if payload.person_uuid else None
        if person is None:
            person = session.scalar(
                select(DimPerson).where(func.lower(DimPerson.name) == payload.name.strip().casefold())
            )
        if person is None:
            person = DimPerson(**values)
            session.add(person)
        else:
            for field, value in values.items():
                if field != "person_uuid":
                    setattr(person, field, value)
        session.flush()
        return _as_dict(person)


@app.get("/people", response_model=None, tags=["People"], summary="List people")
def list_people(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(DimPerson, limit)


@app.get("/people/browse", response_model=None, tags=["People"], summary="Browse compact people")
def browse_people(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    query: str | None = Query(default=None, min_length=1),
    status: str | None = Query(default=None, min_length=1),
) -> dict[str, Any]:
    return _browse_compact(DimPerson, query, limit, offset, status)


@app.get("/people/{person_uuid:uuid}/aliases", response_model=None, tags=["People"], summary="List person aliases")
def list_person_aliases(person_uuid: UUID) -> list[str]:
    with get_session() as session:
        person = session.get(DimPerson, person_uuid)
        if person is None:
            raise HTTPException(status_code=404, detail="DimPerson not found")
        return person.aliases or []


@app.post(
    "/people/{person_uuid:uuid}/aliases/reembed",
    response_model=None,
    tags=["People"],
    summary="Regenerate embeddings for a person's aliases",
)
def reembed_person_aliases(person_uuid: UUID) -> list[dict[str, Any]]:
    with get_session() as session:
        person = session.get(DimPerson, person_uuid)
        if person is None:
            raise HTTPException(status_code=404, detail="DimPerson not found")
        values = _person_values(
            {
                "name": person.name,
                "aliases": person.aliases,
                "affiliation": person.affiliation,
            }
        )
        person.aliases = values["aliases"]
        person.aliases_vector = values["aliases_vector"]
        session.flush()
        return [_as_dict(person)]


@app.get("/people/{person_uuid:uuid}", response_model=None, tags=["People"], summary="Get a person")
def get_person(person_uuid: UUID) -> dict[str, Any]:
    return _get(DimPerson, person_uuid)


@app.put("/people/{person_uuid:uuid}", response_model=None, tags=["People"], summary="Replace a person")
def update_person(person_uuid: UUID, payload: PersonUpdate) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    with get_session() as session:
        person = session.get(DimPerson, person_uuid)
        if person is None:
            raise HTTPException(status_code=404, detail="DimPerson not found")
        for field, value in values.items():
            setattr(person, field, value)
        refreshed = _person_values({"aliases": person.aliases, "affiliation": person.affiliation})
        person.aliases = refreshed["aliases"]
        person.aliases_vector = refreshed["aliases_vector"]
        session.flush()
        session.refresh(person)
        return _as_dict(person)


@app.delete("/people/bulk", tags=["People - Bulk"], summary="Delete people in bulk")
def delete_people_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimPerson, "person_uuid", payload.ids)


@app.delete("/people/{person_uuid:uuid}", status_code=204, response_model=None, tags=["People"], summary="Delete a person")
def delete_person(person_uuid: UUID) -> None:
    _delete(DimPerson, person_uuid)


@app.post("/people/bulk", status_code=201, response_model=None, tags=["People - Bulk"], summary="Create people in bulk")
def create_people_bulk(payload: list[PersonPayload], response: Response) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    created_new = 0
    retrieved_from_db = 0
    try:
        prepared: list[tuple[PersonPayload, dict[str, Any], bool]] = []
        texts: list[str] = []
        text_indexes: list[int | None] = []
        for item in payload:
            values = item.model_dump(exclude_none=True)
            has_person_details = "affiliation" in values or "aliases" in values
            values["aliases"] = _normalize_aliases(values.get("aliases"))
            alias_text = _person_embedding_text(
                values["aliases"], values.get("affiliation"), values.get("name")
            )
            text_indexes.append(len(texts) if alias_text else None)
            if alias_text:
                texts.append(alias_text)
            prepared.append((item, values, has_person_details))
        embeddings = _embed_texts_deduplicated(texts) if texts else []
        for item_index, (_, values, _) in enumerate(prepared):
            text_index = text_indexes[item_index]
            if text_index is not None:
                values["aliases_vector"] = embeddings[text_index]
            else:
                values["aliases_vector"] = None
        with get_session() as session:
            for item, values, has_person_details in prepared:
                existing = session.scalar(
                    select(DimPerson).where(func.lower(DimPerson.name) == item.name.strip().casefold())
                )
                was_existing = existing is not None
                if existing is None:
                    existing = DimPerson(**values)
                    session.add(existing)
                    created_new += 1
                else:
                    retrieved_from_db += 1
                if was_existing and has_person_details:
                    existing.affiliation = values.get("affiliation", existing.affiliation)
                if existing is not None:
                    if has_person_details:
                        existing.aliases = values["aliases"]
                        existing.aliases_vector = values["aliases_vector"]
                results.append(_as_dict(existing))
            session.flush()
        _set_bulk_stats(response, created_new, retrieved_from_db)
        logger.info(
            "db_write operation=bulk_upsert model=DimPerson created_new=%d retrieved_from_db=%d",
            created_new,
            retrieved_from_db,
        )
        return results
    except IntegrityError as exc:
        logger.exception("Failed to create people in bulk")
        raise HTTPException(status_code=409, detail="Bulk request conflicts with existing records") from exc


@app.patch("/people/bulk", response_model=None, tags=["People - Bulk"], summary="Update people in bulk")
def update_people_bulk(payload: list[PersonBulkUpdate]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        with get_session() as session:
            for item in payload:
                values = item.model_dump(exclude_unset=True)
                person_uuid = values.pop("person_uuid")
                person = session.get(DimPerson, person_uuid)
                if person is None:
                    raise HTTPException(status_code=404, detail=f"DimPerson {person_uuid} not found")
                for field, value in values.items():
                    setattr(person, field, value)
                refreshed = _person_values({"aliases": person.aliases, "affiliation": person.affiliation})
                person.aliases = refreshed["aliases"]
                person.aliases_vector = refreshed["aliases_vector"]
                results.append(person)
            session.flush()
            return [_as_dict(item) for item in results]
    except IntegrityError as exc:
        logger.exception("Failed to update people in bulk")
        raise HTTPException(status_code=409, detail="Bulk request conflicts with existing records") from exc


@app.post("/citations", status_code=201, response_model=None, tags=["Citations"], summary="Create a citation")
def create_citation(payload: CitationPayload) -> dict[str, Any]:
    values = _with_generated_vector(
        payload.model_dump(exclude_none=True),
        "summarized_quote",
        "summarized_quote_vector",
    )
    with get_session() as session:
        citation = session.get(DimCitation, payload.citation_uuid) if payload.citation_uuid else None
        if citation is None:
            citation = session.scalar(
                select(DimCitation).where(
                    DimCitation.origin_article_id == payload.origin_article_id,
                    DimCitation.person_uuid == payload.person_uuid,
                    DimCitation.exact_quote == payload.exact_quote,
                )
            )
        if citation is None:
            citation = DimCitation(**values)
            session.add(citation)
        else:
            for field, value in values.items():
                if field not in {"citation_uuid", "origin_article_id", "person_uuid", "exact_quote"}:
                    setattr(citation, field, value)
        session.flush()
        return _as_dict(citation)


@app.get("/citations", response_model=None, tags=["Citations"], summary="List citations")
def list_citations(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(DimCitation, limit)


@app.get("/citations/browse", response_model=None, tags=["Citations"], summary="Browse compact citations")
def browse_citations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    query: str | None = Query(default=None, min_length=1),
    status: str | None = Query(default=None, min_length=1),
) -> dict[str, Any]:
    return _browse_compact(DimCitation, query, limit, offset, status)


@app.get("/citations/search", response_model=None, tags=["Citations"], summary="Search citations")
def search_citations(
    query: str = Query(min_length=1),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    with get_session() as session:
        statement = (
            select(DimCitation)
            .where(or_(DimCitation.exact_quote.ilike(pattern), DimCitation.summarized_quote.ilike(pattern)))
            .limit(limit)
        )
        return [_as_dict(item) for item in session.scalars(statement).all()]

@app.get("/citations/{citation_uuid:uuid}", response_model=None, tags=["Citations"], summary="Get a citation")
def get_citation(citation_uuid: UUID) -> dict[str, Any]:
    return _get(DimCitation, citation_uuid)


@app.put("/citations/{citation_uuid:uuid}", response_model=None, tags=["Citations"], summary="Replace a citation")
def update_citation(citation_uuid: UUID, payload: CitationUpdate) -> dict[str, Any]:
    values = _with_generated_vector(
        payload.model_dump(exclude_unset=True),
        "summarized_quote",
        "summarized_quote_vector",
    )
    return _update(DimCitation, citation_uuid, values)


@app.delete("/citations/bulk", tags=["Citations - Bulk"], summary="Delete citations in bulk")
def delete_citations_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimCitation, "citation_uuid", payload.ids)


@app.delete("/citations/{citation_uuid:uuid}", status_code=204, response_model=None, tags=["Citations"], summary="Delete a citation")
def delete_citation(citation_uuid: UUID) -> None:
    _delete(DimCitation, citation_uuid)


@app.post("/citations/bulk", status_code=201, response_model=None, tags=["Citations - Bulk"], summary="Create citations in bulk")
def create_citations_bulk(payload: list[CitationPayload], response: Response) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    created_new = 0
    retrieved_from_db = 0
    try:
        prepared = [item.model_dump(exclude_none=True) for item in payload]
        texts = [
            values["summarized_quote"]
            for values in prepared
            if values.get("summarized_quote") and values["summarized_quote"].strip()
        ]
        embeddings = _embed_texts_deduplicated(texts) if texts else []
        embedding_index = 0
        for values in prepared:
            text = values.get("summarized_quote")
            if text and text.strip():
                values["summarized_quote_vector"] = embeddings[embedding_index]
                embedding_index += 1
            else:
                values["summarized_quote_vector"] = None
        with get_session() as session:
            for item, values in zip(payload, prepared):
                existing = session.scalar(
                    select(DimCitation).where(
                        DimCitation.origin_article_id == item.origin_article_id,
                        DimCitation.person_uuid == item.person_uuid,
                        DimCitation.exact_quote == item.exact_quote,
                    )
                )
                if existing is None:
                    existing = DimCitation(**values)
                    session.add(existing)
                    created_new += 1
                else:
                    retrieved_from_db += 1
                    for field, value in values.items():
                        if field not in {"origin_article_id", "person_uuid", "exact_quote"}:
                            setattr(existing, field, value)
                results.append(_as_dict(existing))
            session.flush()
        _set_bulk_stats(response, created_new, retrieved_from_db)
        logger.info(
            "db_write operation=bulk_upsert model=DimCitation created_new=%d retrieved_from_db=%d",
            created_new,
            retrieved_from_db,
        )
        return results
    except IntegrityError as exc:
        logger.exception("Failed to upsert citations in bulk")
        raise HTTPException(
            status_code=409,
            detail="Citation references an unknown article or person",
        ) from exc


@app.patch("/citations/bulk", response_model=None, tags=["Citations - Bulk"], summary="Update citations in bulk")
def update_citations_bulk(payload: list[CitationBulkUpdate]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with get_session() as session:
        for item in payload:
            values = item.model_dump(exclude_unset=True)
            citation_uuid = values.pop("citation_uuid")
            citation = session.get(DimCitation, citation_uuid)
            if citation is None:
                raise HTTPException(status_code=404, detail=f"DimCitation {citation_uuid} not found")
            values = _with_generated_vector(values, "summarized_quote", "summarized_quote_vector")
            for field, value in values.items():
                setattr(citation, field, value)
            results.append(citation)
        session.flush()
        return [_as_dict(item) for item in results]


@app.post("/topics", status_code=201, response_model=None, tags=["Topics"], summary="Create a topic")
def create_topic(payload: TopicPayload) -> dict[str, Any]:
    values = _with_generated_vector(
        payload.model_dump(exclude_none=True),
        "topic_description",
        "topic_description_vector",
    )
    with get_session() as session:
        topic = session.get(DimTopic, payload.topic_uuid) if payload.topic_uuid else None
        if topic is None:
            topic = session.scalar(
                select(DimTopic).where(func.lower(DimTopic.topic_name) == payload.topic_name.strip().casefold())
            )
        if topic is None:
            topic = DimTopic(**values)
            session.add(topic)
        else:
            for field, value in values.items():
                if field != "topic_uuid":
                    setattr(topic, field, value)
        session.flush()
        return _as_dict(topic)


@app.get("/topics", response_model=None, tags=["Topics"], summary="List topics")
def list_topics(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(DimTopic, limit)


@app.get("/topics/browse", response_model=None, tags=["Topics"], summary="Browse topics with analytical aggregates")
def browse_topics(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    topic_query: str | None = Query(default=None, min_length=1),
    sort_by: str = Query("topic_name"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    """Return topic rows with counts and stance statistics calculated by the database."""
    stance_number = FctAttitude.stance
    stats = (
        select(
            FctAttitude.topic_uuid.label("topic_uuid"),
            func.count(func.distinct(FctAttitude.citation_uuid)).label("citation_count"),
            func.count(func.distinct(DimCitation.person_uuid)).label("people_count"),
            func.avg(stance_number).label("stance_average"),
            func.stddev_pop(stance_number).label("stance_std"),
            func.percentile_cont(0.5).within_group(stance_number).label("stance_median"),
        )
        .join(DimCitation, FctAttitude.citation_uuid == DimCitation.citation_uuid)
        .group_by(FctAttitude.topic_uuid)
        .subquery()
    )
    sort_columns = {
        "topic_name": DimTopic.topic_name,
        "citation_count": stats.c.citation_count,
        "people_count": stats.c.people_count,
        "stance_average": stats.c.stance_average,
        "stance_std": stats.c.stance_std,
        "stance_median": stats.c.stance_median,
    }
    sort_column = sort_columns.get(sort_by, DimTopic.topic_name)
    with get_session() as session:
        statement = (
            select(
                DimTopic,
                stats.c.citation_count,
                stats.c.people_count,
                stats.c.stance_average,
                stats.c.stance_std,
                stats.c.stance_median,
            )
            .outerjoin(stats, DimTopic.topic_uuid == stats.c.topic_uuid)
        )
        count_statement = select(func.count()).select_from(DimTopic)
        if topic_query:
            pattern = f"%{topic_query.strip()}%"
            statement = statement.where(DimTopic.topic_name.ilike(pattern))
            count_statement = count_statement.where(DimTopic.topic_name.ilike(pattern))
        total = session.scalar(count_statement) or 0
        ordered_sort = sort_column.asc().nulls_last() if sort_dir == "asc" else sort_column.desc().nulls_last()
        rows = session.execute(
            statement.order_by(ordered_sort)
            .offset(offset).limit(limit)
        ).all()
    items = []
    for topic, citation_count, people_count, stance_average, stance_std, stance_median in rows:
        item = _as_dict(topic)
        item.update({
            "citation_count": citation_count or 0,
            "people_count": people_count or 0,
            "stance_average": float(stance_average) if stance_average is not None else None,
            "stance_std": float(stance_std) if stance_std is not None else None,
            "stance_median": float(stance_median) if stance_median is not None else None,
        })
        items.append(item)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/topics/{topic_uuid:uuid}", response_model=None, tags=["Topics"], summary="Get a topic")
def get_topic(topic_uuid: UUID) -> dict[str, Any]:
    return _get(DimTopic, topic_uuid)


@app.put("/topics/{topic_uuid:uuid}", response_model=None, tags=["Topics"], summary="Replace a topic")
def update_topic(topic_uuid: UUID, payload: TopicUpdate) -> dict[str, Any]:
    values = _with_generated_vector(
        payload.model_dump(exclude_unset=True),
        "topic_description",
        "topic_description_vector",
    )
    return _update(DimTopic, topic_uuid, values)


@app.delete("/topics/bulk", tags=["Topics - Bulk"], summary="Delete topics in bulk")
def delete_topics_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimTopic, "topic_uuid", payload.ids)


@app.delete("/topics/{topic_uuid:uuid}", status_code=204, response_model=None, tags=["Topics"], summary="Delete a topic")
def delete_topic(topic_uuid: UUID) -> None:
    _delete(DimTopic, topic_uuid)


@app.post("/topics/bulk", status_code=201, response_model=None, tags=["Topics - Bulk"], summary="Create topics in bulk")
def create_topics_bulk(payload: list[TopicPayload], response: Response) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    created_new = 0
    retrieved_from_db = 0
    try:
        prepared = [item.model_dump(exclude_none=True) for item in payload]
        texts = [
            values["topic_description"]
            for values in prepared
            if values.get("topic_description") and values["topic_description"].strip()
        ]
        embeddings = _embed_texts_deduplicated(texts) if texts else []
        embedding_index = 0
        for values in prepared:
            text = values.get("topic_description")
            if text and text.strip():
                values["topic_description_vector"] = embeddings[embedding_index]
                embedding_index += 1
            else:
                values["topic_description_vector"] = None
        with get_session() as session:
            for item, values in zip(payload, prepared):
                existing = session.scalar(
                    select(DimTopic).where(func.lower(DimTopic.topic_name) == item.topic_name.strip().casefold())
                )
                if existing is None:
                    existing = DimTopic(**values)
                    session.add(existing)
                    created_new += 1
                else:
                    retrieved_from_db += 1
                    for field, value in values.items():
                        if field != "topic_uuid":
                            setattr(existing, field, value)
                results.append(_as_dict(existing))
            session.flush()
        _set_bulk_stats(response, created_new, retrieved_from_db)
        logger.info(
            "db_write operation=bulk_upsert model=DimTopic created_new=%d retrieved_from_db=%d",
            created_new,
            retrieved_from_db,
        )
        return results
    except IntegrityError as exc:
        logger.exception("Failed to create topics in bulk")
        raise HTTPException(status_code=409, detail="Bulk request conflicts with existing records") from exc


@app.patch("/topics/bulk", response_model=None, tags=["Topics - Bulk"], summary="Update topics in bulk")
def update_topics_bulk(payload: list[TopicBulkUpdate]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with get_session() as session:
        for item in payload:
            values = item.model_dump(exclude_unset=True)
            topic_uuid = values.pop("topic_uuid")
            topic = session.get(DimTopic, topic_uuid)
            if topic is None:
                raise HTTPException(status_code=404, detail=f"DimTopic {topic_uuid} not found")
            values = _with_generated_vector(values, "topic_description", "topic_description_vector")
            for field, value in values.items():
                setattr(topic, field, value)
            results.append(topic)
        session.flush()
        return [_as_dict(item) for item in results]


@app.post("/attitudes", status_code=201, response_model=None, tags=["Attitudes"], summary="Create an attitude")
def create_attitude(payload: AttitudePayload) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    with get_session() as session:
        item = session.get(FctAttitude, (payload.topic_uuid, payload.citation_uuid))
        if item is None:
            item = FctAttitude(**values)
            session.add(item)
        else:
            for field, value in values.items():
                if field not in {"topic_uuid", "citation_uuid"}:
                    setattr(item, field, value)
        session.flush()
        return _as_dict(item)


@app.get("/attitudes", response_model=None, tags=["Attitudes"], summary="List attitudes")
def list_attitudes(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(FctAttitude, limit)


@app.get("/attitudes/browse", response_model=None, tags=["Attitudes"], summary="Browse attitudes with related entities")
def browse_attitudes(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    stance: str | None = Query(default=None, min_length=1),
    status: str | None = Query(default=None, min_length=1),
    person_uuid: UUID | None = None,
    topic_uuid: UUID | None = None,
    person_query: str | None = Query(default=None, min_length=1),
    topic_query: str | None = Query(default=None, min_length=1),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
) -> dict[str, Any]:
    with get_session() as session:
        statement = (
            select(FctAttitude, DimTopic, DimCitation, DimPerson)
            .join(DimTopic, FctAttitude.topic_uuid == DimTopic.topic_uuid)
            .join(DimCitation, FctAttitude.citation_uuid == DimCitation.citation_uuid)
            .outerjoin(DimPerson, DimCitation.person_uuid == DimPerson.person_uuid)
        )
        count_statement = select(func.count()).select_from(FctAttitude).join(DimCitation, FctAttitude.citation_uuid == DimCitation.citation_uuid).join(DimTopic, FctAttitude.topic_uuid == DimTopic.topic_uuid).outerjoin(DimPerson, DimCitation.person_uuid == DimPerson.person_uuid)
        if stance:
            statement = statement.where(cast(FctAttitude.stance, String).ilike(f"%{stance}%"))
            count_statement = count_statement.where(cast(FctAttitude.stance, String).ilike(f"%{stance}%"))
        if status:
            statement = statement.where(FctAttitude.status == status)
            count_statement = count_statement.where(FctAttitude.status == status)
        if person_uuid:
            statement = statement.where(DimCitation.person_uuid == person_uuid)
            count_statement = count_statement.where(DimCitation.person_uuid == person_uuid)
        if topic_uuid:
            statement = statement.where(FctAttitude.topic_uuid == topic_uuid)
            count_statement = count_statement.where(FctAttitude.topic_uuid == topic_uuid)
        if person_query:
            statement = statement.where(DimPerson.name.ilike(f"%{person_query}%"))
            count_statement = count_statement.where(DimPerson.name.ilike(f"%{person_query}%"))
        if topic_query:
            statement = statement.where(DimTopic.topic_name.ilike(f"%{topic_query}%"))
            count_statement = count_statement.where(DimTopic.topic_name.ilike(f"%{topic_query}%"))
        total = session.scalar(count_statement) or 0
        sort_column = {"created_at": FctAttitude.created_at, "stance": FctAttitude.stance, "relevancy_score": FctAttitude.relevancy_score}.get(sort_by, FctAttitude.created_at)
        rows = session.execute(statement.order_by(sort_column.asc() if sort_dir == "asc" else sort_column.desc()).offset(offset).limit(limit)).all()
        stance_rows = session.execute(
            select(FctAttitude.stance, func.count()).select_from(FctAttitude)
            .join(DimCitation, FctAttitude.citation_uuid == DimCitation.citation_uuid)
            .where(*(condition for condition in (
                cast(FctAttitude.stance, String).ilike(f"%{stance}%") if stance else None,
                FctAttitude.status == status if status else None,
                DimCitation.person_uuid == person_uuid if person_uuid else None,
                FctAttitude.topic_uuid == topic_uuid if topic_uuid else None,
            ) if condition))
            .group_by(FctAttitude.stance)
        ).all()
    items = []
    for attitude, topic, citation, person in rows:
        items.append({
            **_as_dict(attitude),
            "topic_name": topic.topic_name,
            "topic_description": topic.topic_description,
            "citation_exact_quote": citation.exact_quote,
            "citation_summarized_quote": citation.summarized_quote,
            "citation_type": citation.citation_type,
            "origin_article_id": citation.origin_article_id,
            "person_uuid": citation.person_uuid,
            "person_name": person.name if person else None,
        })
    return {"items": items, "total": total, "offset": offset, "limit": limit, "stance_counts": {stance or "UNKNOWN": count for stance, count in stance_rows}}


@app.get("/attitudes/{topic_uuid:uuid}/{citation_uuid:uuid}", response_model=None, tags=["Attitudes"], summary="Get an attitude")
def get_attitude(topic_uuid: UUID, citation_uuid: UUID) -> dict[str, Any]:
    return _get(FctAttitude, (topic_uuid, citation_uuid))


@app.put("/attitudes/{topic_uuid:uuid}/{citation_uuid:uuid}", response_model=None, tags=["Attitudes"], summary="Replace an attitude")
def update_attitude(topic_uuid: UUID, citation_uuid: UUID, payload: AttitudeUpdate) -> dict[str, Any]:
    return _update(FctAttitude, (topic_uuid, citation_uuid), payload.model_dump(exclude_unset=True))


@app.delete("/attitudes/bulk", tags=["Attitudes - Bulk"], summary="Delete attitudes in bulk")
def delete_attitudes_bulk(payload: BulkAttitudeDeleteRequest) -> dict[str, int]:
    return _bulk_delete_attitudes(payload.keys)


@app.delete("/attitudes/{topic_uuid:uuid}/{citation_uuid:uuid}", status_code=204, response_model=None, tags=["Attitudes"], summary="Delete an attitude")
def delete_attitude(topic_uuid: UUID, citation_uuid: UUID) -> None:
    _delete(FctAttitude, (topic_uuid, citation_uuid))


@app.post("/attitudes/bulk", status_code=201, response_model=None, tags=["Attitudes - Bulk"], summary="Create attitudes in bulk")
def create_attitudes_bulk(payload: list[AttitudePayload], response: Response) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    created_new = 0
    retrieved_from_db = 0
    try:
        with get_session() as session:
            payload_by_key = {
                (item.topic_uuid, item.citation_uuid): item
                for item in payload
            }
            items = []
            for payload_item in payload_by_key.values():
                values = payload_item.model_dump()
                key = (payload_item.topic_uuid, payload_item.citation_uuid)
                item = session.get(FctAttitude, key)
                if item is None:
                    item = FctAttitude(**values)
                    session.add(item)
                    created_new += 1
                else:
                    retrieved_from_db += 1
                    for field, value in values.items():
                        if field not in {"topic_uuid", "citation_uuid"}:
                            setattr(item, field, value)
                items.append(item)
            session.flush()
            results = [_as_dict(item) for item in items]
        _set_bulk_stats(response, created_new, retrieved_from_db)
        logger.info(
            "db_write operation=bulk_upsert model=FctAttitude created_new=%d retrieved_from_db=%d",
            created_new,
            retrieved_from_db,
        )
        return results
    except IntegrityError as exc:
        logger.exception("Failed to upsert attitudes in bulk")
        raise HTTPException(status_code=409, detail="Attitude references an unknown topic or citation") from exc


@app.patch("/attitudes/bulk", response_model=None, tags=["Attitudes - Bulk"], summary="Update attitudes in bulk")
def update_attitudes_bulk(payload: list[AttitudeBulkUpdate]) -> list[dict[str, Any]]:
    values = [item.model_dump(exclude_unset=True) for item in payload]
    with get_session() as session:
        result = []
        for item_values in values:
            key = (item_values.pop("topic_uuid"), item_values.pop("citation_uuid"))
            item = session.get(FctAttitude, key)
            if item is None:
                raise HTTPException(status_code=404, detail=f"FctAttitude {key} not found")
            for field, value in item_values.items():
                setattr(item, field, value)
            result.append(item)
        session.flush()
        return [_as_dict(item) for item in result]


@app.post("/inconsistencies", status_code=201, response_model=None, tags=["Inconsistencies"], summary="Create an inconsistency assessment")
def create_inconsistency(payload: InconsistencyPayload, response: Response) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    with get_session() as session:
        existing = session.scalar(
            select(FctInconsistency).where(
                FctInconsistency.topic_uuid == payload.topic_uuid,
                or_(
                    (
                        (FctInconsistency.citation_a_uuid == payload.citation_a_uuid)
                        & (FctInconsistency.citation_b_uuid == payload.citation_b_uuid)
                    ),
                    (
                        (FctInconsistency.citation_a_uuid == payload.citation_b_uuid)
                        & (FctInconsistency.citation_b_uuid == payload.citation_a_uuid)
                    ),
                ),
            )
        )
        if existing is not None:
            _set_bulk_stats(response, 0, 1)
            logger.info(
                "db_write operation=upsert model=FctInconsistency created_new=0 retrieved_from_db=1"
            )
            return _as_dict(existing)
        item = FctInconsistency(**values)
        session.add(item)
        session.flush()
        _set_bulk_stats(response, 1, 0)
        logger.info(
            "db_write operation=upsert model=FctInconsistency created_new=1 retrieved_from_db=0"
        )
        return _as_dict(item)


@app.get("/inconsistencies", response_model=None, tags=["Inconsistencies"], summary="List inconsistency assessments")
def list_inconsistencies(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(FctInconsistency, limit)


@app.get("/inconsistencies/browse", response_model=None, tags=["Inconsistencies"], summary="Browse inconsistencies with evidence")
def browse_inconsistencies(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    classification: str | None = Query(default=None, min_length=1),
    person_uuid: UUID | None = None,
    topic_uuid: UUID | None = None,
    person_query: str | None = Query(default=None, min_length=1),
    topic_query: str | None = Query(default=None, min_length=1),
    sort_by: str = Query("detected_at"),
    sort_dir: str = Query("desc"),
) -> dict[str, Any]:
    citation_a = aliased(DimCitation)
    citation_b = aliased(DimCitation)
    with get_session() as session:
        statement = (
            select(FctInconsistency, DimPerson, DimTopic, citation_a, citation_b)
            .join(DimPerson, FctInconsistency.person_uuid == DimPerson.person_uuid)
            .join(DimTopic, FctInconsistency.topic_uuid == DimTopic.topic_uuid)
            .join(citation_a, FctInconsistency.citation_a_uuid == citation_a.citation_uuid)
            .join(citation_b, FctInconsistency.citation_b_uuid == citation_b.citation_uuid)
        )
        count_statement = select(func.count()).select_from(FctInconsistency).join(DimPerson, FctInconsistency.person_uuid == DimPerson.person_uuid).join(DimTopic, FctInconsistency.topic_uuid == DimTopic.topic_uuid)
        if classification:
            statement = statement.where(FctInconsistency.classification == classification)
            count_statement = count_statement.where(FctInconsistency.classification == classification)
        if person_uuid:
            statement = statement.where(FctInconsistency.person_uuid == person_uuid)
            count_statement = count_statement.where(FctInconsistency.person_uuid == person_uuid)
        if topic_uuid:
            statement = statement.where(FctInconsistency.topic_uuid == topic_uuid)
            count_statement = count_statement.where(FctInconsistency.topic_uuid == topic_uuid)
        if person_query:
            statement = statement.where(DimPerson.name.ilike(f"%{person_query}%"))
            count_statement = count_statement.where(DimPerson.name.ilike(f"%{person_query}%"))
        if topic_query:
            statement = statement.where(DimTopic.topic_name.ilike(f"%{topic_query}%"))
            count_statement = count_statement.where(DimTopic.topic_name.ilike(f"%{topic_query}%"))
        total = session.scalar(count_statement) or 0
        sort_column = {"detected_at": FctInconsistency.detected_at, "classification": FctInconsistency.classification, "severity": FctInconsistency.severity}.get(sort_by, FctInconsistency.detected_at)
        rows = session.execute(statement.order_by(sort_column.asc() if sort_dir == "asc" else sort_column.desc()).offset(offset).limit(limit)).all()
    items = []
    for inconsistency, person, topic, citation_left, citation_right in rows:
        items.append({
            **_as_dict(inconsistency),
            "person_name": person.name,
            "topic_name": topic.topic_name,
            "citation_a_exact_quote": citation_left.exact_quote,
            "citation_b_exact_quote": citation_right.exact_quote,
            "citation_a_origin_article_id": citation_left.origin_article_id,
            "citation_b_origin_article_id": citation_right.origin_article_id,
        })
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/inconsistencies/{inconsistency_uuid:uuid}", response_model=None, tags=["Inconsistencies"], summary="Get inconsistency assessment")
def get_inconsistency(inconsistency_uuid: UUID) -> dict[str, Any]:
    record = _get(FctInconsistency, inconsistency_uuid)
    with get_session() as session:
        person = session.get(DimPerson, record["person_uuid"])
        topic = session.get(DimTopic, record["topic_uuid"])
        citation_a = session.get(DimCitation, record["citation_a_uuid"])
        citation_b = session.get(DimCitation, record["citation_b_uuid"])
    record["person"] = _as_dict(person) if person else None
    record["topic"] = _as_dict(topic) if topic else None
    record["citation_a"] = _as_dict(citation_a) if citation_a else None
    record["citation_b"] = _as_dict(citation_b) if citation_b else None
    return record


def _embed_texts(texts: list[str]) -> list[list[float]]:
    started = time.perf_counter()
    try:
        response = _embedding_client.embeddings.create(
            model=_embedding_model_name,
            input=texts,
        )
    except Exception as exc:
        emit_event(
            "embedding_request",
            duration_ms=int((time.perf_counter() - started) * 1000),
            attributes={"text_count": len(texts), "status": "failed"},
        )
        logger.exception("Failed to generate embeddings")
        raise HTTPException(status_code=503, detail="Embedding service unavailable") from exc
    emit_event(
        "embedding_request",
        duration_ms=int((time.perf_counter() - started) * 1000),
        attributes={
            "text_count": len(texts),
            "model": _embedding_model_name,
            "status": "completed",
        },
    )
    embeddings = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(embeddings) != len(texts):
        raise HTTPException(
            status_code=503,
            detail="Embedding service returned an incomplete batch",
        )
    for embedding in embeddings:
        _validate_vector(embedding)
    return embeddings


def _normalized_embedding_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _embed_texts_deduplicated(texts: list[str]) -> list[list[float]]:
    """Embed unique normalized inputs once and restore the original order."""
    if not texts:
        return []
    unique: list[str] = []
    positions: dict[str, int] = {}
    for text in texts:
        key = _normalized_embedding_text(text)
        if key not in positions:
            positions[key] = len(unique)
            unique.append(text)
    embeddings = _embed_texts(unique)
    return [embeddings[positions[_normalized_embedding_text(text)]] for text in texts]


def _embedding_results(texts: list[str]) -> list[dict[str, Any]]:
    """Return one result per input, including validation/request errors."""
    if not texts:
        return []
    valid_texts = [text for text in texts if text.strip()]
    valid_indexes = [index for index, text in enumerate(texts) if text.strip()]
    results = [
        {
            "index": index,
            "text": text,
            "embedding": None,
            "error": "text must not be blank",
        }
        for index, text in enumerate(texts)
        if not text.strip()
    ]
    try:
        embeddings = _embed_texts_deduplicated(valid_texts)
    except Exception as exc:
        results.extend(
            {"index": index, "text": texts[index], "embedding": None, "error": str(exc)}
            for index in valid_indexes
        )
    else:
        results.extend(
            {
                "index": index,
                "text": texts[index],
                "embedding": embedding,
                "error": None,
            }
            for index, embedding in zip(valid_indexes, embeddings)
        )
    return sorted(results, key=lambda item: item["index"])


@app.post("/vectors/embed", response_model=None, tags=["Vectors"], summary="Embed text")
def embed_text(payload: EmbeddingRequest) -> dict[str, Any]:
    return {
        "model": _embedding_model_name,
        "dimension": EMBEDDING_DIMENSION,
        "embedding": _embed_texts([payload.text])[0],
    }


@app.post("/vectors/embed-batch", response_model=None, tags=["Vectors"], summary="Embed multiple texts")
def embed_text_batch(payload: EmbeddingBatchRequest) -> dict[str, Any]:
    results = _embedding_results(payload.texts)
    return {
        "model": _embedding_model_name,
        "dimension": EMBEDDING_DIMENSION,
        "results": results,
        "embeddings": [item["embedding"] for item in results],
    }


def _search_aliases_by_embedding(
    embedding: list[float], person_uuid: UUID | None, limit: int, min_similarity: float
) -> list[dict[str, Any]]:
    with get_session() as session:
        distance = DimPerson.aliases_vector.cosine_distance(embedding).label("distance")
        similarity = (1 - distance).label("cosine_similarity")
        statement = (
            select(DimPerson, distance, similarity)
            .where(DimPerson.aliases_vector.is_not(None))
            .where(similarity >= min_similarity)
            .order_by(DimPerson.aliases_vector.cosine_distance(embedding))
            .limit(limit)
        )
        if person_uuid is not None:
            statement = statement.where(DimPerson.person_uuid == person_uuid)
        return [
            {
                **_as_dict(person),
                "distance": float(distance_value),
                "cosine_similarity": float(similarity_value),
            }
            for person, distance_value, similarity_value in session.execute(statement).all()
        ]


@app.post("/people/search", response_model=None, tags=["People"], summary="Search people by alias text")
def search_people(
    payload: EmbeddingRequest,
    limit: int = Query(10, ge=1, le=100),
    min_similarity: float = Query(_default_min_cosine_similarity, ge=-1, le=1),
) -> list[dict[str, Any]]:
    return _search_aliases_by_embedding(_embed_texts([payload.text])[0], None, limit, min_similarity)


@app.post(
    "/people/search/vector",
    response_model=None,
    tags=["People"],
    summary="Search people by alias embedding",
)
def search_people_by_vector(
    embedding: list[float],
    limit: int = Query(10, ge=1, le=100),
    min_similarity: float = Query(_default_min_cosine_similarity, ge=-1, le=1),
) -> list[dict[str, Any]]:
    _validate_vector(embedding)
    return _search_aliases_by_embedding(embedding, None, limit, min_similarity)


@app.post("/vectors/reembed-all", response_model=None, tags=["Vectors"], summary="Re-embed all vector fields")
def reembed_all_vectors() -> dict[str, int]:
    with get_session() as session:
        people = session.scalars(select(DimPerson)).all()
        citations = session.scalars(select(DimCitation)).all()
        topics = session.scalars(select(DimTopic)).all()
        texts: list[str] = []
        targets: list[tuple[Any, str]] = []
        for person in people:
            person.aliases = _normalize_aliases(person.aliases)
            aliases = person.aliases
            alias_text = _person_embedding_text(aliases, person.affiliation, person.name)
            if alias_text:
                texts.append(alias_text)
                targets.append((person, "aliases_vector"))
        for citation in citations:
            if citation.summarized_quote:
                texts.append(citation.summarized_quote)
                targets.append((citation, "summarized_quote_vector"))
        for topic in topics:
            if topic.topic_description:
                texts.append(topic.topic_description)
                targets.append((topic, "topic_description_vector"))
        embeddings = _embed_texts_deduplicated(texts) if texts else []
        for (target, field), embedding in zip(targets, embeddings):
            setattr(target, field, embedding)
        session.flush()
        return {
            "people": len(people),
            "people_embedded": sum(field == "aliases_vector" for _, field in targets),
            "citations": len(citations),
            "citations_embedded": sum(field == "summarized_quote_vector" for _, field in targets),
            "topics": len(topics),
            "topics_embedded": sum(field == "topic_description_vector" for _, field in targets),
        }


@app.post("/vectors/reindex-all", response_model=None, tags=["Vectors"], summary="Rebuild all vector fields")
def reindex_all_vectors() -> dict[str, int]:
    index_names = {
        "ix_dim_person_aliases_vector",
        "ix_dim_citation_summarized_quote_vector",
        "ix_dim_topic_description_vector",
    }
    created = 0
    for table in (DimPerson.__table__, DimCitation.__table__, DimTopic.__table__):
        for index in table.indexes:
            if index.name in index_names:
                index.create(bind=engine, checkfirst=True)
                created += 1
    return {"indexes_ensured": created}


@app.post("/topics/search", response_model=None, tags=["Topics"], summary="Search topics by embedding")
def search_topics(
    embedding: list[float],
    limit: int = Query(10, ge=1, le=100),
    min_similarity: float = Query(_default_min_cosine_similarity, ge=-1, le=1),
) -> list[dict[str, Any]]:
    _validate_vector(embedding)
    with get_session() as session:
        distance = DimTopic.topic_description_vector.cosine_distance(embedding).label("distance")
        similarity = (1 - distance).label("cosine_similarity")
        statement = (
            select(DimTopic, distance, similarity)
            .where(DimTopic.topic_description_vector.is_not(None))
            .where(similarity >= min_similarity)
            .order_by(similarity.desc())
            .limit(limit)
        )
        return [
            {
                **_as_dict(topic),
                "distance": float(distance_value),
                "cosine_similarity": float(similarity_value),
            }
            for topic, distance_value, similarity_value in session.execute(statement).all()
        ]


@app.post("/topics/search/text", response_model=None, tags=["Topics"], summary="Search topics by text")
def search_topics_by_text(
    payload: EmbeddingRequest,
    limit: int = Query(10, ge=1, le=100),
    min_similarity: float = Query(_default_min_cosine_similarity, ge=-1, le=1),
) -> list[dict[str, Any]]:
    return search_topics(_embed_texts([payload.text])[0], limit, min_similarity)


@app.post("/topics/embeddings", response_model=None, tags=["Topics"], summary="Get topic embeddings in a batch")
def topic_embeddings(payload: TopicEmbeddingsRequest) -> list[dict[str, Any]]:
    requested_ids = list(dict.fromkeys(payload.topic_uuids))
    with get_session() as session:
        topics = session.scalars(
            select(DimTopic).where(DimTopic.topic_uuid.in_(requested_ids))
        ).all()
        topics_by_id = {topic.topic_uuid: topic for topic in topics}
        missing = [
            topic
            for topic in topics
            if topic.topic_description_vector is None
            and payload.generate_missing
            and (topic.topic_description or topic.topic_name).strip()
        ]
        if missing:
            embeddings = _embed_texts([
                (topic.topic_description or topic.topic_name).strip()
                for topic in missing
            ])
            for topic, embedding in zip(missing, embeddings):
                topic.topic_description_vector = embedding
            session.flush()
        return [
            {
                "topic_uuid": str(topic_uuid),
                "topic_name": topics_by_id[topic_uuid].topic_name,
                "topic_description": topics_by_id[topic_uuid].topic_description,
                "embedding": topics_by_id[topic_uuid].topic_description_vector,
            }
            for topic_uuid in requested_ids
            if topic_uuid in topics_by_id
        ]


@app.post("/citations/search/vector", response_model=None, tags=["Citations"], summary="Search citations by embedding")
def search_citations_by_vector(
    embedding: list[float],
    limit: int = Query(10, ge=1, le=100),
    min_similarity: float = Query(_default_min_cosine_similarity, ge=-1, le=1),
    person_uuid: UUID | None = None,
    topic_uuid: UUID | None = None,
    before: str | None = None,
    window_days: int = Query(365, ge=1, le=3650),
    exclude_citation_uuid: UUID | None = None,
) -> list[dict[str, Any]]:
    _validate_vector(embedding)
    parsed_before = parse_datetime(before) if before else None
    if parsed_before is not None and not isinstance(parsed_before, datetime):
        raise HTTPException(status_code=422, detail="before must be a valid datetime")
    effective_before = parsed_before or datetime.now(timezone.utc)
    cutoff = effective_before - timedelta(days=window_days)
    with get_session() as session:
        distance = DimCitation.summarized_quote_vector.cosine_distance(embedding).label("distance")
        similarity = (1 - distance).label("cosine_similarity")
        stance_query = select(FctAttitude.stance).where(
            FctAttitude.citation_uuid == DimCitation.citation_uuid
        )
        if topic_uuid is not None:
            stance_query = stance_query.where(FctAttitude.topic_uuid == topic_uuid)
        stance = (
            stance_query.correlate(DimCitation).limit(1).scalar_subquery().label("stance")
        )
        statement = (
            select(DimCitation, distance, similarity, stance)
            .join(DimArticle, DimCitation.origin_article_id == DimArticle.article_id)
            .where(DimCitation.summarized_quote_vector.is_not(None))
            .where(similarity >= min_similarity)
            .where(DimArticle.published_at.is_not(None))
            .where(DimArticle.published_at >= cutoff)
            .where(DimArticle.published_at <= effective_before)
            .order_by(similarity.desc())
        )
        if person_uuid is not None:
            statement = statement.where(DimCitation.person_uuid == person_uuid)
        if topic_uuid is not None:
            statement = statement.where(
                exists(
                    select(1).where(
                        FctAttitude.citation_uuid == DimCitation.citation_uuid,
                        FctAttitude.topic_uuid == topic_uuid,
                    )
                )
            )
        if exclude_citation_uuid is not None:
            statement = statement.where(DimCitation.citation_uuid != exclude_citation_uuid)
        statement = statement.limit(limit)
        return [
            {
                **_as_dict(citation),
                "distance": float(distance_value),
                "cosine_similarity": float(similarity_value),
                "stance": stance_value,
                "stance_polarity": _stance_polarity(stance_value),
            }
            for citation, distance_value, similarity_value, stance_value in session.execute(statement).all()
        ]


@app.post("/citations/search/text", response_model=None, tags=["Citations"], summary="Search citations by text")
def search_citations_by_text(
    payload: EmbeddingRequest,
    limit: int = Query(10, ge=1, le=100),
    min_similarity: float = Query(_default_min_cosine_similarity, ge=-1, le=1),
    person_uuid: UUID | None = None,
    topic_uuid: UUID | None = None,
    before: str | None = None,
    window_days: int = Query(365, ge=1, le=3650),
    exclude_citation_uuid: UUID | None = None,
) -> list[dict[str, Any]]:
    return search_citations_by_vector(
        _embed_texts([payload.text])[0],
        limit,
        min_similarity,
        person_uuid,
        topic_uuid,
        before,
        window_days,
        exclude_citation_uuid,
    )


@app.get(
    "/citations/history",
    response_model=None,
    tags=["Citations"],
    summary="List topic citations for a person in a time window",
)
def citation_history(
    person_uuid: UUID,
    topic_uuid: UUID | None = None,
    before: datetime | None = None,
    window_days: int = Query(365, ge=1, le=3650),
    exclude_citation_uuid: UUID | None = None,
    limit: int = Query(500, ge=1, le=1000),
) -> list[dict[str, Any]]:
    effective_before = before or datetime.now(timezone.utc)
    if effective_before.tzinfo is None:
        effective_before = effective_before.replace(tzinfo=timezone.utc)
    cutoff = effective_before - timedelta(days=window_days)
    with get_session() as session:
        statement = (
            select(DimCitation, FctAttitude)
            .join(DimArticle, DimCitation.origin_article_id == DimArticle.article_id)
            .join(
                FctAttitude,
                FctAttitude.citation_uuid == DimCitation.citation_uuid,
            )
            .where(DimCitation.person_uuid == person_uuid)
            .where(DimArticle.published_at >= cutoff)
            .where(DimArticle.published_at <= effective_before)
        )
        if topic_uuid is not None:
            statement = statement.where(FctAttitude.topic_uuid == topic_uuid)
        if exclude_citation_uuid is not None:
            statement = statement.where(
                DimCitation.citation_uuid != exclude_citation_uuid
            )
        statement = statement.order_by(DimArticle.published_at.desc()).limit(limit)
        return [
            {
                **_as_dict(citation),
                "topic_uuid": attitude.topic_uuid,
                "stance": attitude.stance,
                "stance_confidence": attitude.stance_confidence,
                "stance_polarity": _stance_polarity(attitude.stance),
                "historical_date": article_date,
            }
            for citation, attitude, article_date in (
                (citation, attitude, article_date)
                for citation, attitude, article_date in session.execute(
                    statement.add_columns(DimArticle.published_at)
                ).all()
            )
        ]


@app.get(
    "/visualization/query",
    response_model=None,
    tags=["Visualization"],
    summary="Query speaker attitudes for the visualization",
)
def visualization_query(
    phrase: str | None = Query(None, min_length=1, max_length=512),
    general_topic_field: str | None = Query(None, min_length=1, max_length=128),
    min_similarity: float = Query(0.55, ge=-1, le=1),
    limit: int = Query(1000, ge=1, le=5000),
) -> dict[str, Any]:
    """Return one traceable row per citation matching any relevant topic."""
    started = time.perf_counter()
    requested_topic_fields = [
        value.strip()
        for value in (general_topic_field or "").split(",")
        if value.strip()
    ]
    if not phrase and not general_topic_field:
        logger.info("visualization_query status=unfiltered")
    try:
        query_vector = _embed_texts([phrase])[0] if phrase else None
    except Exception as exc:
        logger.exception("visualization_query status=embedding_failed phrase_length=%d", len(phrase or ""))
        raise HTTPException(status_code=503, detail="Topic similarity search is temporarily unavailable") from exc
    with get_session() as session:
        similarity = None
        topic_fields: dict[str, int] = {}
        tag_rows = session.execute(
            select(DimArticle.article_id, DimArticle.tags)
            .join(DimCitation, DimCitation.origin_article_id == DimArticle.article_id)
            .join(FctAttitude, FctAttitude.citation_uuid == DimCitation.citation_uuid)
            .where(DimArticle.tags.is_not(None))
        ).all()
        counted_article_tags: set[tuple[UUID, str]] = set()
        for article_id, tags in tag_rows:
            values = tags if isinstance(tags, list) else [tags]
            for value in values:
                tag = str(value).strip()
                if tag and (article_id, tag) not in counted_article_tags:
                    counted_article_tags.add((article_id, tag))
                    topic_fields[tag] = topic_fields.get(tag, 0) + 1
        statement = (
            select(
                DimPerson,
                DimTopic,
                DimCitation,
                DimArticle,
                DimArticle.published_at,
                FctAttitude,
                FctInconsistency,
            )
            .join(DimCitation, DimCitation.person_uuid == DimPerson.person_uuid)
            .join(DimArticle, DimCitation.origin_article_id == DimArticle.article_id)
            .join(FctAttitude, FctAttitude.citation_uuid == DimCitation.citation_uuid)
            .join(DimTopic, FctAttitude.topic_uuid == DimTopic.topic_uuid)
            .outerjoin(
                FctInconsistency,
                or_(
                    FctInconsistency.citation_a_uuid == DimCitation.citation_uuid,
                    FctInconsistency.citation_b_uuid == DimCitation.citation_uuid,
                ),
            )
            .where(DimCitation.person_uuid.is_not(None))
            .order_by(DimArticle.published_at.asc().nulls_last())
            .limit(limit)
        )
        if requested_topic_fields:
            tags = cast(DimArticle.tags, JSONB)
            statement = statement.where(or_(*[
                tags.contains([field])
                for field in requested_topic_fields
            ], *[
                tags == cast(json.dumps(field), JSONB)
                for field in requested_topic_fields
            ]))
        if query_vector:
            distance = DimTopic.topic_description_vector.cosine_distance(query_vector)
            similarity = (1 - distance).label("topic_similarity")
            statement = (
                statement.add_columns(similarity)
                .where(DimTopic.topic_description_vector.is_not(None))
                .where(similarity >= max(-1.0, min_similarity - 0.25))
            )
        rows = session.execute(statement).all()

    def numeric_stance(value: Any) -> float | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        try:
            return max(-1.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            pass
        return {
            "STRONGLY OPPOSE": -1.0,
            "OPPOSE": -0.75,
            "AGAINST": -0.75,
            "NEGATIVE": -0.75,
            "DISAGREE": -0.75,
            "NEUTRAL": 0.0,
            "UNKNOWN": 0.0,
            "SUPPORT": 0.75,
            "FOR": 0.75,
            "POSITIVE": 0.75,
            "AGREE": 0.75,
            "STRONGLY SUPPORT": 1.0,
        }.get(normalized)

    def serialize(value: Any) -> Any:
        return value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value

    citations: list[dict[str, Any]] = []
    speaker_rows: dict[UUID, dict[str, Any]] = {}
    topic_ids: set[UUID] = set()
    inconsistency_ids: set[UUID] = set()
    invalid_rows: list[dict[str, str]] = []
    seen_citation_topics: set[tuple[UUID, UUID]] = set()
    topic_names_by_citation: dict[UUID, set[str]] = {}
    for row in rows:
        person, topic, citation, article, published_at, attitude, inconsistency = row[:7]
        citation_topic = (citation.citation_uuid, topic.topic_uuid)
        if citation_topic in seen_citation_topics:
            continue
        seen_citation_topics.add(citation_topic)
        topic_similarity = float(row[7]) if len(row) > 7 and row[7] is not None else None
        stance_value = numeric_stance(attitude.stance)
        if not person.name.strip():
            invalid_rows.append({"citation_uuid": serialize(citation.citation_uuid), "reason": "speaker name is empty"})
            continue
        if not topic.topic_name.strip():
            invalid_rows.append({"citation_uuid": serialize(citation.citation_uuid), "reason": "topic name is empty"})
            continue
        if stance_value is None or not math.isfinite(stance_value):
            invalid_rows.append({"citation_uuid": serialize(citation.citation_uuid), "reason": "stance is missing or not numeric"})
            continue
        if published_at is None:
            invalid_rows.append({"citation_uuid": serialize(citation.citation_uuid), "reason": "publication date is missing"})
            continue
        topic_ids.add(topic.topic_uuid)
        topic_names_by_citation.setdefault(citation.citation_uuid, set()).add(topic.topic_name)
        if inconsistency and inconsistency.classification in {"CONTRADICTION", "POSITION_CHANGE"}:
            inconsistency_ids.add(inconsistency.inconsistency_uuid)
        speaker = speaker_rows.setdefault(
            person.person_uuid,
            {
                "person_uuid": serialize(person.person_uuid),
                "name": person.name,
                "image_url": person.image_url,
                "person_type": person.person_type,
                "affiliation": person.affiliation,
                "aliases": person.aliases or [],
                "citations": 0,
                "stances": [],
            },
        )
        speaker["citations"] += 1
        speaker["stances"].append(stance_value)
        if inconsistency and inconsistency.classification in {"CONTRADICTION", "POSITION_CHANGE"}:
            if inconsistency.citation_a_uuid == inconsistency.citation_b_uuid:
                invalid_rows.append({"citation_uuid": serialize(citation.citation_uuid), "reason": "inconsistency links the same citation"})
                continue
        citations.append(
            {
                "citation_uuid": serialize(citation.citation_uuid),
                "person_uuid": serialize(person.person_uuid),
                "person_name": person.name,
                "person_type": person.person_type,
                "topic_uuid": serialize(topic.topic_uuid),
                "topic_name": topic.topic_name,
                "general_topic_field": topic.general_topic_field,
                "published_at": serialize(published_at),
                "stance": attitude.stance,
                "stance_value": stance_value,
                "direction": attitude.direction,
                "endorsement": attitude.endorsement,
                "commitment": attitude.commitment,
                "conditionality": attitude.conditionality,
                "relevancy_score": attitude.relevancy_score,
                "stance_confidence": attitude.stance_confidence,
                "quote": citation.exact_quote or citation.summarized_quote,
                "summarized_quote": citation.summarized_quote,
                "article_title": article.article_title,
                "article_url": article.url,
                "topic_similarity": topic_similarity,
                "inconsistency": {
                    "id": serialize(inconsistency.inconsistency_uuid),
                    "classification": inconsistency.classification,
                    "severity": inconsistency.severity,
                    "comment": inconsistency.inconsistency_comment,
                    "citation_a_uuid": serialize(inconsistency.citation_a_uuid),
                    "citation_b_uuid": serialize(inconsistency.citation_b_uuid),
                } if inconsistency else None,
            }
        )
    for item in citations:
        item["topic_names"] = sorted(topic_names_by_citation.get(UUID(item["citation_uuid"]), {item["topic_name"]}))
    for speaker in speaker_rows.values():
        values = speaker.pop("stances")
        speaker["average_stance"] = sum(values) / len(values)
        speaker["std_stance"] = (sum((value - speaker["average_stance"]) ** 2 for value in values) / len(values)) ** 0.5
    speakers = sorted(speaker_rows.values(), key=lambda item: item["citations"], reverse=True)
    values = [item["stance_value"] for item in citations]
    values.sort()
    mean = sum(values) / len(values) if values else None
    median = values[len(values) // 2] if values and len(values) % 2 else ((values[len(values) // 2 - 1] + values[len(values) // 2]) / 2 if values else None)
    std = (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5 if values and mean is not None else None
    result = {
        "filters": {"phrase": phrase, "general_topic_field": general_topic_field, "min_similarity": min_similarity},
        "speakers": speakers,
        "citations": citations,
        "kpis": {"average_stance": mean, "median_stance": median, "std_stance": std, "inconsistency_frequency": len(inconsistency_ids) / len(citations) if citations else 0},
        "matched_topic_count": len(topic_ids),
        "general_topic_fields": [
            {"name": name, "article_count": count}
            for name, count in sorted(topic_fields.items(), key=lambda item: (-item[1], item[0].lower()))
        ],
        "data_quality": {
            "excluded_row_count": len(invalid_rows),
            "excluded_rows": invalid_rows[:50],
            "truncated": len(invalid_rows) > 50,
            "result_limit": limit,
            "results_truncated": len(rows) >= limit,
        },
    }
    logger.info(
        "visualization_query status=completed citations=%d speakers=%d topics=%d excluded_rows=%d duration_ms=%d",
        len(citations),
        len(speakers),
        len(topic_ids),
        len(invalid_rows),
        int((time.perf_counter() - started) * 1000),
    )
    emit_event(
        "visualization_query_completed",
        duration_ms=int((time.perf_counter() - started) * 1000),
        attributes={
            "citation_count": len(citations),
            "speaker_count": len(speakers),
            "excluded_row_count": len(invalid_rows),
        },
    )
    return result
