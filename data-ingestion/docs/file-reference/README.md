# File reference

- `app/main.py`: FastAPI health, start-job, and job-status endpoints.
- `app/s_scope/config.py`: environment-backed ingestion settings.
- `app/s_scope/cli.py`: manual and scheduled CLI entry point.
- `app/s_scope/ingestion/`: RSS reading, extraction, source definitions, and pipeline coordination.
- `app/s_scope/models/article.py`: Pydantic article schemas and SQLAlchemy table model.
- `app/s_scope/storage/`: JSON and database persistence.
- `tests/`: configuration, URL, publication-window, and model tests.
- `scripts/` and `deploy/systemd/`: optional Linux daily-ingestion scheduling.
