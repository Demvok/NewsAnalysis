from __future__ import annotations

import math
import os
from dataclasses import dataclass
from threading import RLock
from typing import Sequence


MIN_TOPIC_RELEVANCE = float(os.getenv("PROPAGATION_MIN_TOPIC_RELEVANCE", "0.55"))
MIN_RELATION_POLARITY = float(os.getenv("PROPAGATION_MIN_RELATION_POLARITY", "0.15"))


@dataclass(frozen=True)
class TopicRelation:
    relevance: float
    polarity: float


@dataclass(frozen=True)
class PropagatedStance:
    relevance: float
    polarity: float
    transferred_stance: float | None
    transfer_confidence: float | None
    reliable: bool


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        raise ValueError("embeddings must be non-empty and have equal dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("embeddings must not be zero vectors")
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)))


def calculate_topic_relation(
    citation_topic_embedding: Sequence[float],
    target_topic_embedding: Sequence[float],
    positive_anchor_embedding: Sequence[float],
    negative_anchor_embedding: Sequence[float],
) -> TopicRelation:
    relevance_cosine = cosine_similarity(citation_topic_embedding, target_topic_embedding)
    polarity = cosine_similarity(citation_topic_embedding, positive_anchor_embedding) - cosine_similarity(
        citation_topic_embedding, negative_anchor_embedding
    )
    return TopicRelation(
        relevance=(relevance_cosine + 1.0) / 2.0,
        polarity=max(-1.0, min(1.0, polarity)),
    )


def propagate_stance(
    original_stance: float | None,
    original_confidence: float | None,
    relation: TopicRelation,
    *,
    minimum_relevance: float = MIN_TOPIC_RELEVANCE,
    minimum_polarity: float = MIN_RELATION_POLARITY,
) -> PropagatedStance:
    confidence = (
        relation.relevance
        * abs(relation.polarity)
        * original_confidence
        if original_confidence is not None
        else None
    )
    reliable = (
        original_stance is not None
        and original_confidence is not None
        and relation.relevance >= minimum_relevance
        and abs(relation.polarity) >= minimum_polarity
    )
    return PropagatedStance(
        relevance=relation.relevance,
        polarity=relation.polarity,
        transferred_stance=(
            max(-1.0, min(1.0, original_stance * relation.polarity))
            if reliable
            else None
        ),
        transfer_confidence=confidence,
        reliable=reliable,
    )


class TopicPropagationCache:
    def __init__(self) -> None:
        self._lock = RLock()
        self._anchors: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
        self._relations: dict[tuple[str, str], TopicRelation] = {}

    def get_anchors(self, target_key: tuple[str, str]) -> tuple[list[float], list[float]] | None:
        with self._lock:
            return self._anchors.get(target_key)

    def set_anchors(
        self,
        target_key: tuple[str, str],
        anchors: tuple[list[float], list[float]],
    ) -> None:
        with self._lock:
            self._anchors[target_key] = anchors

    def get_relation(self, pair_key: tuple[str, str]) -> TopicRelation | None:
        with self._lock:
            return self._relations.get(pair_key)

    def set_relation(self, pair_key: tuple[str, str], relation: TopicRelation) -> None:
        with self._lock:
            self._relations[pair_key] = relation


topic_propagation_cache = TopicPropagationCache()
