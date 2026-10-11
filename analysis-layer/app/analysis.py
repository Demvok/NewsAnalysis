from __future__ import annotations

import logging
import os
from datetime import datetime
from uuid import UUID

import httpx
from . import LLM, models


logger = logging.getLogger(__name__)
STORAGE_URL = os.getenv("STORAGE_URL", "http://data-storage:8000").rstrip("/")

# Persistence belongs to the analysis workflow so extraction and citation analysis
# can be executed independently while still sharing the same storage behavior.

def extract_citations(article: models.Article) -> models.CitationExtractionResult:
    """
    Extracts citations from the given article title and content.

    Args:
        article (Article): An Article object containing the article's title and content.

    Returns:
        list: A list of extracted citations.
    """
    if not article:
        raise ValueError("article is required")

    response = LLM.extract_citations(
        article_title=article.article_title or "",
        article_content=article.article_content or "",
    )
    result = models.CitationExtractionResult.model_validate(response.model_dump())
    logger.info("Extracted %d speakers", len(result.speakers))
    return result



def analyze_speaker_sentiment(speaker: dict):
    """
    Analyzes the sentiment of the given speaker's content.

    Args:
        speaker (dict): A dictionary containing the speaker's information and content.

    Returns:
        dict: A dictionary containing the sentiment analysis results.
    """
    return LLM.sentiment_analysis(speaker=speaker)


def analyze_speaker_topics(speaker: models.SpeakerExtraction):
    """Evaluate topics and stance for all citations attributed to one speaker."""
    return LLM.sentiment_analysis(speaker.model_dump())


def persist_extraction(article_id: UUID, result: models.CitationExtractionResult) -> None:
    """Persist extracted people and citations without running citation analysis."""
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
    if citations:
        citations_response = httpx.post(
            f"{STORAGE_URL}/citations/bulk",
            json=citations,
            timeout=30.0,
        )
        citations_response.raise_for_status()


def persist_citation_analysis(
    article_id: UUID,
    result: models.CitationExtractionResult,
    before: datetime | None,
    window_days: int,
) -> None:
    """Analyze and persist topics, attitudes, and historical inconsistencies."""
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

    people_response = httpx.post(f"{STORAGE_URL}/people/bulk", json=people, timeout=30.0)
    people_response.raise_for_status()
    person_ids = {
        person["name"].strip().casefold(): person["person_uuid"]
        for person in people_response.json()
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
    citation_ids = {
        citation["exact_quote"]: citation["citation_uuid"]
        for citation in citations_response.json()
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
        if not attitudes:
            continue

        attitudes_response = httpx.post(
            f"{STORAGE_URL}/attitudes/bulk",
            json=attitudes,
            timeout=30.0,
        )
        attitudes_response.raise_for_status()
        persist_inconsistencies(
            person_uuid=person_ids[speaker.speaker_name.strip().casefold()],
            current_citations=citation_ids,
            topic_ids=topic_ids,
            topics=topics,
            attitudes=attitudes,
            before=before,
            window_days=window_days,
        )


def persist_full(
    article_id: UUID,
    result: models.CitationExtractionResult,
    before: datetime | None,
    window_days: int,
) -> None:
    """Persist extraction and citation analysis as one compatibility workflow."""
    persist_citation_analysis(article_id, result, before, window_days)


def persist_inconsistencies(
    person_uuid: str,
    current_citations: dict[str, str],
    topic_ids: dict[str, str],
    topics: list[dict[str, object]],
    attitudes: list[dict[str, object]],
    before: datetime | None,
    window_days: int,
) -> None:
    topic_names = {
        topic["topic_name"].strip().casefold(): topic["topic_name"]
        for topic in topics
    }
    for attitude in attitudes:
        topic_uuid = str(attitude["topic_uuid"])
        topic_name = next(
            (name for key, name in topic_names.items() if topic_ids.get(key) == topic_uuid),
            None,
        )
        citation_uuid = str(attitude["citation_uuid"])
        if not topic_name:
            continue
        history_response = httpx.get(
            f"{STORAGE_URL}/attitudes/history",
            params={
                "person_uuid": person_uuid,
                "topic_uuid": topic_uuid,
                "before": before.isoformat() if before else None,
                "window_days": window_days,
                "exclude_citation_uuid": citation_uuid,
            },
            timeout=30.0,
        )
        history_response.raise_for_status()
        current_quote = next(
            (quote for quote, value in current_citations.items() if value == citation_uuid),
            "",
        )
        for historical in history_response.json():
            comparison = LLM.compare_attitudes(
                topic_name=topic_name,
                earlier=historical.get("exact_quote") or historical.get("summarized_quote") or "",
                earlier_stance=historical.get("stance"),
                newer=current_quote,
                newer_stance=str(attitude.get("stance"))
                if attitude.get("stance") is not None
                else None,
            )
            response = httpx.post(
                f"{STORAGE_URL}/inconsistencies",
                json={
                    "person_uuid": person_uuid,
                    "topic_uuid": topic_uuid,
                    "citation_a_uuid": historical["citation_uuid"],
                    "citation_b_uuid": citation_uuid,
                    "attitude_a": historical.get("stance"),
                    "attitude_b": attitude.get("stance"),
                    "classification": comparison.classification,
                    "severity": comparison.severity,
                    "confidence": comparison.confidence,
                    "inconsistency_comment": comparison.comment,
                },
                timeout=30.0,
            )
            response.raise_for_status()
