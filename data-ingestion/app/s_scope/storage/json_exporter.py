from __future__ import annotations

import json
from pathlib import Path
from typing import List

from app.s_scope.models.article import Article, ArticleIndexItem


def export_articles_to_json(articles: List[Article], filepath: str, indent: int = 2) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([article.model_dump(mode="json") for article in articles], ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )


def export_index_to_json(articles: List[Article], filepath: str, indent: int = 2) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    index = [ArticleIndexItem.from_article(article).model_dump(mode="json") for article in articles]
    path.write_text(json.dumps(index, ensure_ascii=False, indent=indent), encoding="utf-8")
