# Daily ingestion

For Linux hosts with systemd, install the user timer from `scripts/install_user_timer.sh`. It runs `scripts/daily_ingest.sh` daily at 03:00 and stores timestamped JSON files under `data/runs`.

The timer uses the local CLI, not the HTTP API. Configure `S_SCOPE_TARGET`, `S_SCOPE_OUTPUT_DIR`, `S_SCOPE_CONCURRENCY`, `S_SCOPE_TIMEOUT`, and `DATABASE_URL` in the service environment. Docker deployments should use an external scheduler to call `POST /ingest` instead.
