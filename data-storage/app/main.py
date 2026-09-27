from fastapi import FastAPI
from sqlalchemy import create_engine, text
import os

from app import fileread

app = FastAPI()

engine = create_engine(os.environ["DATABASE_URL"])


@app.get("/")
def health():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": result.scalar()}


@app.get("/article/{article_id}")
def get_article(article_id: int):
    article = fileread.get_article_by_id(article_id)
    if article:
        return article
    else:
        return {"error": "Article not found"}