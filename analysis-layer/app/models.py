from __future__ import annotations

from datetime import datetime
import re
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_datetime(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    normalized = re.sub(r"\s+([+-]\d{2})(\d{2})$", r"\1:\2", normalized)
    normalized = re.sub(r"\s+([+-]\d{2}:\d{2})$", r"\1", normalized)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return value


class Article(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    article_id: UUID
    article_title: str | None = None
    article_content: str | None = None
    author: str | None = None
    status: str = "NEW"
    source: str | None = None
    language: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    parsed_at: datetime | None = None
    modified_at: datetime | None = None
    tags: Any = None
    description: str | None = None

    @field_validator("published_at", "parsed_at", "modified_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: object) -> object:
        return parse_datetime(value)


class CitationType(str, Enum):
    DIRECT = "direct"
    PARTIAL = "partial"
    INDIRECT = "indirect"
    SUMMARY = "summary"
    POSITION = "position"
    MENTION = "mention"


class SpeakerKind(str, Enum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    GROUP = "GROUP"
    ANONYMOUS_SOURCE = "ANONYMOUS_SOURCE"
    UNRESOLVED = "UNRESOLVED"


class CitationCandidate(BaseModel):
    exact_quote: str = Field(min_length=1)
    context: str
    citation_type: "CitationType"
    summarized_quote: str
    confidence_level: float = Field(ge=0, le=1)
    extraction_confidence: float = Field(ge=0, le=1)

    @field_validator("confidence_level", "extraction_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: object) -> object:
        if isinstance(value, (int, float)) and 1 < value <= 5:
            return value / 5
        return value


class SpeakerExtraction(BaseModel):
    speaker_name: str = Field(min_length=1)
    speaker_role: str = ""
    aliases: list[str] = Field(default_factory=list)
    speaker_kind: SpeakerKind = SpeakerKind.UNRESOLVED
    influence_score: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Estimated impact of this speaker's evidence on the article's story.",
    )
    uncertain: bool = False
    uncertainty_reason: str = ""
    citations: list[CitationCandidate] = Field(default_factory=list)


class CitationExtractionResult(BaseModel):
    article_title: str
    status: str
    speakers: list[SpeakerExtraction] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    article_id: UUID | None = None
    article_title: str = ""
    article_content: str = ""
    published_at: datetime | None = None
    inconsistency_window_days: int = Field(default=365, ge=1, le=3650)

    @field_validator("published_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: object) -> object:
        return parse_datetime(value)


class CitationAnalysisRequest(BaseModel):
    article_id: UUID
    extraction: CitationExtractionResult
    published_at: datetime | None = None
    inconsistency_window_days: int = Field(default=365, ge=1, le=3650)

    @field_validator("published_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: object) -> object:
        return parse_datetime(value)


class StageRequest(BaseModel):
    """Input shared by independently executable analysis stages."""

    stage_status: str = Field(default="READY", min_length=1)


class ExtractionStageRequest(StageRequest):
    article: AnalysisRequest


class ExtractionPersistenceRequest(StageRequest):
    article_id: UUID
    extraction: CitationExtractionResult


class CitationAnalysisStageRequest(StageRequest):
    analysis: CitationAnalysisRequest


class StageStats(BaseModel):
    created_new: int = Field(default=0, ge=0)
    retrieved_from_db: int = Field(default=0, ge=0)


class StageResponse(BaseModel):
    stage: str
    status: str
    result: Any = None
    stats: StageStats = Field(default_factory=StageStats)


class BulkStageResponse(BaseModel):
    stage: str
    status: str
    results: list[StageResponse]
    stats: StageStats = Field(default_factory=StageStats)


class TopicAttitude(BaseModel):
    topic_name: str
    topic_description: str | None = None
    general_topic_field: str = "UNCLASSIFIED"
    stance: str
    relevancy_score: float = Field(ge=0, le=1)
    stance_summary: str
    stance_confidence: float = Field(ge=0, le=1)
