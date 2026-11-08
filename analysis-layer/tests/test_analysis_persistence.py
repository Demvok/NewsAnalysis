from __future__ import annotations

from uuid import uuid4

import httpx

from app import analysis, models


def test_persist_inconsistencies_allows_empty_historical_candidates(monkeypatch):
    person_uuid = str(uuid4())
    topic_uuid = uuid4()
    citation_uuid = uuid4()

    def history_response(*args, **kwargs):
        return httpx.Response(
            200,
            json=[],
            request=httpx.Request("GET", "http://data-storage/citations/history"),
        )

    monkeypatch.setattr(analysis.httpx, "get", history_response)

    result = analysis.persist_inconsistencies(
        person_uuid=person_uuid,
        speaker_kind=models.SpeakerKind.PERSON,
        current_citations={str(citation_uuid): "Current statement"},
        topic_ids={"topic": str(topic_uuid)},
        topics=[{"topic_name": "Topic"}],
        attitudes=[{"topic_uuid": str(topic_uuid), "citation_uuid": str(citation_uuid), "stance": 1.0}],
        before=None,
        window_days=365,
    )

    assert result["comparison_count"] == 0


def test_persist_inconsistencies_reads_topic_propagation_result_fields(monkeypatch):
    person_uuid = str(uuid4())
    topic_uuid = uuid4()
    current_citation_uuid = uuid4()
    historical_citation_uuid = uuid4()

    def history_response(*args, **kwargs):
        return httpx.Response(
            200,
            json=[{
                "citation_uuid": str(historical_citation_uuid),
                "topic_uuid": str(topic_uuid),
                "exact_quote": "Earlier statement",
                "stance": 1.0,
                "stance_confidence": 0.9,
                "citation_type": "direct",
            }],
            request=httpx.Request("GET", "http://data-storage/citations/history"),
        )

    monkeypatch.setattr(analysis.httpx, "get", history_response)
    monkeypatch.setattr(
        analysis,
        "propagate_topic_stances",
        lambda request: [models.TopicPropagationResult(
            citation_uuid=historical_citation_uuid,
            citation_topic_uuid=topic_uuid,
            target_topic_uuid=topic_uuid,
            topic_relevance=0.9,
            topic_relation_polarity=1.0,
            transferred_stance=1.0,
            transfer_confidence=0.8,
            reliable=False,
            status="UNCERTAIN",
        )],
    )

    result = analysis.persist_inconsistencies(
        person_uuid=person_uuid,
        speaker_kind=models.SpeakerKind.PERSON,
        current_citations={str(current_citation_uuid): "Current statement"},
        topic_ids={"topic": str(topic_uuid)},
        topics=[{"topic_name": "Topic"}],
        attitudes=[{
            "topic_uuid": str(topic_uuid),
            "citation_uuid": str(current_citation_uuid),
            "stance": 1.0,
        }],
        before=None,
        window_days=365,
    )

    assert result["comparison_count"] == 0
