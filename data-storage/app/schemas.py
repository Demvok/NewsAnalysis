from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ArticlePayload(BaseModel):
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


class PersonPayload(BaseModel):
    name: str
    image_url: str | None = None
    affiliation: Any = None
    aliases: Any = None
    status: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None


class CitationPayload(BaseModel):
    origin_article_id: UUID
    person_uuid: UUID | None = None
    exact_quote: str | None = None
    context: str | None = None
    citation_type: str | None = None
    summarized_quote: str | None = None
    summarized_quote_vector: list[float] | None = None
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)


class TopicPayload(BaseModel):
    topic_name: str
    topic_description: str | None = None
    topic_description_vector: list[float] | None = None
    general_topic_field: str | None = None


class AttitudePayload(BaseModel):
    topic_uuid: UUID
    citation_uuid: UUID
    stance: str | None = None
    relevancy_score: float | None = None
    stance_summary: str | None = None
    inconsistency_detected: bool | None = None
    inconsistent_with: Any = None
    inconsistency_comment: str | None = None


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


class CitationUpdate(BaseModel):
    person_uuid: UUID | None = None
    exact_quote: str | None = None
    context: str | None = None
    citation_type: str | None = None
    summarized_quote: str | None = None
    summarized_quote_vector: list[float] | None = None
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)


class TopicUpdate(BaseModel):
    topic_name: str | None = None
    topic_description: str | None = None
    topic_description_vector: list[float] | None = None
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
