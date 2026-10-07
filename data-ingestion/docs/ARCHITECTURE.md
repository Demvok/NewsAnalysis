# Ingestion architecture

`app.main` is the HTTP adapter. It validates requests and manages one background ingestion job at a time. The `app.s_scope` package owns ingestion behavior:

- `ingestion.feed_reader` fetches RSS/Atom feeds and normalizes URLs.
- `ingestion.pipeline` filters the last 24 hours, deduplicates, interleaves sources, and coordinates extraction.
- `ingestion.extractor` fetches article pages and extracts text with `trafilatura`.
- `models.article` defines Pydantic and SQLAlchemy article models.
- `storage` exports JSON and persists new articles.
- `cli` provides the same pipeline for scheduled or manual runs.

The service does not perform analysis, stance classification, or embeddings.
