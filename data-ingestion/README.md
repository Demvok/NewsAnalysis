# NewsAnalysis data ingestion: beginner guide

This service reads RSS feeds, finds articles from the last 24 hours, downloads their web pages, extracts readable text, and stores the result. It does not analyze opinions or generate embeddings yet.

## 1. Start it

From the repository root, start the whole stack:

```powershell
docker compose up --build data-ingestion
```

The ingestion API is at `http://localhost:9000`. FastAPI creates an interactive try-it-yourself page at `http://localhost:9000/docs` and a machine-readable schema at `http://localhost:9000/openapi.json`.

For local development:

```powershell
$env:PYTHONPATH = "data-ingestion"
uvicorn app.main:app --host 127.0.0.1 --port 9000 --reload
```

## 2. Try the API

Check that the service is alive:

```powershell
Invoke-RestMethod http://localhost:9000/health
```

See the configured RSS sources:

```powershell
Invoke-RestMethod http://localhost:9000/sources
```

Start a small metadata-only run. This reads RSS but does not download article pages:

```powershell
$job = Invoke-RestMethod -Method Post -Uri http://localhost:9000/ingest `
	-ContentType 'application/json' `
	-Body '{"target_count":10,"metadata_only":true}'
$job
```

Poll the job using its returned ID:

```powershell
Invoke-RestMethod "http://localhost:9000/ingest/$($job.job_id)"
```

For a real full-text run:

```powershell
$body = @{ target_count = 10; metadata_only = $false } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:9000/ingest -ContentType 'application/json' -Body $body
```

After a completed full-text run, inspect stored articles:

```powershell
Invoke-RestMethod 'http://localhost:9000/articles?limit=10'
```

The API allows one active ingestion job at a time. Use `DELETE /ingest/{job_id}` to cancel a queued or running job.

## 3. Endpoint map

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Short API guide and useful links |
| `GET` | `/docs` | Interactive Swagger UI |
| `GET` | `/health` | Service and database status |
| `GET` | `/config` | Safe, non-secret settings |
| `GET` | `/sources` | RSS sources used by the scraper |
| `POST` | `/ingest` | Start a background ingestion job |
| `GET` | `/ingest` | List known jobs |
| `GET` | `/ingest/{job_id}` | Read one job's status |
| `DELETE` | `/ingest/{job_id}` | Cancel a job |
| `GET` | `/articles?limit=50` | Read stored articles |
| `GET` | `/articles/{article_id}` | Read one stored article |

## 4. What the pipeline does

1. Fetches the RSS feeds listed in `app/s_scope/ingestion/sources.py`.
2. Keeps entries with a publication date in the previous 24 hours.
3. Removes duplicate URLs and balances articles across sources.
4. In full mode, downloads pages and extracts text with `trafilatura`.
5. Writes JSON files and inserts new articles into the configured database.

The default database setting is SQLite. Docker Compose supplies PostgreSQL through `DATABASE_URL`. The database table is created automatically.

## 5. CLI alternative

The same pipeline can run without HTTP:

```powershell
$env:PYTHONPATH = "data-ingestion"
python -m app.s_scope.cli ingest --target 10 --metadata-only
```

## 6. Where to tinker

- Add or remove feeds in `app/s_scope/ingestion/sources.py`.
- Change date filtering and concurrency in `app/s_scope/ingestion/pipeline.py`.
- Change web text extraction in `app/s_scope/ingestion/extractor.py`.
- Change database fields in `app/s_scope/models/article.py`.
- Change or add HTTP endpoints in `app/main.py`.

Run checks with:

```powershell
$env:PYTHONPATH = "data-ingestion"
python -m pytest data-ingestion/tests -q
```
