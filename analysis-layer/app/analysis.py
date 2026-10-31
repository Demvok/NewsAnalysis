from __future__ import annotations

import time
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import UUID

import httpx
from . import LLM, models
from .logger import get_logger
from .telemetry import emit_event


logger = get_logger("analysis", "analysis")
STORAGE_URL = os.getenv("STORAGE_URL", "http://data-storage:8000").rstrip("/")
PERSON_MIN_SIMILARITY = float(os.getenv("PERSON_MIN_COSINE_SIMILARITY", "0.85"))
TOPIC_MIN_SIMILARITY = float(os.getenv("TOPIC_MIN_COSINE_SIMILARITY", "0.85"))
INCONSISTENCY_MIN_SIMILARITY = float(
    os.getenv("INCONSISTENCY_MIN_COSINE_SIMILARITY", "0.75")
)
INCONSISTENCY_TOP_K = int(os.getenv("INCONSISTENCY_TOP_K", "5"))
MIN_ATTRIBUTION_CONFIDENCE = float(os.getenv("MIN_ATTRIBUTION_CONFIDENCE", "0.35"))
MIN_EXTRACTION_CONFIDENCE = float(os.getenv("MIN_EXTRACTION_CONFIDENCE", "0.35"))
LOW_INFLUENCE_THRESHOLD = float(os.getenv("LOW_INFLUENCE_THRESHOLD", "0.35"))
ANALYZE_ORGANIZATIONS = os.getenv("ANALYZE_ORGANIZATIONS", "false").lower() in {
    "1", "true", "yes", "on"
}
PARALLEL_SPEAKER_ANALYSIS = os.getenv("PARALLEL_SPEAKER_ANALYSIS", "true").lower() in {
    "1", "true", "yes", "on"
}
PARALLEL_ANALYSIS_WORKERS = max(1, int(os.getenv("PARALLEL_ANALYSIS_WORKERS", "4")))

_VAGUE_SPEAKER_RE = re.compile(
    r"^(an?|some|other|those|people|experts?|industry sources?|"
    r"developers?|others?|the crowd|sources?|officials?)\b",
    re.IGNORECASE,
)
_ORGANIZATION_RE = re.compile(
    r"\b(government|administration|company|corporation|inc\.?|llc|"
    r"ministry|department|agency|organization|organisation|union|group)\b",
    re.IGNORECASE,
)
_COLLECTIVE_RE = re.compile(
    r"\b(the public|the people|people in|americans?|users?|consumers?|"
    r"residents?|citizens?|workers?|scientists?|researchers?|developers?|"
    r"voters?|the industry|the market|the crowd)\b",
    re.IGNORECASE,
)
_PERSON_NAME_RE = re.compile(r"^[A-Z][a-z'-]+(?:\s+[A-Z][a-z'.'-]+)+$")


def _speaker_kind(speaker: models.SpeakerExtraction) -> models.SpeakerKind:
    name = speaker.speaker_name.strip()
    role = speaker.speaker_role or ""
    if speaker.speaker_kind != models.SpeakerKind.UNRESOLVED:
        return speaker.speaker_kind
    if _VAGUE_SPEAKER_RE.search(name) or name.casefold() in {
        "others", "some people", "people in the us", "other developers"
    }:
        return models.SpeakerKind.ANONYMOUS_SOURCE
    if _ORGANIZATION_RE.search(f"{name} {role}"):
        return models.SpeakerKind.ORGANIZATION
    if _COLLECTIVE_RE.search(name):
        return models.SpeakerKind.GROUP
    if _PERSON_NAME_RE.fullmatch(name):
        return models.SpeakerKind.PERSON
    return models.SpeakerKind.UNRESOLVED


def _usable_citation(citation: models.CitationCandidate) -> bool:
    has_source = any(
        str(value or "").strip()
        for value in (citation.exact_quote, citation.context, citation.summarized_quote)
    )
    return (
        has_source
        and citation.confidence_level >= MIN_ATTRIBUTION_CONFIDENCE
        and citation.extraction_confidence >= MIN_EXTRACTION_CONFIDENCE
    )


