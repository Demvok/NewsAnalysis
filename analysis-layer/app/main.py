import logging
import os
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException

from app import models
from app.analysis import analyze_speaker_topics, extract_citations

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
        _persist_extraction(request.article_id, result)
    return result


def _persist_extraction(article_id: UUID, result: models.CitationExtractionResult) -> None:
    people = [
        {
            "name": speaker.speaker_name.strip(),
            "affiliation": speaker.speaker_role or None,
            "aliases": speaker.aliases,
        }
        for speaker in result.speakers
        if speaker.speaker_name.strip()
    ]
    if not people:
        return
    try:
        people_response = httpx.post(f"{STORAGE_URL}/people/bulk", json=people, timeout=30.0)
        people_response.raise_for_status()
        persisted_people = people_response.json()
        person_ids = {
            person["name"].strip().casefold(): person["person_uuid"]
            for person in persisted_people
        }
        citations = [
            {
                "origin_article_id": str(article_id),
                "person_uuid": person_ids[speaker.speaker_name.strip().casefold()],
                "exact_quote": citation.exact_quote,
                "context": citation.context,
                "citation_type": citation.citation_type.value,
                "summarized_quote": citation.summarized_quote,
                "confidence_level": citation.confidence_level,
                "extraction_confidence": citation.extraction_confidence,
            }
            for speaker in result.speakers
            for citation in speaker.citations
            if speaker.speaker_name.strip().casefold() in person_ids
        ]
        if not citations:
            return
        citations_response = httpx.post(
            f"{STORAGE_URL}/citations/bulk",
            json=citations,
            timeout=30.0,
        )
        citations_response.raise_for_status()
        persisted_citations = citations_response.json()
        citation_ids = {
            citation["exact_quote"]: citation["citation_uuid"]
            for citation in persisted_citations
            if citation.get("exact_quote")
        }
        for speaker in result.speakers:
            evaluation = analyze_speaker_topics(speaker)
            topics = [
                {
                    "topic_name": topic.topic_name.strip(),
                    "topic_description": topic.topic_description,
                }
                for citation in evaluation.evaluated_citations
                for topic in citation.evaluated_topics
                if topic.topic_name.strip() and topic.stance_detected
            ]
            if not topics:
                continue
            topics_response = httpx.post(f"{STORAGE_URL}/topics/bulk", json=topics, timeout=30.0)
            topics_response.raise_for_status()
            topic_ids = {
                topic["topic_name"].strip().casefold(): topic["topic_uuid"]
                for topic in topics_response.json()
            }
            attitudes = [
                {
                    "topic_uuid": topic_ids[topic.topic_name.strip().casefold()],
                    "citation_uuid": citation_ids[citation.exact_quote],
                    "stance": str(topic.stance) if topic.stance is not None else None,
                    "relevancy_score": topic.relevancy_score,
                    "stance_summary": topic.topic_description,
                }
                for citation in evaluation.evaluated_citations
                if citation.exact_quote in citation_ids
                for topic in citation.evaluated_topics
                if topic.topic_name.strip().casefold() in topic_ids
            ]
            if attitudes:
                attitudes_response = httpx.post(
                    f"{STORAGE_URL}/attitudes/bulk",
                    json=attitudes,
                    timeout=30.0,
                )
                attitudes_response.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.exception("Failed to persist extraction for article %s", article_id)
        raise HTTPException(status_code=502, detail="Storage API rejected extracted analytical data") from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)