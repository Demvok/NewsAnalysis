from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, status
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from app.database import (
    DimArticle,
    DimCitation,
    DimPerson,
    DimTopic,
    FctAttitude,
    check_database,
    create_database,
    engine,
    get_session,
    recreate_database,
)
from app.database.DBConnector import EMBEDDING_DIMENSION
from app.schemas import (
    ArticleBulkUpdate,
    ArticlePayload,
    ArticleUpdate,
    AttitudeBulkUpdate,
    AttitudeKey,
    AttitudePayload,
    AttitudeUpdate,
    BulkAttitudeDeleteRequest,
    BulkDeleteRequest,
    CitationBulkUpdate,
    CitationPayload,
    CitationUpdate,
    PersonBulkUpdate,
    PersonPayload,
    PersonUpdate,
    TopicBulkUpdate,
    TopicPayload,
    TopicUpdate,
)

logger = logging.getLogger("data-storage")
app = FastAPI(title="NewsAnalysis-DataStorage", version="0.2.0")


@app.on_event("startup")
def initialize_database() -> None:
    create_database()


def _validate_vector(values: list[float] | None) -> None:
    if values is not None and len(values) != EMBEDDING_DIMENSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Embedding must contain exactly {EMBEDDING_DIMENSION} values",
        )


def _as_dict(item: Any) -> dict[str, Any]:
    return {column.name: getattr(item, column.name) for column in item.__table__.columns}


def _get(model: Any, key: Any) -> dict[str, Any]:
    with get_session() as session:
        item = session.get(model, key)
        if item is None:
            raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
        session.expunge(item)
        return _as_dict(item)


def _list(model: Any, limit: int) -> list[dict[str, Any]]:
    with get_session() as session:
        return [_as_dict(item) for item in session.scalars(select(model).limit(limit)).all()]


def _create(model: Any, values: dict[str, Any]) -> dict[str, Any]:
    try:
        with get_session() as session:
            item = model(**values)
            session.add(item)
            session.flush()
            session.refresh(item)
            return _as_dict(item)
    except IntegrityError as exc:
        logger.exception("Failed to create %s", model.__name__)
        raise HTTPException(status_code=409, detail="Resource conflicts with an existing record") from exc


def _update(model: Any, key: Any, values: dict[str, Any]) -> dict[str, Any]:
    try:
        with get_session() as session:
            item = session.get(model, key)
            if item is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            for field, value in values.items():
                setattr(item, field, value)
            session.flush()
            session.refresh(item)
            return _as_dict(item)
    except IntegrityError as exc:
        logger.exception("Failed to update %s", model.__name__)
        raise HTTPException(status_code=409, detail="Resource conflicts with an existing record") from exc


def _delete(model: Any, key: Any) -> None:
    with get_session() as session:
        item = session.get(model, key)
        if item is None:
            raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
        session.delete(item)


