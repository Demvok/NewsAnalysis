from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"


class JobType(StrEnum):
    ARTICLE_ANALYSIS = "article_analysis"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: UUID
    inconsistency_window_days: int = Field(default=365, ge=1, le=3650)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    job_type: JobType
    article_id: UUID
    status: JobStatus
    current_stage: str | None
    progress: None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    retry_count: int
    error_code: str | None
    error_summary: str | None
    correlation_id: UUID
    trace_id: UUID
    metadata: dict[str, Any]


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Article processing task", min_length=1, max_length=200)
    article_ids: list[UUID] = Field(min_length=1, max_length=500)
    inconsistency_window_days: int = Field(default=365, ge=1, le=3650)


class TaskResponse(BaseModel):
    task_id: UUID
    name: str
    status: TaskStatus
    article_ids: list[UUID]
    job_ids: list[UUID]
    total_articles: int
    completed_articles: int
    failed_articles: int
    running_articles: int
    waiting_articles: int
    citations: int
    speakers: int
    topics: int
    attitudes: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class StageResponse(BaseModel):
    stage_id: UUID
    job_id: UUID
    stage: str
    status: StageStatus
    attempt: int
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    summary: dict[str, Any]
    error_code: str | None
    error_summary: str | None


class ServiceDependency(BaseModel):
    name: str
    status: str
    availability: str
    latency_ms: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    checked_at: datetime


class SystemHealthResponse(BaseModel):
    status: str
    service: str
    version: str
    dependencies: list[ServiceDependency] = Field(default_factory=list)


class RuntimeSettingsResponse(BaseModel):
    request_timeout_seconds: float
    analysis_timeout_seconds: float
    configurable_fields: list[str]


class RuntimeSettingsUpdate(BaseModel):
    request_timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    analysis_timeout_seconds: float | None = Field(default=None, gt=0, le=7200)


class EventRequest(BaseModel):
    event_id: UUID | None = None
    timestamp: datetime | None = None
    service: str = Field(min_length=1, max_length=100)
    level: str = Field(default="INFO", max_length=20)
    event_type: str = Field(min_length=1, max_length=100)
    job_id: UUID | None = None
    correlation_id: UUID | None = None
    request_id: UUID | None = None
    article_id: UUID | None = None
    stage: str | None = Field(default=None, max_length=100)
    duration_ms: int | None = Field(default=None, ge=0)
    operation: str | None = Field(default=None, max_length=150)
    status: str | None = Field(default=None, max_length=30)
    trace_id: UUID | None = None
    span_id: str | None = Field(default=None, max_length=200)
    parent_span_id: str | None = Field(default=None, max_length=200)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    time_to_first_token_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    input_tokens_estimated: int | None = Field(default=None, ge=0)
    output_tokens_estimated: int | None = Field(default=None, ge=0)
    throughput_tokens_per_second: float | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=100)
    finish_reason: str | None = Field(default=None, max_length=100)
    retry_count: int = Field(default=0, ge=0)
    error_category: str | None = Field(default=None, max_length=100)
    error_detail: str | None = Field(default=None, max_length=500)
    debug_content: dict[str, str | None] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExperimentStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExperimentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    article_ids: list[UUID] = Field(min_length=1, max_length=1000)
    model_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    pipeline_parameters: dict[str, Any] = Field(default_factory=dict)
    dataset_name: str | None = Field(default=None, max_length=200)


class ExperimentResponse(BaseModel):
    experiment_id: UUID
    description: str
    dataset_name: str | None
    article_ids: list[UUID]
    model_config_snapshot: dict[str, Any]
    prompt_versions: dict[str, str]
    pipeline_parameters: dict[str, Any]
    status: ExperimentStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    job_ids: list[UUID]
    metrics: dict[str, Any] = Field(default_factory=dict)


class ExperimentComparisonRequest(BaseModel):
    experiment_ids: list[UUID] = Field(min_length=2, max_length=20)


class EvaluationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: UUID
    evaluated_items: int = Field(gt=0)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
