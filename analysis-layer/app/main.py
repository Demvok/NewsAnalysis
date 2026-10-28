import httpx
import time
from typing import Any
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request, Response

from app import models
from app.analysis import (
    extract_citations,
    persist_citation_analysis,
    persist_extraction,
)
from app.logger import get_logger
from app.telemetry import bind_context, emit_event


OPENAPI_TAGS = [
    {
        "name": "System",
        "description": "Service health and connectivity checks.",
    },
    {
        "name": "Stage 1 - Extraction",
        "description": "Extract citation and speaker candidates from article content.",
    },
    {
        "name": "Stage 2 - Extraction Persistence",
        "description": "Persist validated people and citation evidence.",
    },
    {
        "name": "Stage 3 - Citation Analysis",
        "description": "Resolve topics, classify attitudes, and compare historical positions.",
    },
    {
        "name": "Pipeline",
        "description": "Run the complete deterministic analysis pipeline.",
    },
    {
        "name": "Diagnostics",
        "description": "Development-only model connectivity diagnostics.",
    },
]


app = FastAPI(
    title="NewsAnalysis-AnalysisLayer",
    description="Full API for NewsAnalysis data analysis",
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS,
)

logger = get_logger("main", "analysis")


@app.middleware("http")
async def log_api_request(request: Request, call_next) -> Response:
    request_id = request.headers.get("x-request-id", str(uuid4()))
    job_id = request.headers.get("x-job-id")
    correlation_id = request.headers.get("x-correlation-id", request_id)
    started = time.perf_counter()
    logger.info("api_request status=started request_id=%s job_id=%s correlation_id=%s method=%s path=%s", request_id, job_id or "-", correlation_id, request.method, request.url.path)
    with bind_context(
        request_id=request_id,
        job_id=job_id,
        correlation_id=correlation_id,
        trace_id=correlation_id,
    ):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("api_request status=failed request_id=%s job_id=%s correlation_id=%s method=%s path=%s duration=%.3fs", request_id, job_id or "-", correlation_id, request.method, request.url.path, time.perf_counter() - started)
            emit_event("api_request_failed", operation=f"{request.method} {request.url.path}", event_status="failed", attributes={"path": request.url.path})
            raise
        level = logger.info if response.status_code < 400 else logger.warning
        duration = time.perf_counter() - started
        level("api_request status=completed request_id=%s job_id=%s correlation_id=%s method=%s path=%s status_code=%d duration=%.3fs", request_id, job_id or "-", correlation_id, request.method, request.url.path, response.status_code, duration)
        emit_event("api_request_completed", operation=f"{request.method} {request.url.path}", event_status="completed", duration_ms=int(duration * 1000), attributes={"path": request.url.path, "status_code": response.status_code})
    response.headers["x-request-id"] = request_id
    return response


@app.get("/health", tags=["System"], summary="Health check")
async def root():
    return {"message": "Analysis API Server is running", "version": app.version}


@app.post("/llm", tags=["Diagnostics"], summary="Test LLM connection")
def test_llm_connection(query: str):
    from app.LLM import llm_invoke

    response = llm_invoke(query)
    return {"message": "LLM connection is successful", "response": response}


def _article_from_request(request: models.AnalysisRequest) -> models.Article:
    return models.Article(
        article_id=request.article_id or "00000000-0000-0000-0000-000000000000",
        article_title=request.article_title,
        article_content=request.article_content,
        published_at=request.published_at,
    )


def _stage_is_complete(stage_status: str) -> bool:
    return stage_status.strip().upper() == "COMPLETED"


def _stage_response(
    stage: str,
    result: object = None,
    stats: dict[str, int] | None = None,
    status: str = "COMPLETED",
) -> models.StageResponse:
    return models.StageResponse(
        stage=stage,
        status=status,
        result=result,
        stats=models.StageStats.model_validate(stats or {}),
    )


@app.post(
    "/analysis/stages/extraction",
    response_model=models.StageResponse,
    tags=["Stage 1 - Extraction"],
    summary="Run citation extraction stage",
)
def run_extraction_stage(
    request: models.ExtractionStageRequest,
) -> models.StageResponse:
    if _stage_is_complete(request.stage_status):
        return _stage_response("citation_extraction", status="SKIPPED")
    result = extract_citations(_article_from_request(request.article))
    return _stage_response("citation_extraction", result=result)


@app.post(
    "/analysis/stages/extraction/bulk",
    response_model=models.BulkStageResponse,
    tags=["Stage 1 - Extraction"],
    summary="Run citation extraction stage in bulk",
)
def run_extraction_stage_bulk(
    requests: list[models.ExtractionStageRequest],
) -> models.BulkStageResponse:
    results = [run_extraction_stage(request) for request in requests]
    return models.BulkStageResponse(
        stage="citation_extraction",
        status="COMPLETED",
        results=results,
    )


@app.post(
    "/analysis/stages/extraction-persistence",
    response_model=models.StageResponse,
    tags=["Stage 2 - Extraction Persistence"],
    summary="Persist extracted people and citations",
)
def run_extraction_persistence_stage(
    request: models.ExtractionPersistenceRequest,
) -> models.StageResponse:
    if _stage_is_complete(request.stage_status):
        return _stage_response("extraction_persistence", status="SKIPPED")
    with bind_context(article_id=request.article_id):
        stats = _run_persistence(
            persist_extraction, request.article_id, request.extraction
        )
    return _stage_response("extraction_persistence", request.extraction, stats)


