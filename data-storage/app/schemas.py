from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal
from uuid import UUID

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def parse_datetime(value: object) -> object:
    """Accept ISO timestamps and common feeds' spaced numeric offsets."""
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


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CitationType(str, Enum):
    DIRECT = "direct"
    PARTIAL = "partial"
    INDIRECT = "indirect"
    SUMMARY = "summary"
    POSITION = "position"
    MENTION = "mention"


class ArticlePayload(BaseModel):
    article_id: UUID | None = None
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

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class PersonPayload(BaseModel):
    person_uuid: UUID | None = None
    name: str = Field(min_length=1, max_length=512)
    image_url: str | None = None
    affiliation: Any = None
    aliases: Any = None
    status: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None

    @field_validator("created_at", "modified_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: object) -> object:
        return parse_datetime(value)


class CitationPayload(BaseModel):
    citation_uuid: UUID | None = None
    origin_article_id: UUID
    person_uuid: UUID | None = None
    exact_quote: str | None = None
    context: str | None = None
    citation_type: CitationType | None = None
    summarized_quote: str | None = None
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("confidence_level", "extraction_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: object) -> object:
        if isinstance(value, (int, float)) and 1 < value <= 5:
            return value / 5
        return value


class TopicPayload(BaseModel):
    topic_uuid: UUID | None = None
    topic_name: str = Field(min_length=1, max_length=512)
    topic_description: str | None = None
    general_topic_field: str | None = None


class AttitudePayload(BaseModel):
    topic_uuid: UUID
    citation_uuid: UUID
    stance: str | None = None
    relevancy_score: float | None = Field(default=None, ge=0, le=1)
    stance_summary: str | None = None
    inconsistency_detected: bool | None = None
    inconsistent_with: Any = None
    inconsistency_comment: str | None = None


class InconsistencyPayload(BaseModel):
    person_uuid: UUID
    topic_uuid: UUID
    citation_a_uuid: UUID
    citation_b_uuid: UUID
    attitude_a: str | None = None
    attitude_b: str | None = None
    classification: Literal["CONSISTENT", "POSITION_CHANGE", "CONTRADICTION", "INSUFFICIENT_EVIDENCE"]
    severity: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    inconsistency_comment: str | None = None
    detected_at: datetime | None = None

    @field_validator("detected_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: object) -> object:
        return parse_datetime(value)

    @model_validator(mode="after")
    def validate_distinct_citations(self) -> "InconsistencyPayload":
        if self.citation_a_uuid == self.citation_b_uuid:
            raise ValueError("citation_a_uuid and citation_b_uuid must be different")
        return self


class ArticleUpdate(ArticlePayload):
    pass


class PersonUpdate(BaseModel):
    name: str | None = None
    image_url: str | None = None
    affiliation: Any = None
    aliases: Any = None
    status: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None

    @field_validator("created_at", "modified_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: object) -> object:
        return parse_datetime(value)


class EmbeddingRequest(BaseModel):
    text: str = Field(min_length=1)


class EmbeddingBatchRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=100)


class CitationUpdate(BaseModel):
    person_uuid: UUID | None = None
    exact_quote: str | None = None
    context: str | None = None
    citation_type: CitationType | None = None
    summarized_quote: str | None = None
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("confidence_level", "extraction_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: object) -> object:
        if isinstance(value, (int, float)) and 1 < value <= 5:
            return value / 5
        return value


class TopicUpdate(BaseModel):
    topic_name: str | None = None
    topic_description: str | None = None
    general_topic_field: str | None = None


class AttitudeUpdate(BaseModel):
    stance: str | None = None
    relevancy_score: float | None = None
    stance_summary: str | None = None
    inconsistency_detected: bool | None = None
    inconsistent_with: Any = None
    inconsistency_comment: str | None = None


class ArticleBulkUpdate(ArticleUpdate):
    article_id: UUID


class PersonBulkUpdate(PersonUpdate):
    person_uuid: UUID


class CitationBulkUpdate(CitationUpdate):
    citation_uuid: UUID


class TopicBulkUpdate(TopicUpdate):
    topic_uuid: UUID


class AttitudeBulkUpdate(AttitudeUpdate):
    topic_uuid: UUID
    citation_uuid: UUID


class BulkDeleteRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=1000)


class AttitudeKey(BaseModel):
    topic_uuid: UUID
    citation_uuid: UUID


class BulkAttitudeDeleteRequest(BaseModel):
    keys: list[AttitudeKey] = Field(min_length=1, max_length=1000)
