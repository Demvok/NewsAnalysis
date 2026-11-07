from unittest.mock import Mock
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

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


def test_embedding_batch_accepts_empty_and_rejects_more_than_maximum():
    assert EmbeddingBatchRequest(texts=[]).texts == []
    with pytest.raises(ValidationError):
        EmbeddingBatchRequest(texts=["text"] * 101)


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
