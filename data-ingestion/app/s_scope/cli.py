from __future__ import annotations

import argparse
import asyncio

from app.s_scope.config import settings
from app.s_scope.ingestion.pipeline import IngestionPipeline


def parse_args(args=None):
    parser = argparse.ArgumentParser(prog="s-scope", description="NewsAnalysis RSS ingestion engine")
    ingest = parser.add_subparsers(dest="command").add_parser("ingest", help="Collect and process news articles")
    ingest.add_argument("--target", "-t", type=int, default=500)
    ingest.add_argument("--output", "-o", default="data/articles_500.json")
    ingest.add_argument("--index-output", default="data/articles_index.json")
    ingest.add_argument("--db-url", default=settings.database_url)
    ingest.add_argument("--concurrency", type=int, default=settings.ingestion_concurrency)
    ingest.add_argument("--timeout", type=float, default=settings.request_timeout)
    ingest.add_argument("--metadata-only", action="store_true")
    return parser.parse_args(args)


def main() -> None:
    args = parse_args()
    if args.command not in (None, "ingest"):
        raise SystemExit(f"Unknown command: {args.command}")
    asyncio.run(IngestionPipeline(concurrency=args.concurrency, timeout=args.timeout, min_content_length=settings.min_content_length).run(
        target_count=getattr(args, "target", 500),
        output_json="" if getattr(args, "metadata_only", False) else getattr(args, "output", "data/articles_500.json"),
        output_index=getattr(args, "index_output", "data/articles_index.json"),
        db_url=None if getattr(args, "metadata_only", False) else getattr(args, "db_url", settings.database_url),
        metadata_only=getattr(args, "metadata_only", False),
    ))


if __name__ == "__main__":
    main()
