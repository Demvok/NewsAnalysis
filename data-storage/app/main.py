import logging

from fastapi import FastAPI
from sqlalchemy import create_engine, text
import os

from app import fileread

logger = logging.getLogger("data-storage")

app = FastAPI(
    title="NewsAnalysis-DataStorage",
    description="Full API for NewsAnalysis data storage",
    version="0.0.1"
)

engine = create_engine(os.environ["DATABASE_URL"])


@app.get("/health", summary="Health check")
def health(summary="Health check"):
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        return {"message": "Data Storage API Server is running", "version": app.version,
                "db_status": "ok" if result.scalar() == 1 else "error"}


@app.get("/article/{article_id}")
def get_article(article_id: int):
    article = fileread.get_article_by_id(article_id)
    if article:
        return article
    else:
        return {"error": "Article not found"}