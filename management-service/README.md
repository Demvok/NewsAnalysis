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
The storage health response is inspected for `db_status`. LLM health is
reported as `UNKNOWN/NOT_PROBED` because the existing analysis API has no
lightweight provider-readiness endpoint; the management service does not issue
an expensive test completion request.

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
