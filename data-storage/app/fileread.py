from pathlib import Path
import json
from typing import Any

import pandas as pd

df = pd.read_csv(Path(__file__).with_name('dimArticle.csv'), index_col=0).reset_index('article_id')


TEMP_ARTICLES_DIR = Path(__file__).with_name("_tmp")


def read_temp_articles() -> list[dict[str, Any]]:
    """Read and normalize the temporary article export files.

    This importer is intentionally temporary and is kept separate from the
    normal ingestion flow.
    """
    articles: list[dict[str, Any]] = []
    for file_path in sorted(TEMP_ARTICLES_DIR.glob("*.json")):
        with file_path.open(encoding="utf-8") as file:
            contents = json.load(file)
        if not isinstance(contents, list):
            raise ValueError(f"Temporary article file must contain a JSON array: {file_path.name}")
        for article in contents:
            if not isinstance(article, dict):
                raise ValueError(f"Temporary article records must be JSON objects: {file_path.name}")
            articles.append(
                {
                    "article_id": article.get("id"),
                    "article_title": article.get("title"),
                    "article_content": article.get("content_c"),
                    "author": article.get("author"),
                    "source": article.get("source"),
                    "language": article.get("language"),
                    "url": article.get("url"),
                    "published_at": article.get("published_at"),
                    "parsed_at": article.get("parsed_at"),
                    "modified_at": article.get("modified_at"),
                    "tags": article.get("tags") or article.get("category"),
                    "description": article.get("description"),
                }
            )
    return articles


def get_article_by_id(article_id):
    article = df[df['article_id'] == article_id]
    if not article.empty:
        return article.iloc[0].to_dict()
    else:
        return None