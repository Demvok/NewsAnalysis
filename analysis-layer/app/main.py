import logging
import os

import httpx
from fastapi import FastAPI, HTTPException

from app import models
from app.analysis import extract_citations

app = FastAPI(
    title="NewsAnalysis-AnalysisLayer",
    description="Full API for NewsAnalysis data analysis",
    version="0.0.2"
)

logger = logging.getLogger("data-analysis")
STORAGE_URL = os.getenv("STORAGE_URL", "http://data-storage:8000").rstrip("/")

@app.get("/health", summary="Health check")
async def root():
    return {"message": "Analysis API Server is running", "version": app.version}

@app.post("/llm", summary="Test LLM connection")
def test_llm_connection(query: str):
    from app.LLM import llm_invoke

    response = llm_invoke(query)
    return {"message": "LLM connection is successful", "response": response}


@app.post(
    "/analysis/citations",
    response_model=models.CitationExtractionResult,
    summary="Extract and validate article citations",
)
def analyze_citations(request: models.AnalysisRequest) -> models.CitationExtractionResult:
    article = models.Article(
        article_id=request.article_id or "00000000-0000-0000-0000-000000000000",
        article_title=request.article_title,
        article_content=request.article_content,
    )
    result = extract_citations(article)
    if request.article_id is not None:
        citations = [
            {
                "origin_article_id": str(request.article_id),
                "exact_quote": citation.exact_quote,
                "context": citation.context,
                "citation_type": citation.citation_type.value,
                "summarized_quote": citation.summarized_quote,
                "confidence_level": citation.confidence_level,
                "extraction_confidence": citation.extraction_confidence,
            }
            for speaker in result.speakers
            for citation in speaker.citations
        ]
        if citations:
            try:
                response = httpx.post(f"{STORAGE_URL}/citations/bulk", json=citations, timeout=30.0)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.exception("Failed to persist extracted citations for article %s", request.article_id)
                raise HTTPException(status_code=502, detail="Storage API rejected extracted citations") from exc
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)