from unittest.mock import Mock
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import main
from app.schemas import EmbeddingBatchRequest


def test_deduplicated_embeddings_preserve_input_order(monkeypatch):
    embed = Mock(side_effect=lambda texts: [[float(index)] for index, _ in enumerate(texts)])
    monkeypatch.setattr(main, "_embed_texts", embed)

    result = main._embed_texts_deduplicated([" alpha  beta ", "gamma", "alpha beta"])

    assert result == [[0.0], [1.0], [0.0]]
    embed.assert_called_once_with([" alpha  beta ", "gamma"])


def test_empty_embedding_batch_does_not_call_provider(monkeypatch):
    embed = Mock()
    monkeypatch.setattr(main, "_embed_texts", embed)

    assert main._embed_texts_deduplicated([]) == []
    embed.assert_not_called()


def test_deduplicated_embeddings_split_provider_requests_at_configured_limit(monkeypatch):
    calls = []

    def embed(texts):
        calls.append(texts)
        return [[float(index)] for index, _ in enumerate(texts)]

    monkeypatch.setattr(main, "_embedding_batch_size", 100)
    monkeypatch.setattr(main, "_embed_texts", Mock(side_effect=embed))
    texts = [f"text-{index}" for index in range(205)]

    result = main._embed_texts_deduplicated(texts)

    assert [len(call) for call in calls] == [100, 100, 5]
    assert len(result) == len(texts)
    assert result[0] == [0.0]
    assert result[100] == [0.0]
    assert result[200] == [0.0]


def test_deduplicated_embeddings_paces_bulk_batches(monkeypatch):
    delays = []
    monkeypatch.setattr(main, "time", Mock(sleep=delays.append))
    monkeypatch.setattr(main, "_embedding_batch_size", 2)
    monkeypatch.setattr(
        main,
        "_embed_texts",
        Mock(side_effect=lambda texts: [[float(index)] for index, _ in enumerate(texts)]),
    )

    main._embed_texts_deduplicated(
        ["one", "two", "three"],
        inter_batch_delay_seconds=60,
    )

    assert delays == [60]


def test_local_model_bulk_embeddings_are_not_split_at_google_limit(monkeypatch):
    calls = []

    def embed(texts):
        calls.append(texts)
        return [[float(index)] for index, _ in enumerate(texts)]

    monkeypatch.setattr(main, "_embedding_provider", "local_model")
    monkeypatch.setattr(main, "_embed_texts", Mock(side_effect=embed))
    texts = [f"text-{index}" for index in range(205)]

    result = main._embed_texts_deduplicated(texts)

    assert [len(call) for call in calls] == [205]
    assert len(result) == len(texts)


def test_local_gguf_embeddings_use_provider_order_indexes(monkeypatch):
    class FakeLlama:
        def __init__(self, **kwargs):
            assert kwargs["embedding"] is True

        def create_embedding(self, input):
            assert input == ["first", "second"]
            return {
                "data": [
                    {"index": 1, "embedding": [2.0]},
                    {"index": 0, "embedding": [1.0]},
                ]
            }

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=FakeLlama))
    monkeypatch.setattr(main, "_local_embedding_model", None)
    monkeypatch.setattr(main, "_embedding_model_name", "/tmp/model.gguf")

    assert main._embed_texts_local_model(["first", "second"]) == [[1.0], [2.0]]


def test_embedding_batch_accepts_unbounded_local_batches():
    assert EmbeddingBatchRequest(texts=[]).texts == []
    assert len(EmbeddingBatchRequest(texts=["text"] * 205).texts) == 205


def test_batch_results_preserve_partial_invalid_input(monkeypatch):
    monkeypatch.setattr(main, "_embed_texts", Mock(return_value=[[0.0]]))

    result = main._embedding_results(["valid", "  "])

    assert [item["index"] for item in result] == [0, 1]
    assert result[1]["embedding"] is None
    assert result[1]["error"] == "text must not be blank"


def test_batch_provider_failure_returns_per_item_errors(monkeypatch):
    monkeypatch.setattr(main, "_embed_texts", Mock(side_effect=RuntimeError("provider down")))

    result = main._embedding_results(["one", "two"])

    assert [item["error"] for item in result] == ["provider down", "provider down"]


def test_embedding_dimension_validation():
    with pytest.raises(HTTPException):
        main._validate_vector([0.0])


def test_embedding_response_without_indexes_preserves_provider_order(monkeypatch):
    class Item:
        def __init__(self, embedding, index=None):
            self.embedding = embedding
            self.index = index

    response = Mock(
        data=[
            Item([1.0], None),
            Item([2.0], None),
        ]
    )
    client = Mock()
    client.embeddings.create.return_value = response
    monkeypatch.setattr(main, "_embedding_client", client)
    monkeypatch.setattr(main, "_validate_vector", lambda values: None)

    result = main._embed_texts(["first", "second"])

    assert result == [[1.0], [2.0]]


def test_google_embedding_provider_uses_native_batch_endpoint(monkeypatch):
    class Response:
        is_success = True

        def json(self):
            return {"embeddings": [{"values": [1.0]}, {"values": [2.0]}]}

    post = Mock(return_value=Response())
    monkeypatch.setattr(main, "httpx", Mock(post=post))
    monkeypatch.setattr(main, "_embedding_provider", "google")
    monkeypatch.setattr(main, "_embedding_model_name", "gemini-embedding-001")
    monkeypatch.setattr(main, "_embedding_output_dimension_value", 768)
    monkeypatch.setattr(main, "_validate_vector", lambda values: None)

    result = main._embed_texts(["first", "second"])

    assert result == [[1.0], [2.0]]
    url = post.call_args.args[0]
    assert url.endswith("/models/gemini-embedding-001:batchEmbedContents")
    payload = post.call_args.kwargs["json"]
    assert len(payload["requests"]) == 2
    assert payload["requests"][0]["model"] == "models/gemini-embedding-001"
    assert payload["requests"][0]["outputDimensionality"] == 768
