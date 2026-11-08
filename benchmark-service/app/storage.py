from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_run(directory: Path, run: dict[str, Any]) -> None:
    runs_directory = directory / "runs"
    runs_directory.mkdir(parents=True, exist_ok=True)
    path = runs_directory / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")


def load_runs(directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    runs = []
    paths = list(directory.glob("*.json")) + list((directory / "runs").glob("*.json"))
    seen: set[Path] = set()
    for path in sorted(paths, reverse=True):
        if path in seen:
            continue
        seen.add(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not payload.get("run_id"):
            continue
        runs.append(payload)
    return runs