def _prepare_speakers(
    speakers: list[models.SpeakerExtraction],
) -> list[models.SpeakerExtraction]:
    """Classify speakers and group low-impact or uncertain sources."""
    prepared: list[models.SpeakerExtraction] = []
    vague_citations: list[models.CitationCandidate] = []
    for speaker in speakers:
        kind = _speaker_kind(speaker)
        citations = [citation for citation in speaker.citations if _usable_citation(citation)]
        if not citations:
            continue
        if (
            kind in {
                models.SpeakerKind.ANONYMOUS_SOURCE,
                models.SpeakerKind.UNRESOLVED,
                models.SpeakerKind.GROUP,
            }
            or speaker.uncertain
            or speaker.influence_score < LOW_INFLUENCE_THRESHOLD
        ):
            vague_citations.extend(citations)
            continue
        prepared.append(
            speaker.model_copy(update={"speaker_kind": kind, "citations": citations})
        )
    if vague_citations:
        prepared.append(
            models.SpeakerExtraction(
                speaker_name="Vague and unresolved sources",
                speaker_role="Anonymous, collective, or unresolved source",
                speaker_kind=models.SpeakerKind.ANONYMOUS_SOURCE,
                influence_score=min(LOW_INFLUENCE_THRESHOLD, 0.5),
                citations=vague_citations,
            )
        )
    return prepared


def _analysis_speakers(
    speakers: list[models.SpeakerExtraction],
) -> list[models.SpeakerExtraction]:
    """Return only sources allowed to enter person/topic/attitude analysis."""
    return [
        speaker
        for speaker in speakers
        if speaker.speaker_kind == models.SpeakerKind.PERSON
        or (
            speaker.speaker_kind == models.SpeakerKind.ORGANIZATION
            and ANALYZE_ORGANIZATIONS
        )
    ]


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
            exact_quote = str(citation.get("exact_quote") or "")
            if not exact_quote.strip():
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


