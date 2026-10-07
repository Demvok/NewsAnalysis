from datetime import datetime, timedelta, timezone

from app.s_scope.ingestion.feed_reader import RawFeedEntry, canonicalize_url
from app.s_scope.ingestion.pipeline import filter_entries_by_publication_window
from app.s_scope.models.article import Article, generate_article_id


def test_publication_window_filters_dates():
    end = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=24)
    entries = [
        RawFeedEntry(url="https://example.com/start", title="Start", source_name="Example", language="en", published_at=start),
        RawFeedEntry(url="https://example.com/old", title="Old", source_name="Example", language="en", published_at=start - timedelta(seconds=1)),
        RawFeedEntry(url="https://example.com/unknown", title="Unknown", source_name="Example", language="en"),
    ]
    assert [entry.url for entry in filter_entries_by_publication_window(entries, start, end)] == ["https://example.com/start"]


def test_url_canonicalization():
    assert canonicalize_url("https://www.bbc.com/news/story/?utm_source=feed&ref=home#top") == "https://www.bbc.com/news/story"


def test_article_id_is_deterministic():
    article = Article.create(url="https://example.com/story", title="Test", content_c="A" * 200, source="Example", language="en")
    assert article.id == generate_article_id(article.url)
