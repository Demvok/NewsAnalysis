from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

import httpx

from app.s_scope.ingestion.extractor import ArticleExtractor
from app.s_scope.ingestion.feed_reader import FeedReader, RawFeedEntry
from app.s_scope.ingestion.sources import DEFAULT_FEED_SOURCES, FeedSource
from app.s_scope.models.article import Article
from app.s_scope.storage.database import DatabaseManager
from app.s_scope.storage.json_exporter import export_articles_to_json, export_index_to_json

logger = logging.getLogger(__name__)
PUBLICATION_WINDOW = timedelta(hours=24)


def filter_entries_by_publication_window(entries: List[RawFeedEntry], window_start: datetime, window_end: datetime) -> List[RawFeedEntry]:
    eligible: List[RawFeedEntry] = []
    for entry in entries:
        published_at = entry.published_at
        if published_at is None:
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        published_at = published_at.astimezone(timezone.utc)
        if window_start <= published_at <= window_end:
            eligible.append(entry)
    return eligible


def interleave_candidates(entries: List[RawFeedEntry]) -> List[RawFeedEntry]:
    by_source = defaultdict(list)
    for entry in entries:
        by_source[entry.source_name].append(entry)
    result: List[RawFeedEntry] = []
    for index in range(max((len(items) for items in by_source.values()), default=0)):
        for items in by_source.values():
            if index < len(items):
                result.append(items[index])
    return result


class IngestionPipeline:
    def __init__(self, sources: Optional[List[FeedSource]] = None, concurrency: int = 12, timeout: float = 15.0, min_content_length: int = 150):
        self.sources = sources or DEFAULT_FEED_SOURCES
        self.concurrency = concurrency
        self.feed_reader = FeedReader(timeout=timeout)
        self.extractor = ArticleExtractor(timeout=timeout, min_content_length=min_content_length)

    async def run(self, target_count: int = 500, output_json: str = "data/articles_500.json", output_index: str = "data/articles_index.json", db_url: Optional[str] = "sqlite:///data/s_scope.db", metadata_only: bool = False) -> List[Article]:
        window_end = datetime.now(timezone.utc)
        window_start = window_end - PUBLICATION_WINDOW
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
        async with httpx.AsyncClient(limits=limits, timeout=self.extractor.timeout) as client:
            feed_results = await asyncio.gather(*(self.feed_reader.fetch_feed(source, client) for source in self.sources))
            entries = [entry for result in feed_results for entry in result]
            recent = filter_entries_by_publication_window(entries, window_start, window_end)
            seen: Set[str] = set()
            candidates = interleave_candidates([entry for entry in recent if not (entry.url in seen or seen.add(entry.url))])
            articles: List[Article] = []
            if metadata_only:
                articles = [Article.create(url=e.url, title=e.title, content_c="", author=e.author, source=e.source_name, language=e.language, published_at=e.published_at, category=e.category, tags=e.tags, description=e.description) for e in candidates[:target_count]]
            else:
                semaphore = asyncio.Semaphore(self.concurrency)

                async def extract(entry: RawFeedEntry) -> Optional[Article]:
                    async with semaphore:
                        return await self.extractor.extract(entry, client)

                for index in range(0, len(candidates), max(1, self.concurrency * 2)):
                    if len(articles) >= target_count:
                        break
                    results = await asyncio.gather(*(extract(entry) for entry in candidates[index:index + max(1, self.concurrency * 2)]))
                    for article in results:
                        if article and all(existing.url != article.url for existing in articles):
                            articles.append(article)
                            if len(articles) >= target_count:
                                break

        if output_json and not metadata_only:
            export_articles_to_json(articles, output_json)
        if output_index:
            export_index_to_json(articles, output_index)
        if db_url and not metadata_only:
            DatabaseManager(db_url=db_url).save_articles(articles)
        return articles
