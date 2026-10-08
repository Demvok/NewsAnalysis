from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, JSON, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "768"))
DEFAULT_DATABASE_URL = "postgresql+psycopg://news:POSTGRES_PASSWORD@postgres-db:5432/news"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


class Base(DeclarativeBase):
    pass


class DimArticle(Base):
    __tablename__ = "dimArticle"
    __table_args__ = (
        CheckConstraint("status <> ''", name="ck_dim_article_status_nonempty"),
    )

    article_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    article_title: Mapped[str | None] = mapped_column(Text)
    article_content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    source: Mapped[str | None] = mapped_column(String(256))
    language: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(Text, unique=True)
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    parsed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[object | None] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)

    citations: Mapped[list["DimCitation"]] = relationship(back_populates="article", cascade="all, delete-orphan")


class DimPerson(Base):
    __tablename__ = "dimPerson"

    person_uuid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    affiliation: Mapped[object | None] = mapped_column(JSON)
    aliases: Mapped[object | None] = mapped_column(JSON)
    status: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class DimCitation(Base):
    __tablename__ = "dimCitation"
    __table_args__ = (
        CheckConstraint("confidence_level IS NULL OR confidence_level BETWEEN 0 AND 1", name="ck_citation_confidence"),
        CheckConstraint(
            "extraction_confidence IS NULL OR extraction_confidence BETWEEN 0 AND 1",
            name="ck_citation_extraction_confidence",
        ),
    )

    citation_uuid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    origin_article_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimArticle.article_id", ondelete="CASCADE"), nullable=False
    )
    person_uuid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("dimPerson.person_uuid"))
    exact_quote: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    citation_type: Mapped[str | None] = mapped_column(String(32))
    summarized_quote: Mapped[str | None] = mapped_column(Text)
    summarized_quote_vector: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    confidence_level: Mapped[float | None] = mapped_column(Float)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)

    article: Mapped[DimArticle] = relationship(back_populates="citations")


class DimTopic(Base):
    __tablename__ = "dimTopic"
    __table_args__ = (
        CheckConstraint("topic_name <> ''", name="ck_topic_name_nonempty"),
    )

    topic_uuid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_name: Mapped[str] = mapped_column(String(512), nullable=False)
    topic_description: Mapped[str | None] = mapped_column(Text)
    topic_description_vector: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    general_topic_field: Mapped[str | None] = mapped_column(String(128))


class FctAttitude(Base):
    __tablename__ = "fctAttitude"
    __table_args__ = (
        CheckConstraint("relevancy_score IS NULL OR relevancy_score BETWEEN 0 AND 1", name="ck_attitude_relevancy"),
    )

    topic_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimTopic.topic_uuid", ondelete="CASCADE"), primary_key=True
    )
    citation_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimCitation.citation_uuid", ondelete="CASCADE"), primary_key=True
    )
    stance: Mapped[str | None] = mapped_column(String(64))
    relevancy_score: Mapped[float | None] = mapped_column(Float)
    stance_summary: Mapped[str | None] = mapped_column(Text)
    inconsistency_detected: Mapped[bool | None] = mapped_column(Boolean)
    inconsistent_with: Mapped[object | None] = mapped_column(JSON)
    inconsistency_comment: Mapped[str | None] = mapped_column(Text)


class FctInconsistency(Base):
    __tablename__ = "fctInconsistency"
    __table_args__ = (
        UniqueConstraint("topic_uuid", "citation_a_uuid", "citation_b_uuid", name="uq_inconsistency_evidence_pair"),
        CheckConstraint(
            "classification IN ('CONSISTENT', 'POSITION_CHANGE', 'CONTRADICTION', 'INSUFFICIENT_EVIDENCE')",
            name="ck_inconsistency_classification",
        ),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_inconsistency_confidence"),
    )

    inconsistency_uuid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    person_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimPerson.person_uuid", ondelete="CASCADE"), nullable=False
    )
    topic_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimTopic.topic_uuid", ondelete="CASCADE"), nullable=False
    )
    citation_a_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimCitation.citation_uuid", ondelete="CASCADE"), nullable=False
    )
    citation_b_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimCitation.citation_uuid", ondelete="CASCADE"), nullable=False
    )
    attitude_a: Mapped[str | None] = mapped_column(String(64))
    attitude_b: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    inconsistency_comment: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


engine = create_engine(database_url(), pool_pre_ping=True, pool_recycle=1800)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def create_database() -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)


def recreate_database() -> None:
    Base.metadata.drop_all(bind=engine)
    create_database()


@contextmanager
def get_session() -> Iterator:
    with SessionFactory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def check_database() -> bool:
    with engine.connect() as connection:
        return connection.execute(text("SELECT 1")).scalar_one() == 1
