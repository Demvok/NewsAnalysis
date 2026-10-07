from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, JSON, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def generate_article_id(url: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, url.strip()))


class Article(BaseModel):
    id: str
    url: str
    title: str
    content_c: str
    author: Optional[str] = None
    source: str
    language: str
    published_at: Optional[datetime] = None
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    description: Optional[str] = None

    @classmethod
    def create(
        cls,
        url: str,
        title: str,
        content_c: str,
        source: str,
        language: str,
        author: Optional[str] = None,
        published_at: Optional[datetime] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
        parsed_at: Optional[datetime] = None,
    ) -> "Article":
        return cls(
            id=generate_article_id(url),
            url=url.strip(),
            title=title.strip(),
            content_c=content_c.strip(),
            author=author.strip() if author else None,
            source=source.strip(),
            language=language.strip().lower(),
            published_at=published_at,
            parsed_at=parsed_at or datetime.now(timezone.utc),
            category=category.strip() if category else None,
            tags=tags or [],
            description=description.strip() if description else None,
        )


class ArticleIndexItem(BaseModel):
    id: str
    url: str
    title: str
    source: str
    author: Optional[str] = None
    language: str
    published_at: Optional[datetime] = None
    category: Optional[str] = None

    @classmethod
    def from_article(cls, article: Article) -> "ArticleIndexItem":
        return cls(
            id=article.id,
            url=article.url,
            title=article.title,
            source=article.source,
            author=article.author,
            language=article.language,
            published_at=article.published_at,
            category=article.category,
        )


class ArticleModel(Base):
    __tablename__ = "articles"

    id = Column(String(36), primary_key=True)
    url = Column(String(2048), unique=True, index=True, nullable=False)
    title = Column(String(512), nullable=False)
    content_c = Column(Text, nullable=False)
    author = Column(String(256), nullable=True)
    source = Column(String(128), index=True, nullable=False)
    language = Column(String(10), index=True, nullable=False)
    published_at = Column(DateTime(timezone=True), index=True, nullable=True)
    parsed_at = Column(DateTime(timezone=True), nullable=False)
    category = Column(String(128), nullable=True)
    tags = Column(JSON, nullable=False, default=list)
    description = Column(Text, nullable=True)

    def to_pydantic(self) -> Article:
        return Article(
            id=self.id,
            url=self.url,
            title=self.title,
            content_c=self.content_c,
            author=self.author,
            source=self.source,
            language=self.language,
            published_at=self.published_at,
            parsed_at=self.parsed_at,
            category=self.category,
            tags=self.tags or [],
            description=self.description,
        )

    @classmethod
    def from_pydantic(cls, article: Article) -> "ArticleModel":
        return cls(**article.model_dump())
