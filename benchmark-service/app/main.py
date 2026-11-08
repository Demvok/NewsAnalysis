from __future__ import annotations

from datetime import datetime, timezone
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from .config import Settings
from .datasets import load_cases
from .models import ModelConfig, RunCreateRequest, RunResponse
from .runner import run_cases
from .scoring import evaluate, summarize
from .storage import load_runs, save_run
import json
from pathlib import Path

settings = Settings.from_env()
settings.results_dir.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="NewsAnalysis Benchmark Service", version="1.0.0")


def _report_files() -> list[tuple[str, str, Path]]:
    root = settings.results_dir
    files = [
        ("original", "Original benchmark report", root / "benchmark_report.json"),
        ("audit", "Metric methodology audit", root / "audit" / "benchmark_audit.json"),
    ]
    files.extend(
        ("targeted", f"Targeted comparison {path.parent.name}", path)
        for path in sorted(root.glob("targeted_*/targeted_report.json"))
    )
    return [(kind, label, path) for kind, label, path in files if path.exists()]


@app.get("/api/reports")
def reports() -> list[dict]:
    result = []
    for kind, label, path in _report_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        result.append({
            "id": path.relative_to(settings.results_dir).as_posix(),
            "kind": kind,
            "label": label,
            "report_id": payload.get("report_id"),
            "generated_at": payload.get("generated_at") or payload.get("report_id"),
            "models": payload.get("models", {}),
            "dataset": payload.get("dataset", {
                "synthetic_case_count": payload.get("synthetic_case_count"),
                "real_case_count": payload.get("real_case_count"),
            }),
        })
    return result


@app.get("/api/reports/{report_id:path}")
def report(report_id: str) -> dict:
    path = (settings.results_dir / report_id).resolve()
    root = settings.results_dir.resolve()
    if root not in path.parents or path.suffix != ".json" or not path.exists():
        raise HTTPException(status_code=404, detail="Benchmark report not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


def _recomputed_summary(run: dict) -> dict:
    results = run.get("results", [])
    if not results:
        return run.get("summary", {})
    evaluations = []
    for result in results:
        previous = result.get("evaluation") or {}
        structured = previous.get("structured_output") or {}
        performance = previous.get("performance") or {}
        evaluations.append({
            **result,
            "evaluation": evaluate(
                result.get("input") or {},
                result.get("parsed_response"),
                bool(structured.get("json_valid")),
                bool(structured.get("schema_compliant")),
                float(performance.get("execution_time_ms") or 0),
                {
                    "input_tokens": performance.get("input_tokens"),
                    "output_tokens": performance.get("output_tokens"),
                    "tokens_per_second": performance.get("tokens_per_second"),
                },
            ),
        })
    return summarize(evaluations)


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
    return [{**run, "summary": _recomputed_summary(run), "results": []} for run in load_runs(settings.results_dir)]


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
        "started_at": None,
        "progress": {"completed": 0, "total": 0, "percent": 0},
        "error": None,
        "results": [],
        "summary": {},
        "configurations": [item.model_dump(mode="json") for item in request.configurations],
    }
    save_run(settings.results_dir, run)
    return run


def _update_run(run_id: str, updates: dict) -> None:
    runs = {run["run_id"]: run for run in load_runs(settings.results_dir)}
    run = runs.get(run_id)
    if run is None:
        return
    run.update(updates)
    save_run(settings.results_dir, run)


def _execute_run(run: dict) -> None:
    run_id = run["run_id"]
    try:
        cases = load_cases(settings.data_dir, run["dataset"])
        configs = [
            ModelConfig.model_validate(item)
            for item in run.get("configurations", [])
        ] or [ModelConfig(model_id=settings.models[model]) for model in run["model_ids"]]
        total = len(cases) * len(configs) * run["repetitions"]
        _update_run(run_id, {
            "status": "RUNNING",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "progress": {"completed": 0, "total": total, "percent": 0},
            "results": [],
            "error": None,
        })

        def on_progress(progress: dict, result: dict) -> None:
            current = next((item for item in load_runs(settings.results_dir) if item["run_id"] == run_id), None)
            results = list((current or {}).get("results", []))
            results.append(result)
            _update_run(run_id, {"progress": progress, "results": results})

        results, summary = run_cases(settings, cases, configs, run["repetitions"], on_progress)
        _update_run(run_id, {
            "status": "COMPLETED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "progress": {"completed": len(results), "total": total, "percent": 100},
            "results": results,
            "summary": summary,
        })
    except Exception as exc:
        _update_run(run_id, {
            "status": "FAILED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        })


@app.post("/api/runs/{run_id}/start", response_model=RunResponse, status_code=202)
def start_run(run_id: str) -> dict:
    runs = {run["run_id"]: run for run in load_runs(settings.results_dir)}
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    if run["status"] == "RUNNING":
        raise HTTPException(status_code=409, detail="Benchmark run is already running")
    if run["status"] == "COMPLETED":
        raise HTTPException(status_code=409, detail="Completed benchmark runs are immutable")
    run["status"] = "RUNNING"
    run["started_at"] = datetime.now(timezone.utc).isoformat()
    save_run(settings.results_dir, run)
    threading.Thread(target=_execute_run, args=(run,), daemon=True, name=f"benchmark-{run_id}").start()
    return run


@app.get("/api/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> dict:
    run = next((item for item in load_runs(settings.results_dir) if item["run_id"] == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return {**run, "summary": _recomputed_summary(run)}
