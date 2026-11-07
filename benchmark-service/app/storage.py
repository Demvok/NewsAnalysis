from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_run(directory: Path, run: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")


def load_runs(directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    runs = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        runs.append(json.loads(path.read_text(encoding="utf-8")))
    return runs

