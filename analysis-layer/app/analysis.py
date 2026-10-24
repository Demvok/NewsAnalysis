from __future__ import annotations

import logging
import time
import os
from datetime import datetime
from uuid import UUID

import httpx
from . import LLM, models


logger = logging.getLogger(__name__)
STORAGE_URL = os.getenv("STORAGE_URL", "http://data-storage:8000").rstrip("/")
PERSON_MIN_SIMILARITY = float(os.getenv("PERSON_MIN_COSINE_SIMILARITY", "0.85"))
TOPIC_MIN_SIMILARITY = float(os.getenv("TOPIC_MIN_COSINE_SIMILARITY", "0.85"))
INCONSISTENCY_MIN_SIMILARITY = float(
    os.getenv("INCONSISTENCY_MIN_COSINE_SIMILARITY", "0.75")
)
INCONSISTENCY_TOP_K = int(os.getenv("INCONSISTENCY_TOP_K", "5"))


def _remove_empty_citations(
    payload: dict[str, object],
) -> tuple[dict[str, object], int, int]:
    """Remove unsupported citations and normalize bounded confidence values."""
    removed = 0
    normalized = 0
    cleaned = dict(payload)
    speakers = []
    for speaker in payload.get("speakers") or []:
        cleaned_speaker = dict(speaker)
        citations = []
        for citation in speaker.get("citations") or []:
            if not str(citation.get("exact_quote") or "").strip():
                removed += 1
                continue
            cleaned_citation = dict(citation)
            for field in ("confidence_level", "extraction_confidence"):
                value = cleaned_citation.get(field)
                if isinstance(value, (int, float)) and not 0 <= value <= 1:
                    if value <= 5:
                        cleaned_citation[field] = value / 5
                    normalized += 1
            citations.append(cleaned_citation)
        cleaned_speaker["citations"] = citations
        speakers.append(cleaned_speaker)
    cleaned["speakers"] = speakers
    return cleaned, removed, normalized


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

    started = time.perf_counter()
    try:
        response = LLM.extract_citations(
            article_title=article.article_title or "",
            article_content=article.article_content or "",
        )
        extraction_payload, removed_citations, normalized_confidences = (
            _remove_empty_citations(
                response.model_dump()
            )
        )
        if removed_citations:
            logger.warning(
                "analysis_stage=extract_citations removed_empty_citations=%d",
                removed_citations,
            )
        if normalized_confidences:
            logger.warning(
                "analysis_stage=extract_citations normalized_confidences=%d",
                normalized_confidences,
            )
        result = models.CitationExtractionResult.model_validate(extraction_payload)
        logger.info(
            "analysis_stage=extract_citations status=completed speakers=%d duration=%s",
            len(result.speakers),
            f"{time.perf_counter() - started:.3f}s",
        )
        return result
    except Exception:
        logger.exception(
            "analysis_stage=extract_citations status=failed duration=%s",
            f"{time.perf_counter() - started:.3f}s",
        )
        raise



def analyze_speaker_sentiment(speaker: dict):
    """
    Analyzes the sentiment of the given speaker's content.

    Args:
        speaker (dict): A dictionary containing the speaker's information and content.

    Returns:
        dict: A dictionary containing the sentiment analysis results.
    """
    started = time.perf_counter()
    try:
        result = LLM.sentiment_analysis(speaker=speaker)
        logger.info(
            "analysis_stage=sentiment_analysis status=completed duration=%s",
            f"{time.perf_counter() - started:.3f}s",
        )
        return result
    except Exception:
        logger.exception(
            "analysis_stage=sentiment_analysis status=failed duration=%s",
            f"{time.perf_counter() - started:.3f}s",
        )
        raise


def analyze_speaker_topics(speaker: models.SpeakerExtraction):
    """Evaluate topics and stance for all citations attributed to one speaker."""
    started = time.perf_counter()
    try:
        result = LLM.sentiment_analysis(speaker.model_dump())
        logger.info(
            "analysis_stage=topic_and_stance status=completed duration=%s",
            f"{time.perf_counter() - started:.3f}s",
        )
        return result
    except Exception:
        logger.exception(
            "analysis_stage=topic_and_stance status=failed duration=%s",
            f"{time.perf_counter() - started:.3f}s",
        )
        raise


