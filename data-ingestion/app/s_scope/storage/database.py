from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.s_scope.config import settings
from app.s_scope.models.article import Article, ArticleModel, Base

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.database_url
        self.engine = create_engine(self.db_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.init_db()

    def init_db(self) -> None:
        Base.metadata.create_all(bind=self.engine)

    def save_articles(self, articles: List[Article]) -> int:
        if not articles:
            return 0

        inserted_count = 0
        with self.SessionLocal() as session:
            for article in articles:
                existing = session.execute(
                    select(ArticleModel.id).where(
                        (ArticleModel.id == article.id) | (ArticleModel.url == article.url)
                    )
                ).scalar_one_or_none()
                if existing:
                    continue
                session.add(ArticleModel.from_pydantic(article))
                inserted_count += 1
            session.commit()

        logger.info("Saved %s new articles to database.", inserted_count)
        return inserted_count

    def get_all_articles(self, limit: Optional[int] = None) -> List[Article]:
        with Session(self.engine) as session:
            query = select(ArticleModel).order_by(ArticleModel.published_at.desc())
            if limit:
                query = query.limit(limit)
            models = session.execute(query).scalars().all()
            return [model.to_pydantic() for model in models]

    def get_article(self, article_id: str) -> Optional[Article]:
        with Session(self.engine) as session:
            model = session.get(ArticleModel, article_id)
            return model.to_pydantic() if model else None