def _bulk_create(model: Any, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        with get_session() as session:
            items = [model(**item_values) for item_values in values]
            session.add_all(items)
            session.flush()
            return [_as_dict(item) for item in items]
    except IntegrityError as exc:
        logger.exception("Failed to create %s records in bulk", model.__name__)
        raise HTTPException(status_code=409, detail="Bulk request conflicts with existing records") from exc


def _bulk_update(model: Any, key_field: str, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with get_session() as session:
        result = []
        for item_values in values:
            key = item_values.pop(key_field)
            item = session.get(model, key)
            if item is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} {key} not found")
            for field, value in item_values.items():
                setattr(item, field, value)
            result.append(item)
        session.flush()
        return [_as_dict(item) for item in result]


def _bulk_delete(model: Any, key_field: str, keys: list[Any]) -> dict[str, int]:
    with get_session() as session:
        result = session.execute(delete(model).where(getattr(model, key_field).in_(keys)))
        return {"deleted": result.rowcount or 0}


def _bulk_delete_attitudes(keys: list[AttitudeKey]) -> dict[str, int]:
    with get_session() as session:
        conditions = [
            (FctAttitude.topic_uuid == key.topic_uuid) & (FctAttitude.citation_uuid == key.citation_uuid)
            for key in keys
        ]
        result = session.execute(delete(FctAttitude).where(or_(*conditions)))
        return {"deleted": result.rowcount or 0}


@app.get("/health", tags=["System"], summary="Check API and database health")
def health() -> dict[str, str]:
    try:
        database = "ok" if check_database() else "error"
    except Exception:
        logger.exception("Database health check failed")
        database = "unavailable"
    return {"message": "Data Storage API Server is running", "version": app.version, "db_status": database}


@app.post("/database/create", status_code=201, tags=["Database"], summary="Create missing database tables")
def create_database_endpoint() -> dict[str, str]:
    create_database()
    return {"status": "created", "database": "postgres-db", "embedding_dimension": str(EMBEDDING_DIMENSION)}


@app.post("/database/recreate", status_code=201, tags=["Database"], summary="Drop and recreate all database tables")
def recreate_database_endpoint() -> dict[str, str]:
    recreate_database()
    return {"status": "recreated", "database": "postgres-db", "embedding_dimension": str(EMBEDDING_DIMENSION)}


@app.post("/articles", status_code=201, response_model=None, tags=["Articles"], summary="Create an article")
def create_article(payload: ArticlePayload) -> dict[str, Any]:
    if payload.url:
        with get_session() as session:
            existing = session.scalar(select(DimArticle).where(DimArticle.url == payload.url))
            if existing is not None:
                return _as_dict(existing)
    return _create(DimArticle, payload.model_dump())


@app.get("/articles", response_model=None, tags=["Articles"], summary="List articles")
def list_articles(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(DimArticle, limit)


@app.get("/articles/search", response_model=None, tags=["Articles"], summary="Search articles")
def search_articles(
    query: str = Query(min_length=1),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    with get_session() as session:
        statement = (
            select(DimArticle)
            .where(or_(DimArticle.article_title.ilike(pattern), DimArticle.article_content.ilike(pattern)))
            .limit(limit)
        )
        return [_as_dict(item) for item in session.scalars(statement).all()]


@app.get("/articles/{article_id}", response_model=None, tags=["Articles"], summary="Get an article")
def get_article(article_id: UUID) -> dict[str, Any]:
    return _get(DimArticle, article_id)


@app.put("/articles/{article_id}", response_model=None, tags=["Articles"], summary="Replace an article")
def update_article(article_id: UUID, payload: ArticleUpdate) -> dict[str, Any]:
    return _update(DimArticle, article_id, payload.model_dump(exclude_unset=True, exclude={"article_id"}))


@app.delete("/articles/bulk", tags=["Articles - Bulk"], summary="Delete articles in bulk")
def delete_articles_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimArticle, "article_id", payload.ids)


@app.delete("/articles/{article_id}", status_code=204, response_model=None, tags=["Articles"], summary="Delete an article")
def delete_article(article_id: UUID) -> None:
    _delete(DimArticle, article_id)


@app.post("/articles/bulk", status_code=201, response_model=None, tags=["Articles - Bulk"], summary="Create articles in bulk")
def create_articles_bulk(payload: list[ArticlePayload]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        with get_session() as session:
            for item in payload:
                values = item.model_dump(exclude_none=True)
                existing = None
                if item.article_id is not None:
                    existing = session.get(DimArticle, item.article_id)
                if existing is None and item.url:
                    existing = session.scalar(select(DimArticle).where(DimArticle.url == item.url))
                if existing is None:
                    existing = DimArticle(**values)
                    session.add(existing)
                    session.flush()
                results.append(_as_dict(existing))
        return results
    except IntegrityError as exc:
        logger.exception("Failed to create articles in bulk")
        raise HTTPException(status_code=409, detail="Bulk request conflicts with existing records") from exc


@app.patch("/articles/bulk", response_model=None, tags=["Articles - Bulk"], summary="Update articles in bulk")
def update_articles_bulk(payload: list[ArticleBulkUpdate]) -> list[dict[str, Any]]:
    return _bulk_update(DimArticle, "article_id", [item.model_dump(exclude_unset=True) for item in payload])


@app.post("/people", status_code=201, response_model=None, tags=["People"], summary="Create a person")
def create_person(payload: PersonPayload) -> dict[str, Any]:
    return _create(DimPerson, payload.model_dump())


@app.get("/people", response_model=None, tags=["People"], summary="List people")
def list_people(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(DimPerson, limit)


@app.get("/people/{person_uuid}", response_model=None, tags=["People"], summary="Get a person")
def get_person(person_uuid: UUID) -> dict[str, Any]:
    return _get(DimPerson, person_uuid)


@app.put("/people/{person_uuid}", response_model=None, tags=["People"], summary="Replace a person")
def update_person(person_uuid: UUID, payload: PersonUpdate) -> dict[str, Any]:
    return _update(DimPerson, person_uuid, payload.model_dump(exclude_unset=True))


@app.delete("/people/bulk", tags=["People - Bulk"], summary="Delete people in bulk")
def delete_people_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimPerson, "person_uuid", payload.ids)


@app.delete("/people/{person_uuid}", status_code=204, response_model=None, tags=["People"], summary="Delete a person")
def delete_person(person_uuid: UUID) -> None:
    _delete(DimPerson, person_uuid)


@app.post("/people/bulk", status_code=201, response_model=None, tags=["People - Bulk"], summary="Create people in bulk")
def create_people_bulk(payload: list[PersonPayload]) -> list[dict[str, Any]]:
    return _bulk_create(DimPerson, [item.model_dump() for item in payload])


@app.patch("/people/bulk", response_model=None, tags=["People - Bulk"], summary="Update people in bulk")
def update_people_bulk(payload: list[PersonBulkUpdate]) -> list[dict[str, Any]]:
    return _bulk_update(DimPerson, "person_uuid", [item.model_dump(exclude_unset=True) for item in payload])


@app.post("/citations", status_code=201, response_model=None, tags=["Citations"], summary="Create a citation")
def create_citation(payload: CitationPayload) -> dict[str, Any]:
    _validate_vector(payload.summarized_quote_vector)
    return _create(DimCitation, payload.model_dump())


@app.get("/citations", response_model=None, tags=["Citations"], summary="List citations")
def list_citations(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(DimCitation, limit)


@app.get("/citations/search", response_model=None, tags=["Citations"], summary="Search citations")
def search_citations(
    query: str = Query(min_length=1),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    with get_session() as session:
        statement = (
            select(DimCitation)
            .where(or_(DimCitation.exact_quote.ilike(pattern), DimCitation.summarized_quote.ilike(pattern)))
            .limit(limit)
        )
        return [_as_dict(item) for item in session.scalars(statement).all()]


@app.get("/citations/{citation_uuid}", response_model=None, tags=["Citations"], summary="Get a citation")
def get_citation(citation_uuid: UUID) -> dict[str, Any]:
    return _get(DimCitation, citation_uuid)


@app.put("/citations/{citation_uuid}", response_model=None, tags=["Citations"], summary="Replace a citation")
def update_citation(citation_uuid: UUID, payload: CitationUpdate) -> dict[str, Any]:
    _validate_vector(payload.summarized_quote_vector)
    return _update(DimCitation, citation_uuid, payload.model_dump(exclude_unset=True))


@app.delete("/citations/bulk", tags=["Citations - Bulk"], summary="Delete citations in bulk")
def delete_citations_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimCitation, "citation_uuid", payload.ids)


@app.delete("/citations/{citation_uuid}", status_code=204, response_model=None, tags=["Citations"], summary="Delete a citation")
def delete_citation(citation_uuid: UUID) -> None:
    _delete(DimCitation, citation_uuid)


@app.post("/citations/bulk", status_code=201, response_model=None, tags=["Citations - Bulk"], summary="Create citations in bulk")
def create_citations_bulk(payload: list[CitationPayload]) -> list[dict[str, Any]]:
    for item in payload:
        _validate_vector(item.summarized_quote_vector)
    return _bulk_create(DimCitation, [item.model_dump() for item in payload])


@app.patch("/citations/bulk", response_model=None, tags=["Citations - Bulk"], summary="Update citations in bulk")
def update_citations_bulk(payload: list[CitationBulkUpdate]) -> list[dict[str, Any]]:
    for item in payload:
        _validate_vector(item.summarized_quote_vector)
    return _bulk_update(DimCitation, "citation_uuid", [item.model_dump(exclude_unset=True) for item in payload])


@app.post("/topics", status_code=201, response_model=None, tags=["Topics"], summary="Create a topic")
def create_topic(payload: TopicPayload) -> dict[str, Any]:
    _validate_vector(payload.topic_description_vector)
    return _create(DimTopic, payload.model_dump())


@app.get("/topics", response_model=None, tags=["Topics"], summary="List topics")
def list_topics(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(DimTopic, limit)


@app.get("/topics/{topic_uuid}", response_model=None, tags=["Topics"], summary="Get a topic")
def get_topic(topic_uuid: UUID) -> dict[str, Any]:
    return _get(DimTopic, topic_uuid)


@app.put("/topics/{topic_uuid}", response_model=None, tags=["Topics"], summary="Replace a topic")
def update_topic(topic_uuid: UUID, payload: TopicUpdate) -> dict[str, Any]:
    _validate_vector(payload.topic_description_vector)
    return _update(DimTopic, topic_uuid, payload.model_dump(exclude_unset=True))


@app.delete("/topics/bulk", tags=["Topics - Bulk"], summary="Delete topics in bulk")
def delete_topics_bulk(payload: BulkDeleteRequest) -> dict[str, int]:
    return _bulk_delete(DimTopic, "topic_uuid", payload.ids)


@app.delete("/topics/{topic_uuid}", status_code=204, response_model=None, tags=["Topics"], summary="Delete a topic")
def delete_topic(topic_uuid: UUID) -> None:
    _delete(DimTopic, topic_uuid)


@app.post("/topics/bulk", status_code=201, response_model=None, tags=["Topics - Bulk"], summary="Create topics in bulk")
def create_topics_bulk(payload: list[TopicPayload]) -> list[dict[str, Any]]:
    for item in payload:
        _validate_vector(item.topic_description_vector)
    return _bulk_create(DimTopic, [item.model_dump() for item in payload])


@app.patch("/topics/bulk", response_model=None, tags=["Topics - Bulk"], summary="Update topics in bulk")
def update_topics_bulk(payload: list[TopicBulkUpdate]) -> list[dict[str, Any]]:
    for item in payload:
        _validate_vector(item.topic_description_vector)
    return _bulk_update(DimTopic, "topic_uuid", [item.model_dump(exclude_unset=True) for item in payload])


@app.post("/attitudes", status_code=201, response_model=None, tags=["Attitudes"], summary="Create an attitude")
def create_attitude(payload: AttitudePayload) -> dict[str, Any]:
    return _create(FctAttitude, payload.model_dump())


@app.get("/attitudes", response_model=None, tags=["Attitudes"], summary="List attitudes")
def list_attitudes(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _list(FctAttitude, limit)


@app.get("/attitudes/{topic_uuid}/{citation_uuid}", response_model=None, tags=["Attitudes"], summary="Get an attitude")
def get_attitude(topic_uuid: UUID, citation_uuid: UUID) -> dict[str, Any]:
    return _get(FctAttitude, (topic_uuid, citation_uuid))


@app.put("/attitudes/{topic_uuid}/{citation_uuid}", response_model=None, tags=["Attitudes"], summary="Replace an attitude")
def update_attitude(topic_uuid: UUID, citation_uuid: UUID, payload: AttitudeUpdate) -> dict[str, Any]:
    return _update(FctAttitude, (topic_uuid, citation_uuid), payload.model_dump(exclude_unset=True))


@app.delete("/attitudes/bulk", tags=["Attitudes - Bulk"], summary="Delete attitudes in bulk")
def delete_attitudes_bulk(payload: BulkAttitudeDeleteRequest) -> dict[str, int]:
    return _bulk_delete_attitudes(payload.keys)


@app.delete("/attitudes/{topic_uuid}/{citation_uuid}", status_code=204, response_model=None, tags=["Attitudes"], summary="Delete an attitude")
def delete_attitude(topic_uuid: UUID, citation_uuid: UUID) -> None:
    _delete(FctAttitude, (topic_uuid, citation_uuid))


@app.post("/attitudes/bulk", status_code=201, response_model=None, tags=["Attitudes - Bulk"], summary="Create attitudes in bulk")
def create_attitudes_bulk(payload: list[AttitudePayload]) -> list[dict[str, Any]]:
    return _bulk_create(FctAttitude, [item.model_dump() for item in payload])


@app.patch("/attitudes/bulk", response_model=None, tags=["Attitudes - Bulk"], summary="Update attitudes in bulk")
def update_attitudes_bulk(payload: list[AttitudeBulkUpdate]) -> list[dict[str, Any]]:
    values = [item.model_dump(exclude_unset=True) for item in payload]
    with get_session() as session:
        result = []
        for item_values in values:
            key = (item_values.pop("topic_uuid"), item_values.pop("citation_uuid"))
            item = session.get(FctAttitude, key)
            if item is None:
                raise HTTPException(status_code=404, detail=f"FctAttitude {key} not found")
            for field, value in item_values.items():
                setattr(item, field, value)
            result.append(item)
        session.flush()
        return [_as_dict(item) for item in result]


@app.post("/topics/search", response_model=None, tags=["Topics"], summary="Search topics by embedding")
def search_topics(embedding: list[float], limit: int = Query(10, ge=1, le=100)) -> list[dict[str, Any]]:
    _validate_vector(embedding)
    with get_session() as session:
        statement = (
            select(DimTopic)
            .where(DimTopic.topic_description_vector.is_not(None))
            .order_by(DimTopic.topic_description_vector.cosine_distance(embedding))
            .limit(limit)
        )
        return [_as_dict(item) for item in session.scalars(statement).all()]
