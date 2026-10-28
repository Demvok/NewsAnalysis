# NewsAnalysis Management Service

The management service coordinates supported operations through the existing
HTTP APIs. It does not import analysis or storage internals and does not
access the application PostgreSQL database.

## Local endpoints

When started through Docker Compose:

- API: `http://localhost:9000`
- OpenAPI documentation: `http://localhost:9000/docs`

The Phase 1 API includes:

- `GET /health` and `GET /api/services`
- `POST /api/jobs`, `GET /api/jobs`, and job detail/progress/stage endpoints
- `GET /api/articles` plus `POST /api/tasks`, `POST /api/tasks/fresh`, and
  `GET /api/tasks/{id}` for multi-article processing tasks with aggregate
  citation, speaker, topic, and attitude counters.
- `POST /api/tasks/{id}/cancel` for cooperative cancellation of task jobs.
- safe cancellation requests and retry for failed/cancelled jobs
- `GET/PATCH /api/settings` for timeout settings
- `GET /api/pipeline/history`
- `POST /internal/events`
- `GET /api/logs` and `GET /api/logs/errors` for bounded structured log
  exploration, with mounted legacy text logs used only as a fallback.
- `GET /api/metrics/llm`, `GET /api/metrics/stages`, and
  `GET /api/metrics/jobs` for aggregate telemetry.
- `GET /api/events/stream` for bounded Server-Sent Events delivery. Clients
  can resume using the `last_event_id` query parameter.

Job history is stored in the management-owned SQLite database at
`MANAGEMENT_DATABASE_PATH`. The database is mounted by the `management_data`
Compose volume.

## Execution and recovery

Article analysis is currently synchronous in the analysis service. Therefore,
cancellation only prevents queued work or requests cancellation after the
active stage finishes; it does not terminate an in-flight LLM request.

If the management service restarts while a job is `PENDING`, `RUNNING`, or
`RETRYING`, the job is marked `FAILED` with
`MANAGEMENT_RESTART`. It is not automatically replayed, preventing duplicate
analysis execution.

Use an `Idempotency-Key` header when creating a job to safely repeat a
submission request.

## Health semantics

The management service distinguishes API availability from dependency health.
The storage health response is inspected for `db_status`. LLM health probes the configured LM Studio/OpenAI-compatible `/models`
endpoint. This verifies provider API availability and model discovery, not a
successful completion.

## Phase 2 observability

The analysis callback around the existing LangChain/OpenAI-compatible client
emits one `llm_call` telemetry record per observed model call. Events retain
job, request, correlation, trace, span, article, stage, model, provider,
duration, finish reason, retry, error, and token dimensions when available.
Provider-reported token counts are separate from character-based estimates;
estimates are never included in measured totals. Time-to-first-token is null
unless a streaming callback observes a token, and throughput is null unless
measured output tokens and a valid generation interval are available.

Pipeline stage events cover extraction, person resolution, topic resolution,
attitude persistence, and inconsistency comparison. Structured events are
durable in the management-owned SQLite database, deduplicated by event ID, and
are the primary log and metrics source. Legacy mounted text logs remain a
bounded compatibility fallback.

Prompt and response content is not stored. When
`MANAGEMENT_CAPTURE_LLM_CONTENT=true`, only SHA-256 hashes are included for
debug correlation; this is disabled by default and should be used only with
short retention. The current LM Studio-compatible endpoint may omit usage
metadata and normally does not provide streaming token callbacks, so token
counts, TTFT, and throughput can legitimately be unavailable.

## Phase 3 dashboard

The React/Vite dashboard is available at `http://localhost:3000` when the
Compose stack is running. It uses the management API as its only source of
operational data and includes:

- overview KPIs with historical aggregates and bounded live SVG charts;
- job filtering, detail views, stage history, safe cancellation, and retry;
- LLM call inspection sorted by slowest duration;
- structured log search by severity, service, job, article, stage, trace, and
  text;
- service health and explicitly supported runtime configuration;
- multi-article Tasks control with fresh-article discovery, selection, task
  cancellation, aggregate progress, and live telemetry refresh.

The dashboard consumes `/api/events/stream` through Server-Sent Events and
reconnects with the last received event ID. Client-side live data is bounded
to the most recent 60 events, while historical tables are bounded API
requests; the browser never loads unrestricted log history. The backend
remains authoritative for job state, metrics, validation, and action safety.

## Phase 4 experiments

Experiments extend the management-owned SQLite metadata store. An experiment
contains an immutable article selection and configuration snapshot, including
model configuration, prompt/template versions, and pipeline parameters. The
`POST /api/experiments/{id}/start` operation creates ordinary management jobs
through the existing coordinator and records the experiment-to-job
relationship; it does not duplicate analysis logic.

Available endpoints include:

- `POST/GET /api/experiments`
- `GET /api/experiments/{id}`
- `POST /api/experiments/{id}/start`
- `POST /api/experiments/compare`
- `POST /api/experiments/{id}/evaluation`
- `GET /api/experiments/{id}/export?format=json|csv`

Experiment metrics are operational measurements derived from the associated
jobs and structured telemetry: processing time, LLM calls and measured
tokens per article, latency, throughput, failure/retry rates, and stage
timings. Where stage telemetry contains the relevant counts, summaries also
report speakers, extracted citations, resolved topics, persisted attitudes,
and inconsistency comparisons. Missing provider or analysis measurements
remain unavailable. A larger count of citations, topics, or contradictions
is not treated as a quality score.

Precision, recall, and F1 are only returned after explicit ground-truth
evaluation data is submitted through the evaluation endpoint. Configuration
snapshots remove sensitive keys such as API keys, passwords, credentials, and
secrets before persistence. Existing experiment records are never overwritten
when another experiment is created with different settings.

## Production-readiness operations

Start and inspect the stack with:

```text
docker compose up -d --build
docker compose ps
curl http://localhost:9000/health
```

The management database is stored in the `management_data` volume. Restarting
the management container preserves jobs, stages, telemetry, and experiments.
The service deliberately marks interrupted active jobs as
`MANAGEMENT_RESTART` rather than replaying them, because replaying a
synchronous analysis request could duplicate analytical side effects.

For troubleshooting, inspect `docker compose logs management-service`,
`docker compose logs analysis-layer`, and the dashboard Log Explorer. The
dashboard and management API use the same-origin Compose proxy; the browser
does not receive service credentials or unrestricted environment variables.

The current deployment is intended for a trusted local network. The
management API does not yet provide authentication or authorization, and the
internal telemetry endpoint is reachable wherever port 9000 is exposed.
Place it behind a trusted reverse proxy or network boundary before exposing it
outside the local environment. Adding authentication is a follow-up security
hardening step.
