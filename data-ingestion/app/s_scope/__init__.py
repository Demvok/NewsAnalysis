from app.s_scope.config import Settings, settings
from app.s_scope.ingestion.pipeline import IngestionPipeline


def main() -> None:
	from app.s_scope.cli import main as cli_main

	cli_main()


__all__ = ["IngestionPipeline", "Settings", "main", "settings"]
