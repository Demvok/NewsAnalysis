from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint, create_engine, event, inspect, text
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    tags: Mapped[object | None] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)

    citations: Mapped[list["DimCitation"]] = relationship(back_populates="article", cascade="all, delete-orphan")


class DimPerson(Base):
    __tablename__ = "dimPerson"

    person_uuid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    affiliation: Mapped[object | None] = mapped_column(JSON)
    person_type: Mapped[str] = mapped_column(String(32), default="UNRESOLVED", nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    aliases_vector: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


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
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

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
    general_topic_field: Mapped[str] = mapped_column(String(128), default="UNCLASSIFIED", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class FctAttitude(Base):
    __tablename__ = "fctAttitude"
    __table_args__ = (
        CheckConstraint("relevancy_score IS NULL OR relevancy_score BETWEEN 0 AND 1", name="ck_attitude_relevancy"),
        CheckConstraint("stance_confidence IS NULL OR stance_confidence BETWEEN 0 AND 1", name="ck_attitude_stance_confidence"),
    )

    topic_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimTopic.topic_uuid", ondelete="CASCADE"), primary_key=True
    )
    citation_uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dimCitation.citation_uuid", ondelete="CASCADE"), primary_key=True
    )
    stance: Mapped[str | None] = mapped_column(String(64))
    relevancy_score: Mapped[float | None] = mapped_column(Float)
    stance_confidence: Mapped[float | None] = mapped_column(Float)
    stance_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class FctInconsistency(Base):
    __tablename__ = "fctInconsistency"
    __table_args__ = (
        UniqueConstraint("topic_uuid", "citation_a_uuid", "citation_b_uuid", name="uq_inconsistency_evidence_pair"),
        CheckConstraint(
            "citation_a_uuid <> citation_b_uuid",
            name="ck_inconsistency_distinct_citations",
        ),
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
    severity: Mapped[str] = mapped_column(Text, default="NONE", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    inconsistency_comment: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


Index(
    "ix_dim_person_aliases_vector",
    DimPerson.aliases_vector,
    postgresql_using="hnsw",
    postgresql_ops={"aliases_vector": "vector_cosine_ops"},
)
Index(
    "ix_dim_citation_summarized_quote_vector",
    DimCitation.summarized_quote_vector,
    postgresql_using="hnsw",
    postgresql_ops={"summarized_quote_vector": "vector_cosine_ops"},
)
Index(
    "ix_dim_topic_description_vector",
    DimTopic.topic_description_vector,
    postgresql_using="hnsw",
    postgresql_ops={"topic_description_vector": "vector_cosine_ops"},
)
Index(
    "uq_inconsistency_unordered_evidence_pair",
    FctInconsistency.topic_uuid,
    text("LEAST(citation_a_uuid, citation_b_uuid)"),
    text("GREATEST(citation_a_uuid, citation_b_uuid)"),
    unique=True,
)


engine = create_engine(database_url(), pool_pre_ping=True, pool_recycle=1800)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(SessionFactory.class_, "before_flush")
def update_lifecycle_metadata(session, flush_context, instances) -> None:
    now = datetime.now(timezone.utc)
    for item in session.new:
        if hasattr(item, "status") and not getattr(item, "status", None):
            item.status = "NEW"
        if hasattr(item, "created_at") and getattr(item, "created_at", None) is None:
            item.created_at = now
        if hasattr(item, "modified_at"):
            item.modified_at = now
    for item in session.dirty:
        if session.is_modified(item, include_collections=False) and hasattr(item, "modified_at"):
            item.modified_at = now
            if hasattr(item, "status") and not inspect(item).attrs.status.history.has_changes():
                item.status = "UPDATED"


def create_database() -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        _migrate_lifecycle_columns(connection)
        connection.execute(
            text(
                'ALTER TABLE "fctAttitude" '
                'ADD COLUMN IF NOT EXISTS stance_confidence DOUBLE PRECISION'
            )
        )
        connection.execute(
            text(
                'DO $$ BEGIN '
                'ALTER TABLE "fctAttitude" ADD CONSTRAINT '
                'ck_attitude_stance_confidence CHECK '
                '(stance_confidence IS NULL OR stance_confidence BETWEEN 0 AND 1); '
                'EXCEPTION WHEN duplicate_object THEN NULL; END $$'
            )
        )


def _migrate_lifecycle_columns(connection) -> None:
    """Add lifecycle fields introduced after the initial schema was deployed."""
    for table in ("dimArticle", "dimCitation", "dimTopic", "fctAttitude", "fctInconsistency"):
        connection.execute(
            text(
                f'ALTER TABLE "{table}" '
                'ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE '
                'NOT NULL DEFAULT CURRENT_TIMESTAMP'
            )
        )
        connection.execute(
            text(
                f'ALTER TABLE "{table}" '
                'ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP WITH TIME ZONE '
                'NOT NULL DEFAULT CURRENT_TIMESTAMP'
            )
        )

    for table in ("dimArticle", "dimCitation", "dimTopic", "fctAttitude", "fctInconsistency"):
        connection.execute(
            text(
                f'ALTER TABLE "{table}" '
                'ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT \'NEW\''
            )
        )


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
