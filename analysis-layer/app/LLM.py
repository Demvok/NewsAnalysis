import os
import time
import math
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any

from langchain_openai.chat_models import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import ConfigurableField

from . import prompts
from .logger import get_logger
from .telemetry import current_context, emit_event, sanitized_error
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

llm_main = ChatOpenAI(
    # base_url="http://host.docker.internal:1234/v1",
    base_url=os.getenv("LLM_URL"),
    model=os.getenv("LLM_MODEL", "google/gemma-4-e4b"),
    api_key="fake_api_key",
    temperature=0.5
).configurable_fields(
    temperature=ConfigurableField(
        id="temperature",
        name="Temperature",
        description="Sampling temperature for the model.",
    )
)


logger = get_logger("llm", "analysis")
_METRICS = defaultdict(lambda: {
    "calls": 0,
    "input_chars": 0,
    "estimated_input_tokens": 0,
    "output_chars": 0,
    "estimated_output_tokens": 0,
    "provider_input_tokens": 0,
    "provider_output_tokens": 0,
    "provider_token_calls": 0,
    "duration": 0.0,
    "time_to_first_token": 0.0,
    "time_to_first_token_calls": 0,
})


def _estimate_tokens(char_count: int) -> int:
    return round(char_count / 4) if char_count else 0