def _json_list_response(
    response: httpx.Response,
    *,
    expected_count: int | None = None,
    required_fields: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Validate storage JSON before analytical code relies on its identifiers."""
    payload = response.json()
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("Storage API returned an invalid list response")
    if expected_count is not None and len(payload) != expected_count:
        raise ValueError(
            f"Storage API returned {len(payload)} records for {expected_count} inputs"
        )
    for item in payload:
        if any(not str(item.get(field) or "").strip() for field in required_fields):
            raise ValueError("Storage API returned a record missing a required identifier")
    return payload


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
    emit_event(
        "pipeline_stage",
        operation="citation_extraction",
        stage="citation_extraction",
        event_status="started",
        attributes={"article_id": str(article.article_id)},
    )
    logger.info(
        "article_processing status=started article_id=%s stage=citation_extraction "
        "title_chars=%d content_chars=%d",
        article.article_id,
        len(article.article_title or ""),
        len(article.article_content or ""),
    )
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
        logger.info(
            "article_processing status=progress article_id=%s stage=citation_extraction "
            "speakers=%d citations=%d",
            article.article_id,
            len(result.speakers),
            sum(len(speaker.citations) for speaker in result.speakers),
        )
        LLM.log_usage_summary(f"article_id={article.article_id} stage=citation_extraction")
        emit_event(
            "pipeline_stage",
            operation="citation_extraction",
            stage="citation_extraction",
            event_status="completed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            attributes={
                "article_id": str(article.article_id),
                "speaker_count": len(result.speakers),
                "citation_count": sum(len(item.citations) for item in result.speakers),
            },
        )
        return result
    except Exception:
        logger.exception(
            "analysis_stage=extract_citations status=failed duration=%s",
            f"{time.perf_counter() - started:.3f}s",
        )
        emit_event(
            "pipeline_stage",
            operation="citation_extraction",
            stage="citation_extraction",
            event_status="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error_category="analysis_error",
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
    resolved, _ = _resolve_person_with_stats(person)
    return resolved


def _resolve_person_with_stats(
    person: dict[str, object],
) -> tuple[dict[str, object], bool]:
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
    matches = _json_list_response(response)
    if matches:
        if not matches[0].get("person_uuid"):
            raise ValueError("Storage API returned a person without person_uuid")
        return matches[0], False

    response = httpx.post(f"{STORAGE_URL}/people", json=person, timeout=30.0)
    response.raise_for_status()
    resolved = response.json()
    if not isinstance(resolved, dict) or not str(resolved.get("person_uuid") or "").strip():
        raise ValueError("Storage API returned a person without person_uuid")
    return resolved, True


def _resolve_topic(topic: dict[str, object]) -> dict[str, object]:
    resolved, _ = _resolve_topic_with_stats(topic)
    return resolved


def _resolve_topic_with_stats(
    topic: dict[str, object],
) -> tuple[dict[str, object], bool]:
    description = str(topic.get("topic_description") or topic["topic_name"])
    response = httpx.post(
        f"{STORAGE_URL}/topics/search/text",
        json={"text": description},
        params={"limit": 1, "min_similarity": TOPIC_MIN_SIMILARITY},
        timeout=30.0,
    )
    response.raise_for_status()
    matches = _json_list_response(response)
    if matches:
        if not matches[0].get("topic_uuid"):
            raise ValueError("Storage API returned a topic without topic_uuid")
        return matches[0], False

    response = httpx.post(f"{STORAGE_URL}/topics", json=topic, timeout=30.0)
    response.raise_for_status()
    resolved = response.json()
    if not isinstance(resolved, dict) or not str(resolved.get("topic_uuid") or "").strip():
        raise ValueError("Storage API returned a topic without topic_uuid")
    return resolved, True


def _bulk_stats(response: httpx.Response) -> dict[str, int]:
    return {
        "created_new": int(response.headers.get("x-db-created-new", "0")),
        "retrieved_from_db": int(response.headers.get("x-db-retrieved-from-db", "0")),
    }


def _add_stats(target: dict[str, int], values: dict[str, int]) -> None:
    target["created_new"] += values["created_new"]
    target["retrieved_from_db"] += values["retrieved_from_db"]


def persist_extraction(
    article_id: UUID, result: models.CitationExtractionResult
) -> dict[str, int]:
    """Persist extracted people and citations without running citation analysis."""
    speakers = _prepare_speakers(result.speakers)
    analysis_speakers = _analysis_speakers(speakers)
    people = [
        {
            "name": speaker.speaker_name.strip(),
            "affiliation": speaker.speaker_role or None,
            "person_type": speaker.speaker_kind.value,
            "aliases": speaker.aliases,
        }
        for speaker in analysis_speakers
        if speaker.speaker_name.strip()
    ]
    logger.info(
        "article_processing status=started article_id=%s stage=person_and_citation_persistence "
        "people=%d citations=%d",
        article_id,
        len(people),
        sum(len(speaker.citations) for speaker in speakers),
    )
    resolution_started = time.perf_counter()
    emit_event(
        "pipeline_stage",
        operation="person_resolution",
        stage="person_resolution",
        event_status="started",
        attributes={"article_id": str(article_id), "person_count": len(people)},
    )
    stats = {"created_new": 0, "retrieved_from_db": 0}
    resolved_people = []
    for person in people:
        resolved, created = _resolve_person_with_stats(person)
        resolved_people.append((person, resolved))
        stats["created_new" if created else "retrieved_from_db"] += 1
    logger.info("analysis_stage=person_resolution status=completed article_id=%s people=%d duration=%.3fs", article_id, len(resolved_people), time.perf_counter() - resolution_started)
    emit_event(
        "pipeline_stage",
        operation="person_resolution",
        stage="person_resolution",
        event_status="completed",
        duration_ms=int((time.perf_counter() - resolution_started) * 1000),
        attributes={
            "article_id": str(article_id),
            "person_count": len(resolved_people),
            "created_new": stats["created_new"],
            "retrieved_from_db": stats["retrieved_from_db"],
        },
    )
    person_ids = {
        source["name"].strip().casefold(): resolved["person_uuid"]
        for source, resolved in resolved_people
    }
    citations = [
        {
            "origin_article_id": str(article_id),
            "person_uuid": person_ids.get(speaker.speaker_name.strip().casefold()),
            "exact_quote": citation.exact_quote,
            "context": citation.context,
            "citation_type": citation.citation_type.value,
            "summarized_quote": citation.summarized_quote,
            "confidence_level": citation.confidence_level,
            "extraction_confidence": citation.extraction_confidence,
        }
        for speaker in analysis_speakers
        for citation in speaker.citations
    ]
    if citations:
        citations_response = httpx.post(
            f"{STORAGE_URL}/citations/bulk",
            json=citations,
            timeout=30.0,
        )
        citations_response.raise_for_status()
        _json_list_response(
            citations_response,
            expected_count=len(citations),
            required_fields=("citation_uuid",),
        )
        _add_stats(stats, _bulk_stats(citations_response))
    logger.info(
        "article_processing status=completed article_id=%s stage=person_and_citation_persistence "
        "people=%d citations=%d created_new=%d retrieved_from_db=%d duration=%.3fs",
        article_id,
        len(resolved_people),
        len(citations),
        stats["created_new"],
        stats["retrieved_from_db"],
        time.perf_counter() - resolution_started,
    )
    return stats


def persist_citation_analysis(
    article_id: UUID,
    result: models.CitationExtractionResult,
    before: datetime | None,
    window_days: int,
) -> dict[str, int]:
    """Analyze and persist topics, attitudes, and historical inconsistencies."""
    speakers = _prepare_speakers(result.speakers)
    analysis_speakers = _analysis_speakers(speakers)
    people = [
        {
            "name": speaker.speaker_name.strip(),
            "affiliation": speaker.speaker_role or None,
            "person_type": speaker.speaker_kind.value,
            "aliases": speaker.aliases,
        }
        for speaker in analysis_speakers
        if speaker.speaker_name.strip()
    ]
    workflow_started = time.perf_counter()
    total_citations = sum(len(speaker.citations) for speaker in speakers)
    logger.info(
        "article_processing status=started article_id=%s stage=citation_analysis "
        "people=%d citations=%d window_days=%d",
        article_id,
        len(people),
        total_citations,
        window_days,
    )
    person_started = time.perf_counter()
    emit_event(
        "pipeline_stage",
        operation="person_resolution",
        stage="person_resolution",
        event_status="started",
        attributes={"article_id": str(article_id), "person_count": len(people)},
    )
    stats = {"created_new": 0, "retrieved_from_db": 0}
    topic_total = 0
    attitude_total = 0
    comparison_total = 0
    resolved_people = []
    for person in people:
        resolved, created = _resolve_person_with_stats(person)
        resolved_people.append((person, resolved))
        stats["created_new" if created else "retrieved_from_db"] += 1
    person_ids = {
        source["name"].strip().casefold(): resolved["person_uuid"]
        for source, resolved in resolved_people
    }
    logger.info("analysis_stage=person_resolution status=completed article_id=%s people=%d duration=%.3fs", article_id, len(resolved_people), time.perf_counter() - person_started)
    emit_event(
        "pipeline_stage",
        operation="person_resolution",
        stage="person_resolution",
        event_status="completed",
        duration_ms=int((time.perf_counter() - person_started) * 1000),
        attributes={
            "article_id": str(article_id),
            "person_count": len(resolved_people),
            "created_new": stats["created_new"],
            "retrieved_from_db": stats["retrieved_from_db"],
        },
    )
    citations = [
        {
            "origin_article_id": str(article_id),
            "person_uuid": person_ids.get(speaker.speaker_name.strip().casefold()),
            "exact_quote": citation.exact_quote,
            "context": citation.context,
            "citation_type": citation.citation_type.value,
            "summarized_quote": citation.summarized_quote,
            "confidence_level": citation.confidence_level,
            "extraction_confidence": citation.extraction_confidence,
        }
        for speaker in speakers
        for citation in speaker.citations
    ]
    if not citations:
        logger.info("article_processing status=completed article_id=%s stage=citation_analysis people=%d citations=0 duration=%.3fs", article_id, len(people), time.perf_counter() - workflow_started)
        return stats

    citations_response = httpx.post(
        f"{STORAGE_URL}/citations/bulk",
        json=citations,
        timeout=30.0,
    )
    citations_response.raise_for_status()
    stored_citations = _json_list_response(
        citations_response,
        expected_count=len(citations),
        required_fields=("citation_uuid",),
    )
    _add_stats(stats, _bulk_stats(citations_response))
    citation_ids = {
        (str(citation.get("person_uuid")), citation["exact_quote"]): citation["citation_uuid"]
        for citation in stored_citations
        if citation.get("exact_quote") and citation.get("person_uuid")
    }
    current_citations = {
        citation["citation_uuid"]: citation.get("summarized_quote")
        or citation.get("exact_quote")
        or ""
        for citation in stored_citations
    }

    analysis_inputs = [
        speaker for speaker in analysis_speakers
        if speaker.citations
    ]
    if PARALLEL_SPEAKER_ANALYSIS and len(analysis_inputs) > 1:
        with ThreadPoolExecutor(max_workers=PARALLEL_ANALYSIS_WORKERS) as executor:
            evaluations = list(executor.map(analyze_speaker_topics, analysis_inputs))
    else:
        evaluations = [analyze_speaker_topics(speaker) for speaker in analysis_inputs]

    for speaker_index, (speaker, evaluation) in enumerate(
        zip(analysis_inputs, evaluations), start=1
    ):
        logger.info(
            "article_processing status=progress article_id=%s stage=topic_and_stance "
            "speaker_index=%d speakers_total=%d speaker=%s citations=%d",
            article_id,
            speaker_index,
            len(analysis_inputs),
            speaker.speaker_name,
            len(speaker.citations),
        )
        topics = [
            {
                "topic_name": topic.topic_name.strip(),
                "topic_description": topic.topic_description,
                "general_topic_field": topic.general_topic_field,
            }
            for citation in evaluation.evaluated_citations
            for topic in citation.evaluated_topics
            if (
                topic.topic_name.strip()
                and topic.stance_detected
                and topic.stance is not None
                and citation.citation_type != models.CitationType.MENTION
            )
        ]
        unique_topics: dict[str, dict[str, object]] = {}
        for topic in topics:
            unique_topics.setdefault(topic["topic_name"].casefold(), topic)
        topics = list(unique_topics.values())
        logger.info(
            "analysis_stage=topic_resolution status=started article_id=%s speaker=%s "
            "topic_candidates=%d",
            article_id,
            speaker.speaker_name,
            len(topics),
        )
        emit_event(
            "pipeline_stage",
            operation="topic_resolution",
            stage="topic_resolution",
            event_status="started",
            attributes={"article_id": str(article_id), "topic_candidates": len(topics)},
        )
        if not topics:
            logger.info("article_processing status=progress article_id=%s stage=topic_and_stance speaker_index=%d topics=0 attitudes=0", article_id, speaker_index)
            continue

        topic_started = time.perf_counter()
        resolved_topics = []
        for topic in topics:
            resolved, created = _resolve_topic_with_stats(topic)
            resolved_topics.append((topic, resolved))
            stats["created_new" if created else "retrieved_from_db"] += 1
        topic_total += len(resolved_topics)
        topic_ids = {
            source["topic_name"].strip().casefold(): resolved["topic_uuid"]
            for source, resolved in resolved_topics
        }
        canonical_topic_descriptions = {
            source["topic_name"].strip().casefold(): (
                resolved.get("topic_description")
                or source["topic_description"]
            )
            for source, resolved in resolved_topics
        }
        logger.info("analysis_stage=topic_resolution status=completed article_id=%s speaker=%s topics=%d duration=%.3fs", article_id, speaker.speaker_name, len(resolved_topics), time.perf_counter() - topic_started)
        emit_event(
            "pipeline_stage",
            operation="topic_resolution",
            stage="topic_resolution",
            event_status="completed",
            duration_ms=int((time.perf_counter() - topic_started) * 1000),
            attributes={"article_id": str(article_id), "topic_count": len(resolved_topics)},
        )
        attitudes_by_key: dict[tuple[str, str], dict[str, object]] = {}
        for citation in evaluation.evaluated_citations:
            citation_uuid = citation_ids.get(
                (str(person_ids[speaker.speaker_name.strip().casefold()]), citation.exact_quote)
            )
            if citation_uuid is None:
                continue
            for topic in citation.evaluated_topics:
                topic_uuid = topic_ids.get(topic.topic_name.strip().casefold())
                if topic_uuid is None:
                    continue
                key = (str(topic_uuid), str(citation_uuid))
                stance_summary = (topic.stance_summary or "").strip()
                attitudes_by_key[key] = {
                    "topic_uuid": topic_uuid,
                    "citation_uuid": citation_uuid,
                    "stance": str(topic.stance) if topic.stance is not None else None,
                    "relevancy_score": topic.relevancy_score,
                    "stance_confidence": topic.stance_confidence,
                    "stance_summary": stance_summary or canonical_topic_descriptions.get(
                        topic.topic_name.strip().casefold(),
                        topic.topic_description,
                    ),
                    "status": "ANALYZED",
                }
        attitudes = list(attitudes_by_key.values())
        attitude_total += len(attitudes)
        logger.info(
            "article_processing status=progress article_id=%s stage=attitude_persistence "
            "speaker_index=%d topics=%d attitudes=%d",
            article_id,
            speaker_index,
            len(resolved_topics),
            len(attitudes),
        )
        if not attitudes:
            continue

        attitude_started = time.perf_counter()
        emit_event(
            "pipeline_stage",
            operation="attitude_persistence",
            stage="attitude_persistence",
            event_status="started",
            attributes={"article_id": str(article_id), "attitude_count": len(attitudes)},
        )
        attitudes_response = httpx.post(
            f"{STORAGE_URL}/attitudes/bulk",
            json=attitudes,
            timeout=30.0,
        )
        attitudes_response.raise_for_status()
        _add_stats(stats, _bulk_stats(attitudes_response))
        emit_event(
            "pipeline_stage",
            operation="attitude_persistence",
            stage="attitude_persistence",
            event_status="completed",
            duration_ms=int((time.perf_counter() - attitude_started) * 1000),
            attributes={"article_id": str(article_id), "attitude_count": len(attitudes)},
        )
        inconsistency_stats = persist_inconsistencies(
            person_uuid=person_ids[speaker.speaker_name.strip().casefold()],
            speaker_kind=speaker.speaker_kind,
            current_citations=current_citations,
            topic_ids=topic_ids,
            topics=topics,
            attitudes=attitudes,
            before=before,
            window_days=window_days,
        )
        _add_stats(stats, inconsistency_stats)
        comparison_total += inconsistency_stats.get("comparison_count", 0)
    logger.info(
        "article_processing status=completed article_id=%s stage=citation_analysis "
        "people=%d citations=%d created_new=%d retrieved_from_db=%d duration=%.3fs",
        article_id,
        len(people),
        len(citations),
        stats["created_new"],
        stats["retrieved_from_db"],
        time.perf_counter() - workflow_started,
    )
    emit_event(
        "pipeline_stage",
        operation="citation_analysis",
        stage="citation_analysis",
        event_status="completed",
        duration_ms=int((time.perf_counter() - workflow_started) * 1000),
        attributes={
            "article_id": str(article_id),
            "person_count": len(resolved_people),
            "citation_count": len(citations),
            "topic_count": topic_total,
            "attitude_count": attitude_total,
            "comparison_count": comparison_total,
            "created_new": stats["created_new"],
            "retrieved_from_db": stats["retrieved_from_db"],
        },
    )
    LLM.log_usage_summary(f"article_id={article_id} stage=citation_analysis")
    return stats


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
    speaker_kind: models.SpeakerKind,
    current_citations: dict[str, str],
    topic_ids: dict[str, str],
    topics: list[dict[str, object]],
    attitudes: list[dict[str, object]],
    before: datetime | None,
    window_days: int,
) -> dict[str, int]:
    stats = {"created_new": 0, "retrieved_from_db": 0, "comparison_count": 0}
    if speaker_kind in {
        models.SpeakerKind.ANONYMOUS_SOURCE,
        models.SpeakerKind.UNRESOLVED,
        models.SpeakerKind.GROUP,
    }:
        return stats
    topic_names = {
        topic["topic_name"].strip().casefold(): topic["topic_name"]
        for topic in topics
    }
    comparison_started = time.perf_counter()
    emit_event(
        "pipeline_stage",
        operation="inconsistency_comparison",
        stage="inconsistency_comparison",
        event_status="started",
        attributes={"person_uuid": person_uuid, "attitude_count": len(attitudes)},
    )
    comparisons = 0
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
        if not current_quote.strip() or attitude.get("stance") is None:
            continue
        search_response = httpx.post(
            f"{STORAGE_URL}/citations/search/text",
            json={"text": current_quote},
            params={
                "person_uuid": person_uuid,
                "topic_uuid": topic_uuid,
                "before": before.isoformat() if before else None,
                "window_days": window_days,
                "exclude_citation_uuid": citation_uuid,
                "limit": INCONSISTENCY_TOP_K,
                "min_similarity": INCONSISTENCY_MIN_SIMILARITY,
            },
            timeout=30.0,
        )
        search_response.raise_for_status()
        comparisons += 1
        for historical in search_response.json():
            historical_quote = (
                historical.get("exact_quote")
                or historical.get("summarized_quote")
                or ""
            ).strip()
            if (
                not historical_quote
                or historical.get("citation_uuid") == citation_uuid
                or historical_quote.casefold() == current_quote.casefold()
                or historical.get("citation_type") == models.CitationType.MENTION.value
                or historical.get("cosine_similarity", 0) < INCONSISTENCY_MIN_SIMILARITY
                or historical.get("stance") is None
            ):
                continue
            logger.info(
                "analysis_stage=inconsistency_comparison status=started "
                "person_uuid=%s topic_uuid=%s",
                person_uuid,
                topic_uuid,
            )
            comparison = LLM.compare_attitudes(
                topic_name=topic_name,
                earlier=historical_quote,
                earlier_stance=historical.get("stance"),
                newer=current_quote,
                newer_stance=str(attitude.get("stance"))
                if attitude.get("stance") is not None
                else None,
                speaker=person_uuid,
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
            if response.is_error:
                logger.error(
                    "analysis_stage=inconsistency_persistence status=rejected "
                    "person_uuid=%s topic_uuid=%s citation_a_uuid=%s citation_b_uuid=%s "
                    "status_code=%d detail=%s",
                    person_uuid,
                    topic_uuid,
                    historical["citation_uuid"],
                    citation_uuid,
                    response.status_code,
                    response.text[:1000],
                )
            response.raise_for_status()
            _add_stats(stats, _bulk_stats(response))
    stats["comparison_count"] = comparisons
    emit_event(
        "pipeline_stage",
        operation="inconsistency_comparison",
        stage="inconsistency_comparison",
        event_status="completed",
        duration_ms=int((time.perf_counter() - comparison_started) * 1000),
        attributes={"person_uuid": person_uuid, "comparison_count": comparisons},
    )
    return stats
