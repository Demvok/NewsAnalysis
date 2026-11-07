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

Create a run with `POST /api/runs`, then start it with
`POST /api/runs/{run_id}/start`. Set `BENCHMARK_LLM_URL` and model environment
variables to match the local LM Studio/OpenAI-compatible server.