@app.post(
    "/analysis/stages/extraction-persistence/bulk",
    response_model=models.BulkStageResponse,
    tags=["Stage 2 - Extraction Persistence"],
    summary="Persist extracted people and citations in bulk",
)
def run_extraction_persistence_stage_bulk(
    requests: list[models.ExtractionPersistenceRequest],
) -> models.BulkStageResponse:
    results = [run_extraction_persistence_stage(request) for request in requests]
    return models.BulkStageResponse(
        stage="extraction_persistence",
        status="COMPLETED",
        results=results,
        stats=models.StageStats(
            created_new=sum(item.stats.created_new for item in results),
            retrieved_from_db=sum(item.stats.retrieved_from_db for item in results),
        ),
    )


@app.post(
    "/analysis/stages/citation-analysis",
    response_model=models.StageResponse,
    tags=["Stage 3 - Citation Analysis"],
    summary="Run topic, attitude, and inconsistency stages",
)
def run_citation_analysis_stage(
    request: models.CitationAnalysisStageRequest,
) -> models.StageResponse:
    if _stage_is_complete(request.stage_status):
        return _stage_response("citation_analysis", status="SKIPPED")
    analysis = request.analysis
    with bind_context(article_id=analysis.article_id):
        stats = _run_persistence(
            persist_citation_analysis,
            analysis.article_id,
            analysis.extraction,
            analysis.published_at,
            analysis.inconsistency_window_days,
        )
    return _stage_response("citation_analysis", analysis.extraction, stats)


@app.post(
    "/analysis/stages/citation-analysis/bulk",
    response_model=models.BulkStageResponse,
    tags=["Stage 3 - Citation Analysis"],
    summary="Run topic, attitude, and inconsistency stages in bulk",
)
def run_citation_analysis_stage_bulk(
    requests: list[models.CitationAnalysisStageRequest],
) -> models.BulkStageResponse:
    results = [run_citation_analysis_stage(request) for request in requests]
    return models.BulkStageResponse(
        stage="citation_analysis",
        status="COMPLETED",
        results=results,
        stats=models.StageStats(
            created_new=sum(item.stats.created_new for item in results),
            retrieved_from_db=sum(item.stats.retrieved_from_db for item in results),
        ),
    )


@app.post(
    "/analysis/stages/full",
    response_model=models.StageResponse,
    tags=["Pipeline"],
    summary="Run all analysis stages",
)
def run_full_stage(request: models.ExtractionStageRequest) -> models.StageResponse:
    if _stage_is_complete(request.stage_status):
        return _stage_response("full_analysis", status="SKIPPED")
    extraction = extract_citations(_article_from_request(request.article))
    stats = {}
    if request.article.article_id is not None:
        stats = _run_persistence(
            persist_citation_analysis,
            request.article.article_id,
            extraction,
            request.article.published_at,
            request.article.inconsistency_window_days,
        )
    return _stage_response("full_analysis", extraction, stats)


@app.post(
    "/analysis/stages/full/bulk",
    response_model=models.BulkStageResponse,
    tags=["Pipeline"],
    summary="Run all analysis stages in bulk",
)
def run_full_stage_bulk(
    requests: list[models.ExtractionStageRequest],
) -> models.BulkStageResponse:
    results = [run_full_stage(request) for request in requests]
    return models.BulkStageResponse(
        stage="full_analysis",
        status="COMPLETED",
        results=results,
        stats=models.StageStats(
            created_new=sum(item.stats.created_new for item in results),
            retrieved_from_db=sum(item.stats.retrieved_from_db for item in results),
        ),
    )


@app.post(
    "/analysis/extraction",
    response_model=models.CitationExtractionResult,
    tags=["Stage 1 - Extraction"],
    summary="Extract article citations",
)
def extract_article_citations(
    request: models.AnalysisRequest,
) -> models.CitationExtractionResult:
    stage = run_extraction_stage(
        models.ExtractionStageRequest(article=request)
    )
    result = models.CitationExtractionResult.model_validate(stage.result)
    if request.article_id is not None:
        with bind_context(article_id=request.article_id):
            _run_persistence(
                persist_extraction,
                request.article_id,
                result,
            )
    return result


@app.post(
    "/analysis/citation-analysis",
    response_model=models.CitationExtractionResult,
    tags=["Stage 3 - Citation Analysis"],
    summary="Analyze extracted citations",
)
def analyze_extracted_citations(
    request: models.CitationAnalysisRequest,
) -> models.CitationExtractionResult:
    with bind_context(article_id=request.article_id):
        _run_persistence(
            persist_citation_analysis,
            request.article_id,
            request.extraction,
            request.published_at,
            request.inconsistency_window_days,
        )
    return request.extraction


@app.post(
    "/analysis/citations",
    response_model=models.CitationExtractionResult,
    tags=["Pipeline"],
    summary="Extract and analyze article citations",
)
def analyze_citations(request: models.AnalysisRequest) -> models.CitationExtractionResult:
    stage = run_full_stage(
        models.ExtractionStageRequest(article=request)
    )
    return models.CitationExtractionResult.model_validate(stage.result)


def _run_persistence(callback, *args) -> Any:
    try:
        return callback(*args)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.exception("Failed to persist analytical data")
        raise HTTPException(
            status_code=502,
            detail="Storage API rejected extracted analytical data",
        ) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)