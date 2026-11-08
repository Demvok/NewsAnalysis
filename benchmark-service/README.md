# Benchmark service

This service runs isolated, sequential benchmark calls against an
OpenAI-compatible local endpoint. It does not invoke or modify the production
analysis pipeline. Synthetic labels are hand-authored in `data/synthetic.jsonl`;
real-news cases are exploratory reference data and are not independently
verified ground truth.

Run locally:

```powershell
cd benchmark-service
python -m uvicorn app.main:app --port 7100
```

The management dashboard exposes the complete workflow under **Benchmarks**:

1. Select an experiment type, dataset, repetitions, and models.
2. Create and start a run.
3. Keep the run detail panel open for live progress polling.
4. Inspect summary cards, quality bars, configuration, every case result,
   expected input, parsed response, and raw response.
5. Open the JSON endpoint for programmatic export.

The service also supports `GET /api/models`, `GET /api/datasets`,
`GET /api/runs`, `POST /api/runs`, `POST /api/runs/{run_id}/start`, and
`GET /api/runs/{run_id}`. Runs execute in a background worker and persist
progress after each model call, so the GUI remains responsive and can recover
the latest state after refresh.

Set `BENCHMARK_LLM_URL` and model environment variables to match the local
LM Studio/OpenAI-compatible server.
