from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class BenchmarkCase(BaseModel):
    test_id: str
    dataset_type: str
    article_text: str
    expected_citations: list[dict[str, Any]] = Field(default_factory=list)
    expected_persons: list[str] = Field(default_factory=list)
    expected_topics: list[str] = Field(default_factory=list)
    expected_stance: list[dict[str, Any]] = Field(default_factory=list)
    expected_contradictions: list[dict[str, Any]] = Field(default_factory=list)
    difficulty: str = "medium"
    description: str = ""


class ModelConfig(BaseModel):
    model_id: str
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1200, ge=1, le=20000)
    reasoning: bool = False


class RunCreateRequest(BaseModel):
    experiment_type: str = Field(pattern=r"^EXP-0[1-3]$")
    dataset: str = "synthetic"
    model_ids: list[str] = Field(default_factory=list, min_length=1)
    repetitions: int = Field(default=1, ge=1, le=3)
    configurations: list[ModelConfig] = Field(default_factory=list)
    prompt_version: str = "benchmark-v1"


class RunResponse(BaseModel):
    run_id: str
    experiment_type: str
    status: str
    dataset: str
    model_ids: list[str]
    repetitions: int
    prompt_version: str
    created_at: str
    completed_at: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)

