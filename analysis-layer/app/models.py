from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class CitationType(str, Enum):
    DIRECT = "direct"
    PARTIAL = "partial"
    INDIRECT = "indirect"
    SUMMARY = "summary"
    POSITION = "position"
    MENTION = "mention"


class CitationCandidate(BaseModel):
    exact_quote: str = Field(min_length=1)
    context: str
    citation_type: "CitationType"
    summarized_quote: str
    confidence_level: float = Field(ge=0, le=1)
    extraction_confidence: float = Field(ge=0, le=1)


class SpeakerExtraction(BaseModel):
    speaker_name: str = Field(min_length=1)
    speaker_role: str = ""
    aliases: list[str] = Field(default_factory=list)
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


class TopicAttitude(BaseModel):
    topic_name: str
    topic_description: str | None = None
    stance: str
    relevancy_score: float = Field(ge=0, le=1)
    stance_summary: str
    stance_confidence: float = Field(ge=0, le=1)
