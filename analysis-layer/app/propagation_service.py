from __future__ import annotations

import httpx
import os
import os

from . import models
from .topic_propagation import (
    MIN_RELATION_POLARITY,
    MIN_TOPIC_RELEVANCE,
    calculate_topic_relation,
    propagate_stance,
    topic_propagation_cache,
)


def _storage_topics(topic_ids: list[str]) -> dict[str, dict[str, object]]:
    response = httpx.post(
        f"{os.environ.get('STORAGE_URL', 'http://data-storage:8000').rstrip('/')}/topics/embeddings",
        json={"topic_uuids": topic_ids, "generate_missing": True},
        timeout=STORAGE_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    topics = response.json()
    if not isinstance(topics, list):
        raise ValueError("Storage API returned an invalid topic embedding response")
    return {
        str(topic["topic_uuid"]): topic
        for topic in topics
        if isinstance(topic, dict) and topic.get("topic_uuid")
    }


def _embedding(value: object, topic_id: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Topic {topic_id} has no usable embedding")
    return [float(item) for item in value]


def _anchor_embeddings(topic_id: str, description: str) -> tuple[list[float], list[float]]:
    cache_key = (topic_id, description)
    cached = topic_propagation_cache.get_anchors(cache_key)
    if cached is not None:
        return cached
    storage_url = os.environ.get("STORAGE_URL", "http://data-storage:8000").rstrip("/")
    response = httpx.post(
        f"{storage_url}/vectors/embed-batch",
        json={
            "texts": [
                f"A position or policy that supports, advances, or promotes {description}.",
                f"A position or policy that opposes, restricts, or undermines {description}.",
            ]
        },
        timeout=STORAGE_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    embeddings = response.json().get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != 2:
        raise ValueError("Storage API returned invalid topic anchor embeddings")
    anchors = (_embedding(embeddings[0], topic_id), _embedding(embeddings[1], topic_id))
    topic_propagation_cache.set_anchors(cache_key, anchors)
    return anchors


def propagate_topic_stances(
    request: models.TopicPropagationRequest,
) -> list[models.TopicPropagationResult]:
    target_id = str(request.target_topic_uuid)
    topic_ids = list(dict.fromkeys(
        [target_id, *(str(citation.citation_topic_uuid) for citation in request.citations)]
    ))
    topics = _storage_topics(topic_ids)
    target = topics.get(target_id)
    if target is None:
        raise ValueError("Target topic was not found")
    target_embedding = _embedding(target.get("embedding"), target_id)
    description = str(target.get("topic_description") or target.get("topic_name") or "").strip()
    if not description:
        raise ValueError("Target topic has no description")
    positive_anchor, negative_anchor = _anchor_embeddings(target_id, description)
    minimum_relevance = (
        MIN_TOPIC_RELEVANCE if request.minimum_relevance is None else request.minimum_relevance
    )
    minimum_polarity = (
        MIN_RELATION_POLARITY if request.minimum_polarity is None else request.minimum_polarity
    )
    results: list[models.TopicPropagationResult] = []
    for citation in request.citations:
        citation_topic_id = str(citation.citation_topic_uuid)
        citation_topic = topics.get(citation_topic_id)
        if citation_topic is None:
            raise ValueError(f"Citation topic {citation_topic_id} was not found")
        pair_key = (citation_topic_id, target_id)
        relation = topic_propagation_cache.get_relation(pair_key)
        if relation is None:
            relation = calculate_topic_relation(
                _embedding(citation_topic.get("embedding"), citation_topic_id),
                target_embedding,
                positive_anchor,
                negative_anchor,
            )
            topic_propagation_cache.set_relation(pair_key, relation)
        propagated = propagate_stance(
            citation.original_stance,
            citation.original_stance_confidence,
            relation,
            minimum_relevance=minimum_relevance,
            minimum_polarity=minimum_polarity,
        )
        results.append(models.TopicPropagationResult(
            citation_uuid=citation.citation_uuid,
            citation_topic_uuid=citation.citation_topic_uuid,
            target_topic_uuid=request.target_topic_uuid,
            citation_topic_name=str(citation_topic.get("topic_name") or ""),
            target_topic_name=str(target.get("topic_name") or ""),
            original_stance=citation.original_stance,
            topic_relevance=propagated.relevance,
            topic_relation_polarity=propagated.polarity,
            transferred_stance=propagated.transferred_stance,
            transfer_confidence=propagated.transfer_confidence,
            reliable=propagated.reliable,
            status="RELIABLE" if propagated.reliable else "UNCERTAIN",
        ))
    return results
STORAGE_REQUEST_TIMEOUT = max(0.1, float(os.getenv("STORAGE_REQUEST_TIMEOUT_SECONDS", "30")))
