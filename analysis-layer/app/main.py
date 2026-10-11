import logging

import httpx
from fastapi import FastAPI, HTTPException

from app import models
from app.analysis import (
    extract_citations,
    persist_citation_analysis,
    persist_extraction,
)


app = FastAPI(
    title="NewsAnalysis-AnalysisLayer",
    description="Full API for NewsAnalysis data analysis",
    version="0.1.0",
)

logger = logging.getLogger("data-analysis")


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
    result = extract_citations(_article_from_request(request))
    if request.article_id is not None:
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
    result = extract_citations(_article_from_request(request))
    if request.article_id is not None:
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