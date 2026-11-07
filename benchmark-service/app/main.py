from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from .config import Settings
from .datasets import load_cases
from .models import ModelConfig, RunCreateRequest, RunResponse
from .runner import run_cases
from .storage import load_runs, save_run

settings = Settings.from_env()
settings.results_dir.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="NewsAnalysis Benchmark Service", version="1.0.0")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


@app.get("/health")
def health() -> dict:
    return {"status": "HEALTHY", "service": "benchmark-service"}


@app.get("/api/models")
def models() -> list[dict[str, str]]:
    return [{"id": key, "model": value} for key, value in settings.models.items()]


@app.get("/api/datasets")
def datasets() -> list[dict]:
    result = []
    for name in ("synthetic", "real"):
        cases = load_cases(settings.data_dir, name)
        result.append({"id": name, "case_count": len(cases), "ground_truth": name == "synthetic"})
    return result


@app.get("/api/runs", response_model=list[RunResponse])
def list_runs() -> list[dict]:
    return load_runs(settings.results_dir)


@app.post("/api/runs", response_model=RunResponse, status_code=201)
def create_run(request: RunCreateRequest) -> dict:
    unknown = [model for model in request.model_ids if model not in settings.models]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown benchmark models: {', '.join(unknown)}")
    run = {
        "run_id": _run_id(),
        "experiment_type": request.experiment_type,
        "status": "CREATED",
        "dataset": request.dataset,
        "model_ids": request.model_ids,
        "repetitions": request.repetitions,
        "prompt_version": request.prompt_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "results": [],
        "summary": {},
        "configurations": [item.model_dump(mode="json") for item in request.configurations],
    }
    save_run(settings.results_dir, run)
    return run


@app.post("/api/runs/{run_id}/start", response_model=RunResponse)
def start_run(run_id: str) -> dict:
    runs = {run["run_id"]: run for run in load_runs(settings.results_dir)}
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    if run["status"] == "RUNNING":
        raise HTTPException(status_code=409, detail="Benchmark run is already running")
    cases = load_cases(settings.data_dir, run["dataset"])
    configs = [
        ModelConfig.model_validate(item)
        for item in run.get("configurations", [])
    ] or [ModelConfig(model_id=settings.models[model]) for model in run["model_ids"]]
    run["status"] = "RUNNING"
    save_run(settings.results_dir, run)
    try:
        results, summary = run_cases(settings, cases, configs, run["repetitions"])
        run.update(status="COMPLETED", completed_at=datetime.now(timezone.utc).isoformat(), results=results, summary=summary)
    except Exception as exc:
        run.update(status="FAILED", completed_at=datetime.now(timezone.utc).isoformat(), error=str(exc))
        save_run(settings.results_dir, run)
        raise HTTPException(status_code=502, detail=f"Benchmark execution failed: {exc}") from exc
    save_run(settings.results_dir, run)
    return run


@app.get("/api/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> dict:
    run = next((item for item in load_runs(settings.results_dir) if item["run_id"] == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return run
