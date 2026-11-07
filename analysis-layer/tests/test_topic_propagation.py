import pytest

from app.topic_propagation import (
    TopicPropagationCache,
    TopicRelation,
    calculate_topic_relation,
    propagate_stance,
)


def propagated(stance, polarity, relevance=0.9, confidence=0.8):
    return propagate_stance(
        stance,
        confidence,
        TopicRelation(relevance=relevance, polarity=polarity),
        minimum_relevance=0.5,
        minimum_polarity=0.15,
    )


@pytest.mark.parametrize(
    ("stance", "polarity", "expected"),
    [(0.8, 0.8, 0.64), (0.8, -0.8, -0.64), (-0.8, 0.8, -0.64), (-0.8, -0.8, 0.64)],
)
def test_stance_transfers_with_relation_direction(stance, polarity, expected):
    assert propagated(stance, polarity).transferred_stance == pytest.approx(expected)


def test_topic_relation_normalizes_relevance_and_bounds_polarity():
    relation = calculate_topic_relation(
        [1.0, 0.0],
        [1.0, 0.0],
        [1.0, 0.0],
        [-1.0, 0.0],
    )
    assert relation.relevance == pytest.approx(1.0)
    assert relation.polarity == 1.0


def test_ambiguous_polarity_is_uncertain():
    result = propagated(0.8, 0.05)
    assert result.transferred_stance is None
    assert not result.reliable
    assert result.transfer_confidence == pytest.approx(0.036)


def test_low_relevance_is_uncertain_even_with_strong_polarity():
    result = propagated(0.8, 0.8, relevance=0.2)
    assert result.transferred_stance is None
    assert not result.reliable


def test_missing_stance_or_confidence_is_not_forced():
    assert propagated(None, 0.8).transferred_stance is None
    assert propagated(0.8, 0.8, confidence=None).transfer_confidence is None


def test_cache_reuses_anchor_and_relation_values():
    cache = TopicPropagationCache()
    anchors = ([1.0, 0.0], [-1.0, 0.0])
    relation = TopicRelation(relevance=0.8, polarity=-0.4)
    cache.set_anchors(("target", "description"), anchors)
    cache.set_relation(("citation", "target"), relation)
    assert cache.get_anchors(("target", "description")) is anchors
    assert cache.get_relation(("citation", "target")) is relation


def test_identical_topics_preserve_stance_polarity():
    result = propagate_stance(
        -0.8,
        0.9,
        TopicRelation(relevance=1.0, polarity=1.0),
        minimum_relevance=0.5,
        minimum_polarity=0.15,
    )
    assert result.reliable
    assert result.transferred_stance == pytest.approx(-0.8)