def _resolve_person(person: dict[str, object]) -> dict[str, object]:
    search_text = "\n".join(
        [str(person["name"]), *(str(alias) for alias in person.get("aliases", []) or [])]
    )
    response = httpx.post(
        f"{STORAGE_URL}/people/search",
        json={"text": search_text},
        params={"limit": 1, "min_similarity": PERSON_MIN_SIMILARITY},
        timeout=30.0,
    )
    response.raise_for_status()
    matches = response.json()
    if matches:
        return matches[0]

    response = httpx.post(f"{STORAGE_URL}/people", json=person, timeout=30.0)
    response.raise_for_status()
    return response.json()


def _resolve_topic(topic: dict[str, object]) -> dict[str, object]:
    description = str(topic.get("topic_description") or topic["topic_name"])
    response = httpx.post(
        f"{STORAGE_URL}/topics/search/text",
        json={"text": description},
        params={"limit": 1, "min_similarity": TOPIC_MIN_SIMILARITY},
        timeout=30.0,
    )
    response.raise_for_status()
    matches = response.json()
    if matches:
        return matches[0]

    response = httpx.post(f"{STORAGE_URL}/topics", json=topic, timeout=30.0)
    response.raise_for_status()
    return response.json()


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

    resolved_people = [(person, _resolve_person(person)) for person in people]
    person_ids = {
        source["name"].strip().casefold(): resolved["person_uuid"]
        for source, resolved in resolved_people
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

    resolved_people = [(person, _resolve_person(person)) for person in people]
    person_ids = {
        source["name"].strip().casefold(): resolved["person_uuid"]
        for source, resolved in resolved_people
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
    current_citations = {
        citation["citation_uuid"]: citation.get("summarized_quote")
        or citation.get("exact_quote")
        or ""
        for citation in citations_response.json()
    }

    for speaker in result.speakers:
        logger.info(
            "analysis_stage=topic_and_stance status=started speaker=%s",
            speaker.speaker_name,
        )
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

        resolved_topics = [(topic, _resolve_topic(topic)) for topic in topics]
        topic_ids = {
            source["topic_name"].strip().casefold(): resolved["topic_uuid"]
            for source, resolved in resolved_topics
        }
        attitudes_by_key: dict[tuple[str, str], dict[str, object]] = {}
        for citation in evaluation.evaluated_citations:
            citation_uuid = citation_ids.get(citation.exact_quote)
            if citation_uuid is None:
                continue
            for topic in citation.evaluated_topics:
                topic_uuid = topic_ids.get(topic.topic_name.strip().casefold())
                if topic_uuid is None:
                    continue
                key = (str(topic_uuid), str(citation_uuid))
                attitudes_by_key[key] = {
                    "topic_uuid": topic_uuid,
                    "citation_uuid": citation_uuid,
                    "stance": str(topic.stance) if topic.stance is not None else None,
                    "relevancy_score": topic.relevancy_score,
                    "stance_summary": topic.topic_description,
                }
        attitudes = list(attitudes_by_key.values())
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
            current_citations=current_citations,
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
        current_quote = current_citations.get(citation_uuid, "")
        search_response = httpx.post(
            f"{STORAGE_URL}/citations/search/text",
            json={"text": current_quote},
            params={
                "person_uuid": person_uuid,
                "before": before.isoformat() if before else None,
                "window_days": window_days,
                "exclude_citation_uuid": citation_uuid,
                "limit": INCONSISTENCY_TOP_K,
                "min_similarity": INCONSISTENCY_MIN_SIMILARITY,
            },
            timeout=30.0,
        )
        search_response.raise_for_status()
        for historical in search_response.json():
            logger.info(
                "analysis_stage=inconsistency_comparison status=started "
                "person_uuid=%s topic_uuid=%s",
                person_uuid,
                topic_uuid,
            )
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
