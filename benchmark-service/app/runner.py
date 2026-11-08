from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
import httpx

from .config import Settings
from .models import BenchmarkCase, ModelConfig
from .scoring import evaluate, summarize


BENCHMARK_PROMPT = """Extract structured information from this news excerpt. Return JSON only with this shape:
{"citations":[{"exact_quote":"...","speaker":"..."}],"persons":["..."],"topics":["..."],"stance":[{"topic":"...","score":-1.0}],"contradictions":[]}
Use only evidence in the article. Score stance from -1 (opposes) to 1 (supports), 0 neutral or unclear.
ARTICLE:
"""


def _parse(content: Any) -> tuple[dict[str, Any] | None, bool, bool]:
    if isinstance(content, dict):
        parsed = content
        valid = True
    else:
        text = str(content or "").strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        try:
            parsed = json.loads(text)
            valid = isinstance(parsed, dict)
        except json.JSONDecodeError:
            return None, False, False
    schema_valid = isinstance(parsed, dict) and all(key in parsed for key in ("citations", "persons", "topics", "stance", "contradictions"))
    return parsed if isinstance(parsed, dict) else None, valid, schema_valid


def run_case(settings: Settings, case: BenchmarkCase, config: ModelConfig) -> dict[str, Any]:
    payload = {
        "model": config.model_id,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "messages": [{"role": "user", "content": BENCHMARK_PROMPT + case.article_text}],
    }
    started = time.perf_counter()
    raw: Any = None
    error = None
    tokens: dict[str, Any] = {"input_tokens": None, "output_tokens": None, "tokens_per_second": None}
    try:
        response = httpx.post(f"{settings.llm_url}/chat/completions", json=payload, timeout=settings.request_timeout_seconds)
        if response.is_error:
            raise RuntimeError(f"LLM request failed ({response.status_code}): {response.text[:1000]}")
        raw = response.json()
        message = raw.get("choices", [{}])[0].get("message", {})
        content = message.get("content", "")
        parsed, valid_json, schema_valid = _parse(content)
        usage = raw.get("usage") or {}
        tokens["input_tokens"] = usage.get("prompt_tokens", usage.get("input_tokens"))
        tokens["output_tokens"] = usage.get("completion_tokens", usage.get("output_tokens"))
        if tokens["output_tokens"] and (time.perf_counter() - started):
            tokens["tokens_per_second"] = tokens["output_tokens"] / (time.perf_counter() - started)
    except Exception as exc:
        content, parsed, valid_json, schema_valid = "", None, False, False
        error = str(exc)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    result = {
        "test_id": case.test_id,
        "model_id": config.model_id,
        "model_config": config.model_dump(mode="json"),
        "input": case.model_dump(mode="json"),
        "raw_response": raw,
        "parsed_response": parsed,
        "error": error,
        "evaluation": evaluate(case.model_dump(), parsed, valid_json, schema_valid, duration_ms, tokens),
    }
    return result


def run_cases(
    settings: Settings,
    cases: list[BenchmarkCase],
    configs: list[ModelConfig],
    repetitions: int,
    progress_callback=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results = []
    total = len(cases) * len(configs) * repetitions
    completed = 0
    for config_index, config in enumerate(configs):
        for repetition in range(repetitions):
            for case in cases:
                result = run_case(settings, case, config)
                result["configuration_index"] = config_index
                result["repetition"] = repetition + 1
                results.append(result)
                completed += 1
                if progress_callback:
                    progress_callback({
                        "completed": completed,
                        "total": total,
                        "percent": round(completed / total * 100, 1) if total else 100,
                        "current_test_id": case.test_id,
                        "current_model_id": config.model_id,
                        "repetition": repetition + 1,
                    }, result)
    return results, summarize(results)
