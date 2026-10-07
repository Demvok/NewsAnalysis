from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import dateutil.parser
import feedparser
import httpx
from pydantic import BaseModel, Field

from app.s_scope.ingestion.sources import FeedSource

logger = logging.getLogger(__name__)
USER_AGENT = "NewsAnalysis ingestion crawler/1.0"


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    clean_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query)
        if not key.startswith("utm_") and key.lower() not in {"fbclid", "gclid", "ref", "from", "rss"}
    ]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.params, urlencode(clean_query), ""))


def parse_datetime(entry: Dict[str, Any]) -> Optional[datetime]:
    time_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if time_struct:
        try:
            return datetime(*time_struct[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    raw_date = entry.get("published") or entry.get("updated") or entry.get("created")
    if isinstance(raw_date, str):
        try:
            parsed = dateutil.parser.parse(raw_date)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    return None


class RawFeedEntry(BaseModel):
    url: str
    title: str
    source_name: str
    language: str
    published_at: Optional[datetime] = None
    author: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class FeedReader:
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.headers = {"User-Agent": USER_AGENT}

    async def fetch_feed(self, source: FeedSource, client: httpx.AsyncClient) -> List[RawFeedEntry]:
        try:
            response = await client.get(source.url, headers=self.headers, follow_redirects=True, timeout=self.timeout)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
        except Exception as error:
            logger.warning("Error fetching feed %s (%s): %s", source.name, source.url, error)
            return []

        entries: List[RawFeedEntry] = []
        for item in parsed.entries:
            url = canonicalize_url(item.get("link") or "")
            title = (item.get("title") or "").strip()
            if not url.startswith("http") or not title:
                continue
            tags = [tag for tag in ((entry.get("term") or entry.get("label") or "").strip() for entry in item.get("tags", [])) if tag]
            description = item.get("summary") or item.get("description")
            entries.append(RawFeedEntry(
                url=url,
                title=title,
                source_name=source.name,
                language=source.language,
                published_at=parse_datetime(item),
                author=item.get("author"),
                category=tags[0] if tags else source.default_category,
                tags=tags,
                description=re.sub(r"<[^>]+>", "", description).strip() if description else None,
            ))
        return entries
