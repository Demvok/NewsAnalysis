# Ingestion walkthrough

A request to `POST /ingest` creates a job and runs `IngestionPipeline` in the background. The pipeline fetches the configured RSS sources concurrently, keeps entries published in the previous 24 hours, canonicalizes and deduplicates URLs, then interleaves sources for balanced collection.

Full mode downloads article pages and extracts readable text with `trafilatura`. Articles shorter than `min_content_length` are skipped. Results are written to the full JSON file, the compact index file, and the configured SQLAlchemy database. Existing article IDs and URLs are ignored by the database layer.

Metadata-only mode uses RSS fields only. It writes the compact index but does not download article pages, write full JSON, or persist to the database.

The CLI and systemd timer call the same pipeline directly. Docker deployments should use the API and mount persistent storage for output files if JSON artifacts are required.
