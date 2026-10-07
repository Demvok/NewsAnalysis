from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.s_scope.config import settings
from app.s_scope.ingestion.pipeline import IngestionPipeline
from app.s_scope.ingestion.sources import DEFAULT_FEED_SOURCES, FeedSource
from app.s_scope.models.article import Article
from app.s_scope.storage.database import DatabaseManager

app = FastAPI(
    title="NewsAnalysis-DataIngestion",
    description="Full API for NewsAnalysis data ingestion",
    version="0.0.1"
)

logger = logging.getLogger("data-ingestion")


class IngestionRequest(BaseModel):
    target_count: int = Field(default=500, ge=1, le=10_000)
    output_json: str = "data/articles_500.json"
    output_index: str = "data/articles_index.json"
    db_url: Optional[str] = None
    concurrency: int = Field(default=settings.ingestion_concurrency, ge=1, le=100)
    timeout: float = Field(default=settings.request_timeout, gt=0, le=300)
    metadata_only: bool = False


class IngestionStatus(BaseModel):
    job_id: str
    status: str
    article_count: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


jobs: dict[str, IngestionStatus] = {}
job_tasks: dict[str, asyncio.Task] = {}


def _set_job(job_id: str, **changes: object) -> None:
    jobs[job_id] = jobs[job_id].model_copy(update=changes)


async def _run_ingestion(job_id: str, request: IngestionRequest) -> None:
    _set_job(job_id, status="running", started_at=datetime.now(timezone.utc))
    try:
        pipeline = IngestionPipeline(
            concurrency=request.concurrency,
            timeout=request.timeout,
            min_content_length=settings.min_content_length,
        )
        articles = await pipeline.run(
            target_count=request.target_count,
            output_json=request.output_json,
            output_index=request.output_index,
            db_url=None if request.metadata_only else (request.db_url or settings.database_url),
            metadata_only=request.metadata_only,
        )
        _set_job(
            job_id,
            status="completed",
            article_count=len(articles),
            finished_at=datetime.now(timezone.utc),
        )
    except Exception as error:
        logger.exception("Ingestion job %s failed", job_id)
        _set_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error=str(error),
        )
    finally:
        job_tasks.pop(job_id, None)


def _database() -> DatabaseManager:
    try:
        return DatabaseManager()
    except Exception as error:
        logger.exception("Could not connect to the article database")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Database unavailable: {error}") from error


@app.get("/", summary="API guide", tags=["discovery"])
async def api_guide():
    return {
        "message": "NewsAnalysis data-ingestion API",
        "interactive_docs": "/docs",
        "openapi_schema": "/openapi.json",
        "next_steps": ["GET /sources", "POST /ingest", "GET /ingest/{job_id}", "GET /articles"],
    }


@app.get("/health", summary="Health check", tags=["discovery"])
async def root():
    database_status = "unknown"
    try:
        _database()
        database_status = "ok"
    except HTTPException:
        database_status = "unavailable"
    return {"message": "Data Ingestion API Server is running", "version": app.version, "database": database_status}


@app.get("/config", summary="Show non-secret configuration", tags=["discovery"])
async def get_config():
    return {
        "database_configured": bool(settings.database_url),
        "ingestion_concurrency": settings.ingestion_concurrency,
        "request_timeout": settings.request_timeout,
        "min_content_length": settings.min_content_length,
        "default_database": "configured by DATABASE_URL",
    }


@app.get("/sources", response_model=list[FeedSource], summary="List RSS sources", tags=["discovery"])
async def get_sources():
    return DEFAULT_FEED_SOURCES


@app.post("/ingest", response_model=IngestionStatus, status_code=status.HTTP_202_ACCEPTED, summary="Start an ingestion job", tags=["ingestion"])
async def start_ingestion(request: IngestionRequest):
    if any(job.status in {"queued", "running"} for job in jobs.values()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An ingestion job is already running")

    job_id = str(uuid4())
    jobs[job_id] = IngestionStatus(job_id=job_id, status="queued")
    job_tasks[job_id] = asyncio.create_task(_run_ingestion(job_id, request))
    return jobs[job_id]


@app.get("/ingest", response_model=list[IngestionStatus], summary="List ingestion jobs", tags=["ingestion"])
async def list_ingestion_jobs():
    return list(jobs.values())


@app.get("/ingest/{job_id}", response_model=IngestionStatus, summary="Read ingestion job status", tags=["ingestion"])
async def get_ingestion_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion job not found")
    return job


@app.delete("/ingest/{job_id}", response_model=IngestionStatus, summary="Cancel a queued or running job", tags=["ingestion"])
async def cancel_ingestion(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion job not found")
    task = job_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        _set_job(job_id, status="cancelled", finished_at=datetime.now(timezone.utc))
    return jobs[job_id]


@app.get("/articles", response_model=list[Article], summary="List stored articles", tags=["articles"])
async def list_articles(limit: int = Query(default=50, ge=1, le=500)):
    return _database().get_all_articles(limit=limit)


@app.get("/articles/{article_id}", response_model=Article, summary="Read one stored article", tags=["articles"])
async def get_article(article_id: str):
    article = _database().get_article(article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    return article