def _value_chars(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_value_chars(key) + _value_chars(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_value_chars(item) for item in value)
    if hasattr(value, "model_dump"):
        return _value_chars(value.model_dump())
    return len(str(value or ""))


def _response_chars(response: Any) -> int:
    if hasattr(response, "model_dump"):
        return _value_chars(response.model_dump())
    content = getattr(response, "content", response)
    return _value_chars(content)


def _provider_usage(response: Any) -> tuple[Any, Any]:
    metadata = getattr(response, "response_metadata", {}) or {}
    usage = (
        getattr(response, "usage_metadata", None)
        or metadata.get("token_usage")
        or metadata.get("usage")
        or {}
    )
    return (
        usage.get("input_tokens", usage.get("prompt_tokens")),
        usage.get("output_tokens", usage.get("completion_tokens")),
    )


def _record_chain_metrics(
    *,
    stage: str,
    speaker: str | None,
    citation_count: int | None,
    payload: Any,
    response: Any,
    started: float,
) -> None:
    input_chars = _value_chars(payload)
    output_chars = _response_chars(response)
    duration = time.perf_counter() - started
    provider_input_tokens, provider_output_tokens = _provider_usage(response)
    item = _METRICS[stage]
    item["calls"] += 1
    item["input_chars"] += input_chars
    item["estimated_input_tokens"] += _estimate_tokens(input_chars)
    item["output_chars"] += output_chars
    item["estimated_output_tokens"] += _estimate_tokens(output_chars)
    item["duration"] += duration
    if provider_input_tokens is not None:
        item["provider_input_tokens"] += provider_input_tokens
        item["provider_token_calls"] += 1
    if provider_output_tokens is not None:
        item["provider_output_tokens"] += provider_output_tokens
    logger.info(
        "analysis_kpi stage=%s speaker=%s citation_count=%s input_chars=%d "
        "estimated_input_tokens=%d output_chars=%d estimated_output_tokens=%d "
        "provider_input_tokens=%s provider_output_tokens=%s time_to_first_token=%s "
        "duration=%.3fs",
        stage,
        speaker or "-",
        citation_count if citation_count is not None else "-",
        input_chars,
        _estimate_tokens(input_chars),
        output_chars,
        _estimate_tokens(output_chars),
        provider_input_tokens if provider_input_tokens is not None else "-",
        provider_output_tokens if provider_output_tokens is not None else "-",
        "-",
        duration,
    )
    # LangChain callbacks are the authoritative call-level instrumentation.
    # This helper remains a process-local fallback for aggregate legacy logs.


def _invoke_chain(chain: Any, payload: Any, *, stage: str, speaker: str | None = None, citation_count: int | None = None) -> Any:
    started = time.perf_counter()
    span_id = f"chain-{time.time_ns()}"
    input_chars = _value_chars(payload)
    emit_event(
        "llm_call",
        operation="structured_chain",
        span_id=span_id,
        parent_span_id=current_context().get("trace_id"),
        stage=stage,
        event_status="started",
        started_at=datetime.now(timezone.utc).isoformat(),
        input_tokens_estimated=_estimate_tokens(input_chars),
        model=os.getenv("LLM_MODEL"),
        provider=os.getenv("LLM_URL", "").split("/")[2] if "://" in os.getenv("LLM_URL", "") else "openai-compatible",
        attributes={"speaker": speaker, "citation_count": citation_count, "instrumentation_source": "chain_invoke"},
    )
    try:
        response = chain.invoke(payload)
    except Exception as error:
        emit_event(
            "llm_call",
            operation="structured_chain",
            span_id=span_id,
            parent_span_id=current_context().get("trace_id"),
            stage=stage,
            event_status="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            input_tokens_estimated=_estimate_tokens(input_chars),
            model=os.getenv("LLM_MODEL"),
            provider=os.getenv("LLM_URL", "").split("/")[2] if "://" in os.getenv("LLM_URL", "") else "openai-compatible",
            error_category=type(error).__name__,
            error_detail=sanitized_error(error),
            attributes={"speaker": speaker, "citation_count": citation_count, "instrumentation_source": "chain_invoke"},
        )
        raise
    provider_input, provider_output = _provider_usage(response)
    duration_ms = int((time.perf_counter() - started) * 1000)
    output_chars = _response_chars(response)
    emit_event(
        "llm_call",
        operation="structured_chain",
        span_id=span_id,
        parent_span_id=current_context().get("trace_id"),
        stage=stage,
        event_status="completed",
        duration_ms=duration_ms,
        input_tokens=provider_input,
        output_tokens=provider_output,
        input_tokens_estimated=_estimate_tokens(input_chars) if provider_input is None else None,
        output_tokens_estimated=_estimate_tokens(output_chars) if provider_output is None else None,
        model=os.getenv("LLM_MODEL"),
        provider=os.getenv("LLM_URL", "").split("/")[2] if "://" in os.getenv("LLM_URL", "") else "openai-compatible",
        attributes={"speaker": speaker, "citation_count": citation_count, "instrumentation_source": "chain_invoke"},
    )
    _record_chain_metrics(
        stage=stage,
        speaker=speaker,
        citation_count=citation_count,
        payload=payload,
        response=response,
        started=started,
    )
    return response


class _LLMUsageLogger(BaseCallbackHandler):
    """Log token usage for both plain and structured-output model calls."""

    def __init__(self):
        self._calls: dict[Any, dict[str, Any]] = {}

    def _remember_usage_type(self, run_id, metadata=None):
        metadata = metadata or {}
        self._calls[run_id] = {
            "stage": metadata.get("stage", metadata.get("usage_type", "unspecified")),
            "speaker": metadata.get("speaker"),
            "citation_count": metadata.get("citation_count"),
            "started_at": time.perf_counter(),
            "input_chars": 0,
            "time_to_first_token": None,
            "model": None,
            "started_at_iso": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _estimate_tokens(char_count: int) -> int:
        return round(char_count / 4) if char_count else 0

    @staticmethod
    def _input_chars(values: Any) -> int:
        if isinstance(values, (list, tuple)):
            return sum(_LLMUsageLogger._input_chars(value) for value in values)
        if isinstance(values, dict):
            return len(str(values.get("content", values)))
        return len(str(getattr(values, "content", values)))

    @staticmethod
    def _output_chars(response: Any) -> int:
        generations = getattr(response, "generations", []) or []
        texts: list[str] = []
        for generation_batch in generations[:1]:
            for generation in generation_batch[:1]:
                message = getattr(generation, "message", None)
                content = getattr(message, "content", None) if message is not None else None
                if content is None:
                    content = getattr(generation, "text", "")
                texts.append(str(content or ""))
        if texts:
            return len(texts[0])
        return len(str(getattr(response, "content", "") or ""))

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        generations = getattr(response, "generations", []) or []
        if generations and generations[0]:
            message = getattr(generations[0][0], "message", None)
            usage = getattr(message, "usage_metadata", {}) or {}
            if usage:
                return usage
        llm_output = getattr(response, "llm_output", {}) or {}
        return llm_output.get("token_usage", llm_output)

    def _record_metrics(
        self,
        *,
        stage: str,
        input_chars: int,
        output_chars: int,
        duration: float,
        provider_input_tokens: int | None,
        provider_output_tokens: int | None,
        time_to_first_token: float | None,
    ) -> None:
        item = _METRICS[stage]
        item["calls"] += 1
        item["input_chars"] += input_chars
        item["estimated_input_tokens"] += self._estimate_tokens(input_chars)
        item["output_chars"] += output_chars
        item["estimated_output_tokens"] += self._estimate_tokens(output_chars)
        item["duration"] += duration
        if provider_input_tokens is not None:
            item["provider_input_tokens"] += provider_input_tokens
            item["provider_token_calls"] += 1
        if provider_output_tokens is not None:
            item["provider_output_tokens"] += provider_output_tokens
        if time_to_first_token is not None:
            item["time_to_first_token"] += time_to_first_token
            item["time_to_first_token_calls"] += 1

    def _log_started(self, run_id, input_chars: int, model: str) -> None:
        call = self._calls[run_id]
        call["model"] = os.getenv("LLM_MODEL", model)
        call["input_chars"] = input_chars
        logger.info(
            "llm_call status=started stage=%s speaker=%s citation_count=%s run_id=%s "
            "input_chars=%d estimated_input_tokens=%d model=%s",
            call["stage"],
            call["speaker"] or "-",
            call["citation_count"] if call["citation_count"] is not None else "-",
            run_id,
            input_chars,
            self._estimate_tokens(input_chars),
            model,
        )
        emit_event(
            "llm_call",
            operation="chat_completion",
            span_id=str(run_id),
            parent_span_id=current_context().get("trace_id"),
            stage=call["stage"],
            event_status="started",
            started_at=call["started_at_iso"],
            input_tokens_estimated=self._estimate_tokens(input_chars),
            model=call["model"],
            provider=os.getenv("LLM_URL", "").split("/")[2] if "://" in os.getenv("LLM_URL", "") else "openai-compatible",
            attributes={
                "speaker": call["speaker"],
                "citation_count": call["citation_count"],
                "token_source": "estimated",
                "prompt_version": os.getenv("PROMPT_VERSION", "unversioned"),
            },
        )

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._remember_usage_type(run_id, metadata)
        self._log_started(
            run_id,
            self._input_chars(messages),
            serialized.get("name") if isinstance(serialized, dict) else "unknown",
        )

    def on_llm_start(self, serialized, prompts, *, run_id, metadata=None, **kwargs):
        self._remember_usage_type(run_id, metadata)
        self._log_started(
            run_id,
            self._input_chars(prompts),
            serialized.get("name") if isinstance(serialized, dict) else "unknown",
        )

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        call = self._calls.get(run_id)
        if call is not None and call["time_to_first_token"] is None:
            call["time_to_first_token"] = time.perf_counter() - call["started_at"]

    def on_llm_end(self, response, *, run_id, **kwargs):
        usage_metadata = self._usage(response)

        output_token_details = usage_metadata.get("output_token_details", {}) or {}
        call = self._calls.pop(run_id, {
            "stage": "unspecified",
            "speaker": None,
            "citation_count": None,
            "started_at": time.perf_counter(),
            "input_chars": 0,
            "time_to_first_token": None,
        })
        output_chars = self._output_chars(response)
        duration = time.perf_counter() - call["started_at"]
        provider_input_tokens = usage_metadata.get("input_tokens", usage_metadata.get("prompt_tokens"))
        provider_output_tokens = usage_metadata.get("output_tokens", usage_metadata.get("completion_tokens"))
        time_to_first_token = call["time_to_first_token"]
        response_metadata = getattr(response, "llm_output", {}) or {}
        finish_reason = response_metadata.get("finish_reason")
        if finish_reason is None:
            generations = getattr(response, "generations", []) or []
            if generations and generations[0]:
                generation = generations[0][0]
                finish_reason = getattr(generation, "generation_info", {}).get("finish_reason")
        self._record_metrics(
            stage=call["stage"],
            input_chars=call["input_chars"],
            output_chars=output_chars,
            duration=duration,
            provider_input_tokens=provider_input_tokens,
            provider_output_tokens=provider_output_tokens,
            time_to_first_token=time_to_first_token,
        )
        logger.info(
            "llm_call status=completed stage=%s speaker=%s citation_count=%s run_id=%s "
            "input_chars=%d estimated_input_tokens=%d output_chars=%d "
            "estimated_output_tokens=%d provider_input_tokens=%s provider_output_tokens=%s "
            "time_to_first_token=%s duration=%.3fs",
            call["stage"],
            call["speaker"] or "-",
            call["citation_count"] if call["citation_count"] is not None else "-",
            run_id,
            call["input_chars"],
            self._estimate_tokens(call["input_chars"]),
            output_chars,
            self._estimate_tokens(output_chars),
            provider_input_tokens if provider_input_tokens is not None else "-",
            provider_output_tokens if provider_output_tokens is not None else "-",
            f"{time_to_first_token:.3f}s" if time_to_first_token is not None else "-",
            duration,
        )
        generation_seconds = duration - time_to_first_token if time_to_first_token is not None else None
        throughput = None
        if (
            provider_output_tokens is not None
            and generation_seconds is not None
            and generation_seconds > 0
        ):
            throughput = provider_output_tokens / generation_seconds
        emit_event(
            "llm_call",
            operation="chat_completion",
            span_id=str(run_id),
            parent_span_id=current_context().get("trace_id"),
            stage=call["stage"],
            event_status="completed",
            started_at=call["started_at_iso"],
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=int(duration * 1000),
            time_to_first_token_ms=int(time_to_first_token * 1000) if time_to_first_token is not None else None,
            input_tokens=provider_input_tokens,
            output_tokens=provider_output_tokens,
            input_tokens_estimated=self._estimate_tokens(call["input_chars"]) if provider_input_tokens is None else None,
            output_tokens_estimated=self._estimate_tokens(output_chars) if provider_output_tokens is None else None,
            throughput_tokens_per_second=throughput,
            model=call.get("model"),
            provider=os.getenv("LLM_URL", "").split("/")[2] if "://" in os.getenv("LLM_URL", "") else "openai-compatible",
            finish_reason=finish_reason,
            attributes={
                "speaker": call["speaker"],
                "citation_count": call["citation_count"],
                "token_source": "provider" if provider_input_tokens is not None or provider_output_tokens is not None else "unavailable",
                "prompt_version": os.getenv("PROMPT_VERSION", "unversioned"),
            },
        )

        logger.info(
            "analysis_kpi stage=%s calls=1 input_chars=%d estimated_input_tokens=%d "
            "output_chars=%d estimated_output_tokens=%d provider_input_tokens=%s "
            "provider_output_tokens=%s time_to_first_token=%s duration=%.3fs",
            call["stage"],
            call["input_chars"],
            self._estimate_tokens(call["input_chars"]),
            output_chars,
            self._estimate_tokens(output_chars),
            provider_input_tokens if provider_input_tokens is not None else "-",
            provider_output_tokens if provider_output_tokens is not None else "-",
            f"{time_to_first_token:.3f}s" if time_to_first_token is not None else "-",
            duration,
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        call = self._calls.pop(run_id, {
            "stage": "unspecified",
            "speaker": None,
            "citation_count": None,
            "started_at": time.perf_counter(),
            "time_to_first_token": None,
        })
        logger.error(
            "llm_call status=failed stage=%s speaker=%s citation_count=%s run_id=%s "
            "error_type=%s time_to_first_token=%s duration=%.3fs",
            call["stage"],
            call["speaker"] or "-",
            call["citation_count"] if call["citation_count"] is not None else "-",
            run_id,
            type(error).__name__,
            f"{call['time_to_first_token']:.3f}s" if call["time_to_first_token"] is not None else "-",
            time.perf_counter() - call["started_at"],
        )
        duration = time.perf_counter() - call["started_at"]
        emit_event(
            "llm_call",
            operation="chat_completion",
            span_id=str(run_id),
            parent_span_id=current_context().get("trace_id"),
            stage=call["stage"],
            event_status="failed",
            started_at=call.get("started_at_iso"),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=int(duration * 1000),
            time_to_first_token_ms=int(call["time_to_first_token"] * 1000) if call.get("time_to_first_token") is not None else None,
            model=call.get("model"),
            provider=os.getenv("LLM_URL", "").split("/")[2] if "://" in os.getenv("LLM_URL", "") else "openai-compatible",
            error_category=type(error).__name__,
            error_detail=sanitized_error(error),
            attributes={"speaker": call["speaker"], "citation_count": call["citation_count"]},
        )

# === Basics ===


def llm_invoke(
    *args,
    usage_type="unspecified",
    temperature=None,
    thinking="off",
    stage=None,
    speaker=None,
    citation_count=None,
    **kwargs,
):
    """
    Invoke the language model with logging of token usage and purpose.
    
    Args:
        *args: Arguments to pass to the model's invoke method
        usage_type: String describing what the LLM is being used for (e.g. "event_scoring", "sentiment_analysis")
        temperature: The temperature to use for the model   
        model: The model to use ('main' for llm_main, 'text' for llm_text)
        thinking: Whether the provider should use its reasoning mode ("on" or "off").
        **kwargs: Keyword arguments to pass to the model's invoke method
    
    Returns:
        The model's response
    """
    started = time.perf_counter()
    logger.info(
        "llm_request status=started stage=%s speaker=%s citation_count=%s usage_type=%s "
        "args=%d kwargs=%s temperature=%s thinking=%s",
        stage or usage_type,
        speaker or "-",
        citation_count if citation_count is not None else "-",
        usage_type,
        len(args),
        sorted(kwargs.keys()),
        temperature,
        thinking,
    )
    config = kwargs.pop("config", None) or {}
    if temperature is not None:
        configurable = dict(config.get("configurable", {}))
        configurable["temperature"] = temperature
        config["configurable"] = configurable
    extra_body = dict(kwargs.get("extra_body") or {})
    extra_body["thinking"] = thinking
    kwargs["extra_body"] = extra_body

    config["callbacks"] = [
        *config.get("callbacks", []),
        _USAGE_LOGGER,
    ]
    config["metadata"] = {
        **config.get("metadata", {}),
        "usage_type": usage_type,
        "stage": stage or usage_type,
        "speaker": speaker,
        "citation_count": citation_count,
    }
    try:
        response = llm_main.invoke(*args, config=config, **kwargs)
        logger.info(
            "llm_request status=completed stage=%s speaker=%s citation_count=%s "
            "usage_type=%s response_type=%s duration=%.3fs",
            stage or usage_type,
            speaker or "-",
            citation_count if citation_count is not None else "-",
            usage_type,
            type(response).__name__,
            time.perf_counter() - started,
        )
        return response
    except Exception:
        logger.exception(
            "llm_request status=failed stage=%s speaker=%s citation_count=%s "
            "usage_type=%s duration=%.3fs",
            stage or usage_type,
            speaker or "-",
            citation_count if citation_count is not None else "-",
            usage_type,
            time.perf_counter() - started,
        )
        raise


_USAGE_LOGGER = _LLMUsageLogger()


def log_usage_summary(context: str) -> None:
    """Write compact accumulated LLM KPIs to the analysis log."""
    for stage, item in sorted(_METRICS.items()):
        average_ttft = (
            item["time_to_first_token"] / item["time_to_first_token_calls"]
            if item["time_to_first_token_calls"]
            else None
        )
        logger.info(
            "analysis_kpi_summary context=%s stage=%s calls=%d input_chars=%d "
            "estimated_input_tokens=%d output_chars=%d estimated_output_tokens=%d "
            "provider_input_tokens=%s provider_output_tokens=%s avg_time_to_first_token=%s "
            "duration=%.3fs",
            context,
            stage,
            item["calls"],
            item["input_chars"],
            item["estimated_input_tokens"],
            item["output_chars"],
            item["estimated_output_tokens"],
            item["provider_input_tokens"] if item["provider_token_calls"] else "-",
            item["provider_output_tokens"] if item["provider_token_calls"] else "-",
            f"{average_ttft:.3f}s" if average_ttft is not None else "-",
            item["duration"],
        )


def _structured_output(
    output_model,
    usage_type,
    thinking="off",
    *,
    stage=None,
    speaker=None,
    citation_count=None,
):
    """Create a structured-output model whose underlying call is usage-logged."""
    structured_model = llm_main.with_config(
        metadata={
            "usage_type": usage_type,
            "stage": stage or usage_type,
            "speaker": speaker,
            "citation_count": citation_count,
        },
    ).with_structured_output(output_model)
    return structured_model.bind(extra_body={"thinking": thinking})


def _compact_topic_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only evidence fields needed for topic extraction."""
    return [
        {
            "exact_quote": citation.get("exact_quote", ""),
            "context": citation.get("context", ""),
            "citation_type": citation.get("citation_type"),
            "summarized_quote": citation.get("summarized_quote", ""),
        }
        for citation in citations
    ]


def _compact_evaluated_citations(
    citations: list[prompts.AnalysedCitation],
) -> list[dict[str, Any]]:
    """Keep evidence and proposed topics needed for stance assignment."""
    return [
        {
            "exact_quote": citation.exact_quote,
            "context": citation.context,
            "citation_type": citation.citation_type.value,
            "summarized_quote": citation.summarized_quote,
            "mentioned_topics": [
                {
                    "topic_name": topic.topic_name,
                    "topic_description": topic.topic_description,
                    "relevancy_score": topic.relevancy_score,
                    "stance_detected": topic.stance_detected,
                }
                for topic in citation.mentioned_topics
            ],
        }
        for citation in citations
    ]


# === Extraction ===

def extract_citations(article_title, article_content):
    """
    Extract citations from the given article using the citation extraction prompt.
    
    Args:
        article_title: The name of the article
        article_content: The content of the article
    """

    extraction_chain = prompts.CITATION_EXTRACTION_PROMPT | _structured_output(
        prompts.CitationExtractionOutput,
        "citation_extraction",
        thinking="off",
        stage="citation_extraction",
    )


    logger.info(
        "analysis_step status=started step=citation_extraction title_chars=%d content_chars=%d",
        len(article_title or ""),
        len(article_content or ""),
    )
    started = time.perf_counter()
    try:
        extraction_payload = {
            "article_title": article_title,
            "article_content": article_content,
        }
        response = _invoke_chain(
            extraction_chain,
            extraction_payload,
            stage="citation_extraction",
            citation_count=0,
        )
        response_payload = response.model_dump()
        if _needs_citation_refinement(response_payload):
            logger.info("analysis_step status=started step=citation_refinement reason=deterministic_validation")
            refined_extraction_chain = (
                prompts.CITATION_EXTRACTION_REFINING_PROMPT
                | _structured_output(
                    prompts.CitationExtractionOutput,
                    "citation_refinement",
                    thinking="off",
                    stage="citation_refinement",
                )
            )
            response = _invoke_chain(
                refined_extraction_chain,
                {**extraction_payload, "extraction": response_payload},
                stage="citation_refinement",
                citation_count=sum(
                    len(speaker.get("citations") or [])
                    for speaker in response_payload.get("speakers") or []
                ),
            )
        else:
            logger.info("analysis_step status=skipped step=citation_refinement reason=validation_passed")
        logger.info(
            "analysis_step status=completed step=citation_extraction response_type=%s duration=%.3fs",
            type(response).__name__,
            time.perf_counter() - started,
        )
        return response
    except Exception:
        logger.exception("analysis_step status=failed step=citation_extraction duration=%.3fs", time.perf_counter() - started)
        raise


def _needs_citation_refinement(payload: dict[str, Any]) -> bool:
    """Return whether an extraction has a deterministic edge case worth auditing."""
    if str(payload.get("status", "")).upper() not in {"PROCESSED", "COMPLETE", "COMPLETED"}:
        return True
    speakers = payload.get("speakers") or []
    if not speakers:
        return False
    seen: set[tuple[str, str]] = set()
    quote_speakers: dict[str, str] = {}
    valid_kinds = {
        "person",
        "organization",
        "group",
        "anonymous_source",
        "unresolved",
        "canonical_person",
        "organization_or_government",
        "collective_or_group",
        "vague_source",
        "unresolved_reference",
    }
    for speaker in speakers:
        name = str(speaker.get("speaker_name") or "").strip()
        if not name:
            return True
        kind = str(speaker.get("speaker_kind") or "").casefold()
        if kind not in valid_kinds:
            return True
        if kind in {"unresolved", "unresolved_reference", "anonymous_source", "vague_source"}:
            return True
        if speaker.get("uncertain") is True or speaker.get("uncertainty_reason"):
            return True
        try:
            influence_score = float(speaker.get("influence_score", 1))
        except (TypeError, ValueError):
            return True
        if not math.isfinite(influence_score) or not 0 <= influence_score <= 1:
            return True
        for citation in speaker.get("citations") or []:
            quote = str(citation.get("exact_quote") or "").strip()
            if not quote:
                return True
            for field in ("confidence_level", "extraction_confidence"):
                try:
                    confidence = float(citation.get(field))
                except (TypeError, ValueError):
                    return True
                if not 0 <= confidence <= 5:
                    return True
            citation_type = str(citation.get("citation_type") or "").casefold()
            if citation_type not in {"direct", "partial", "indirect", "summary", "position", "mention"}:
                return True
            normalized_quote = " ".join(quote.casefold().split())
            previous_speaker = quote_speakers.get(normalized_quote)
            if previous_speaker is not None and previous_speaker != name.casefold():
                return True
            quote_speakers[normalized_quote] = name.casefold()
            key = (name.casefold(), normalized_quote)
            if key in seen:
                return True
            seen.add(key)
            if quote.startswith(("...", "…")) or quote.endswith(("...", "…")):
                return True
            if quote.count('"') % 2 or quote.count("“") != quote.count("”"):
                return True
    return False

# === Sentiment Analysis ===

def sentiment_analysis(speaker: prompts.ExtractedSpeaker):
    """Identify topics and stances in one batch call per speaker group."""
    speaker_kind = speaker.get("speaker_kind", "UNRESOLVED")
    thinking = "off"
    chain = prompts.COMBINED_CITATION_ANALYSIS_PROMPT | _structured_output(
        prompts.CombinedCitationAnalysisOutput,
        "citation_topic_attitude",
        thinking=thinking,
        stage="citation_topic_attitude",
        speaker=speaker["speaker_name"],
        citation_count=len(speaker.get("citations", [])),
    )
    logger.info(
        "analysis_step status=started step=topic_and_stance speaker=%s citations=%d",
        speaker["speaker_name"],
        len(speaker.get("citations", [])),
    )
    started = time.perf_counter()
    try:
        analysis_payload = {
            "speaker_name": speaker["speaker_name"],
            "speaker_role": speaker["speaker_role"],
            "speaker_kind": speaker_kind,
            "citations": _compact_topic_citations(speaker.get("citations", [])),
        }
        response = _invoke_chain(
            chain,
            analysis_payload,
            stage="citation_topic_attitude",
            speaker=speaker["speaker_name"],
            citation_count=len(speaker.get("citations", [])),
        )
        logger.info("analysis_step status=completed step=topic_and_stance speaker=%s duration=%.3fs", speaker["speaker_name"], time.perf_counter() - started)
        return response
    except Exception:
        logger.exception("analysis_step status=failed step=topic_and_stance speaker=%s duration=%.3fs", speaker["speaker_name"], time.perf_counter() - started)
        raise


def compare_attitudes(
    topic_name: str,
    earlier: str,
    earlier_stance: str | None,
    newer: str,
    newer_stance: str | None,
    speaker: str | None = None,
) -> prompts.InconsistencyComparison:
    started = time.perf_counter()
    logger.info("analysis_step status=started step=inconsistency_comparison topic=%s", topic_name)
    chain = prompts.INCONSISTENCY_COMPARISON_PROMPT | _structured_output(
        prompts.InconsistencyComparison,
        "inconsistency_comparison",
        thinking="on",
        stage="inconsistency_comparison",
        speaker=speaker,
        citation_count=2,
    )
    try:
        comparison_payload = {
            "topic_name": topic_name,
            "earlier": earlier,
            "earlier_stance": earlier_stance,
            "newer": newer,
            "newer_stance": newer_stance,
        }
        response = _invoke_chain(
            chain,
            comparison_payload,
            stage="inconsistency_comparison",
            speaker=speaker,
            citation_count=2,
        )
        logger.info("analysis_step status=completed step=inconsistency_comparison classification=%s duration=%.3fs", response.classification, time.perf_counter() - started)
        return response
    except Exception:
        logger.exception("analysis_step status=failed step=inconsistency_comparison duration=%.3fs", time.perf_counter() - started)
        raise
