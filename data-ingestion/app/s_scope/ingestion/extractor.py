from __future__ import annotations

import logging
import re
from datetime import timezone
from typing import Optional

import dateutil.parser
import httpx
import trafilatura

from app.s_scope.ingestion.feed_reader import USER_AGENT, RawFeedEntry
from app.s_scope.models.article import Article

logger = logging.getLogger(__name__)
META_AUTHOR_REGEXES = [
    re.compile(r'<meta\s+name=["\']author["\']\s+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta\s+property=["\']article:author["\']\s+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta\s+name=["\']dc.creator["\']\s+content=["\']([^"\']+)["\']', re.I),
]
META_SECTION_REGEXES = [
    re.compile(r'<meta\s+property=["\']article:section["\']\s+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta\s+name=["\']section["\']\s+content=["\']([^"\']+)["\']', re.I),
]


class ArticleExtractor:
    def __init__(self, timeout: float = 15.0, min_content_length: int = 150):
        self.timeout = timeout
        self.min_content_length = min_content_length
        self.headers = {"User-Agent": USER_AGENT, "Accept-Language": "uk,en-US,en;q=0.8,de;q=0.7"}

    @staticmethod
    def _metadata_value(html: str, patterns: list[re.Pattern[str]], max_length: int) -> Optional[str]:
        for pattern in patterns:
            match = pattern.search(html)
            if match and 0 < len(match.group(1).strip()) < max_length:
                return match.group(1).strip()
        return None

    async def extract(self, entry: RawFeedEntry, client: httpx.AsyncClient) -> Optional[Article]:
        try:
            response = await client.get(entry.url, headers=self.headers, follow_redirects=True, timeout=self.timeout)
            response.raise_for_status()
        except Exception as error:
            logger.debug("Failed to fetch %s: %s", entry.url, error)
            return None

        html = response.text
        document = trafilatura.bare_extraction(html, url=entry.url, include_comments=False, include_tables=False, no_fallback=False)
        content = (document.text if document and document.text else "").strip()
        if not content:
            content = (trafilatura.extract(html, url=entry.url, include_comments=False) or "").strip()
        if len(content) < self.min_content_length:
            return None

        author = entry.author or (document.author if document else None) or self._metadata_value(html, META_AUTHOR_REGEXES, 100)
        published_at = entry.published_at
        if not published_at and document and document.date:
            try:
                published_at = dateutil.parser.parse(document.date)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
        category = entry.category
        if (not category or category == "General") and document and document.categories:
            category = document.categories[0]
        category = category or self._metadata_value(html, META_SECTION_REGEXES, 60)
        tags = list(entry.tags)
        if document and document.tags:
            tags.extend(tag for tag in document.tags if tag and tag not in tags)

        return Article.create(
            url=str(response.url) or entry.url,
            title=entry.title or (document.title if document else ""),
            content_c=content,
            author=author,
            source=entry.source_name,
            language=entry.language,
            published_at=published_at,
            category=category,
            tags=tags,
            description=entry.description or (document.description if document else None),
        )
