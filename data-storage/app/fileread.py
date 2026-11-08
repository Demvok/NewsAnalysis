from pathlib import Path
import json
from typing import Any

TEMP_ARTICLES_DIR = Path(__file__).with_name("_data")


def _records_from_contents(contents: Any, file_name: str) -> list[dict[str, Any]]:
    if isinstance(contents, list):
        records = contents
    elif isinstance(contents, dict):
        list_values = [value for value in contents.values() if isinstance(value, list)]
        if len(list_values) != 1:
            raise ValueError(
                f"Temporary article file must contain an article array: {file_name}"
            )
        records = list_values[0]
    else:
        raise ValueError(
            f"Temporary article file must contain an article array: {file_name}"
        )

    normalized: list[dict[str, Any]] = []
    for article in records:
        if not isinstance(article, dict):
            raise ValueError(
                f"Temporary article records must be JSON objects: {file_name}"
            )
        tags = article.get("tags") or article.get("category")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                pass
        normalized.append(
            {
                "article_id": article.get("article_id", article.get("id")),
                "article_title": article.get("article_title", article.get("title")),
                "article_content": article.get(
                    "article_content", article.get("content_c", article.get("content"))
                ),
                "author": article.get("author"),
                "source": article.get("source"),
                "language": article.get("language"),
                "url": article.get("url"),
                "published_at": article.get("published_at"),
                "parsed_at": article.get("parsed_at"),
                "modified_at": article.get("modified_at"),
                "tags": tags,
                "description": article.get("description"),
            }
        )
    return normalized


def read_temp_articles() -> list[dict[str, Any]]:
    """Read and normalize the temporary article export files.

    This importer is intentionally temporary and is kept separate from the
    normal ingestion flow.
    """
    articles: list[dict[str, Any]] = []
    for file_path in sorted(TEMP_ARTICLES_DIR.glob("*.json")):
        with file_path.open(encoding="utf-8") as file:
            contents = json.load(file)
        articles.extend(_records_from_contents(contents, file_path.name))
    return articles