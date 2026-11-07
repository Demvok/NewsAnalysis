from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import BenchmarkCase


def load_cases(data_dir: Path, dataset: str) -> list[BenchmarkCase]:
    path = data_dir / ("synthetic.jsonl" if dataset == "synthetic" else "real_news.jsonl")
    if not path.exists():
        raise FileNotFoundError(f"benchmark dataset not found: {path}")
    cases: list[BenchmarkCase] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                cases.append(BenchmarkCase.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"invalid benchmark case at {path}:{line_number}: {exc}") from exc
    return cases


def case_dicts(cases: Iterable[BenchmarkCase]) -> list[dict]:
    return [case.model_dump(mode="json") for case in cases]

