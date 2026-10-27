import httpx
import time
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


app = FastAPI(
    title="NewsAnalysis-AnalysisLayer",
    description="Full API for NewsAnalysis data analysis",
    version="0.1.0",
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


@app.get("/health", summary="Health check")
async def root():
    return {"message": "Analysis API Server is running", "version": app.version}


@app.post("/llm", summary="Test LLM connection")
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


@app.post(
    "/analysis/extraction",
    response_model=models.CitationExtractionResult,
    summary="Extract article citations",
)
def extract_article_citations(
    request: models.AnalysisRequest,
) -> models.CitationExtractionResult:
    with bind_context(article_id=request.article_id):
        result = extract_citations(_article_from_request(request))
    if request.article_id is not None:
        with bind_context(article_id=request.article_id):
            _run_persistence(persist_extraction, request.article_id, result)
    return result


@app.post(
    "/analysis/citation-analysis",
    response_model=models.CitationExtractionResult,
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
    summary="Extract and analyze article citations",
)
def analyze_citations(request: models.AnalysisRequest) -> models.CitationExtractionResult:
    with bind_context(article_id=request.article_id):
        result = extract_citations(_article_from_request(request))
    if request.article_id is not None:
        with bind_context(article_id=request.article_id):
            _run_persistence(
                persist_citation_analysis,
                request.article_id,
                result,
                request.published_at,
                request.inconsistency_window_days,
            )
    return result


def _run_persistence(callback, *args) -> None:
    try:
        callback(*args)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.exception("Failed to persist analytical data")
        raise HTTPException(
            status_code=502,
            detail="Storage API rejected extracted analytical data",
        ) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)