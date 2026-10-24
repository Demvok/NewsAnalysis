import os
import time
from typing import Any

from langchain_openai.chat_models import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import ConfigurableField, RunnablePassthrough

from . import prompts
from .logger import get_logger
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

llm_main = ChatOpenAI(
    # base_url="http://host.docker.internal:1234/v1",
    base_url=os.getenv("LLM_URL"),
    model='google/gemma-4-e4b',
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


class _LLMUsageLogger(BaseCallbackHandler):
    """Log token usage for both plain and structured-output model calls."""

    def __init__(self):
        self._usage_types: dict[Any, str] = {}
        self._started_at: dict[Any, float] = {}

    def _remember_usage_type(self, run_id, metadata=None):
        metadata = metadata or {}
        self._usage_types[run_id] = metadata.get("usage_type", "unspecified")
        self._started_at[run_id] = time.perf_counter()

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._remember_usage_type(run_id, metadata)
        logger.info(
            "llm_call status=started usage_type=%s run_id=%s message_batches=%d "
            "input_chars=%d model=%s metadata_keys=%s",
            self._usage_types[run_id],
            run_id,
            len(messages),
            sum(len(str(message)) for message in messages),
            serialized.get("name") if isinstance(serialized, dict) else "unknown",
            sorted((metadata or {}).keys()),
        )

    def on_llm_start(self, serialized, prompts, *, run_id, metadata=None, **kwargs):
        self._remember_usage_type(run_id, metadata)
        logger.info(
            "llm_call status=started usage_type=%s run_id=%s prompts=%d "
            "input_chars=%d model=%s",
            self._usage_types[run_id],
            run_id,
            len(prompts),
            sum(len(str(prompt)) for prompt in prompts),
            serialized.get("name") if isinstance(serialized, dict) else "unknown",
        )

    def on_llm_end(self, response, *, run_id, **kwargs):
        usage_metadata = {}
        generations = getattr(response, "generations", [])
        if generations and generations[0]:
            message = getattr(generations[0][0], "message", None)
            usage_metadata = getattr(message, "usage_metadata", {}) or {}
        if not usage_metadata:
            usage_metadata = getattr(response, "llm_output", {}) or {}
            usage_metadata = usage_metadata.get("token_usage", usage_metadata)

        output_token_details = usage_metadata.get("output_token_details", {}) or {}
        usage_type = self._usage_types.pop(run_id, "unspecified")
        duration = time.perf_counter() - self._started_at.pop(run_id, time.perf_counter())
        logger.info(
            "llm_call status=completed usage_type=%s run_id=%s total_tokens=%s "
            "input_tokens=%s output_tokens=%s reasoning_tokens=%s duration=%.3fs",
            usage_type,
            run_id,
            usage_metadata.get("total_tokens", 0),
            usage_metadata.get("input_tokens", 0),
            usage_metadata.get("output_tokens", 0),
            output_token_details.get("reasoning", 0),
            duration,
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        logger.error(
            "llm_call status=failed usage_type=%s run_id=%s error_type=%s duration=%.3fs",
            self._usage_types.pop(run_id, "unspecified"),
            run_id,
            type(error).__name__,
            time.perf_counter() - self._started_at.pop(run_id, time.perf_counter()),
        )

# === Basics ===


def llm_invoke(
    *args,
    usage_type="unspecified",
    temperature=None,
    thinking="off",
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
        "llm_request status=started usage_type=%s args=%d kwargs=%s temperature=%s thinking=%s",
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
    }
    try:
        response = llm_main.invoke(*args, config=config, **kwargs)
        logger.info(
            "llm_request status=completed usage_type=%s response_type=%s duration=%.3fs",
            usage_type,
            type(response).__name__,
            time.perf_counter() - started,
        )
        return response
    except Exception:
        logger.exception(
            "llm_request status=failed usage_type=%s duration=%.3fs",
            usage_type,
            time.perf_counter() - started,
        )
        raise


_USAGE_LOGGER = _LLMUsageLogger()


def _structured_output(output_model, usage_type, thinking="off"):
    """Create a structured-output model whose underlying call is usage-logged."""
    structured_model = llm_main.with_config(
        callbacks=[_USAGE_LOGGER],
        metadata={"usage_type": usage_type},
    ).with_structured_output(output_model)
    return structured_model.bind(extra_body={"thinking": thinking})


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
    )


    refined_extraction_chain = (
        RunnablePassthrough.assign(extraction=extraction_chain)
        | prompts.CITATION_EXTRACTION_REFINING_PROMPT
        | _structured_output(
            prompts.CitationExtractionOutput,
            "citation_refinement",
            thinking="on",
        )
    )

    logger.info(
        "analysis_step status=started step=citation_extraction title_chars=%d content_chars=%d",
        len(article_title or ""),
        len(article_content or ""),
    )
    started = time.perf_counter()
    try:
        response = refined_extraction_chain.invoke({
            "article_title": article_title,
            "article_content": article_content,
        })
        logger.info(
            "analysis_step status=completed step=citation_extraction response_type=%s duration=%.3fs",
            type(response).__name__,
            time.perf_counter() - started,
        )
        return response
    except Exception:
        logger.exception("analysis_step status=failed step=citation_extraction duration=%.3fs", time.perf_counter() - started)
        raise

# === Sentiment Analysis ===

def sentiment_analysis(speaker: prompts.ExtractedSpeaker):
    """
    Perform sentiment analysis on the given content with respect to the specified topic.
    
    Args:
        topic: The topic to analyze sentiment for
        content: The text content to analyze
    """
    sentiment_chain = (
        prompts.TOPIC_EXTRACTION_PROMPT
        | _structured_output(
            prompts.TopicExtractionOutput,
            "topic_extraction",
            thinking="off",
        )
        | prompts.CITATION_EVALUATION_PROMPT
        | _structured_output(
            prompts.CitationEvaluationOutput,
            "citation_evaluation",
            thinking="on",
        )
    )

    logger.info(
        "analysis_step status=started step=topic_and_stance speaker=%s citations=%d",
        speaker["speaker_name"],
        len(speaker.get("citations", [])),
    )
    started = time.perf_counter()
    try:
        response = sentiment_chain.invoke({
        'speaker_name': speaker['speaker_name'],
        'speaker_role': speaker['speaker_role'],
        'aliases': speaker['aliases'],
        'citations': speaker['citations']
        })
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
) -> prompts.InconsistencyComparison:
    started = time.perf_counter()
    logger.info("analysis_step status=started step=inconsistency_comparison topic=%s", topic_name)
    chain = prompts.INCONSISTENCY_COMPARISON_PROMPT | _structured_output(
        prompts.InconsistencyComparison,
        "inconsistency_comparison",
        thinking="on",
    )
    try:
        response = chain.invoke({
            "topic_name": topic_name,
            "earlier": earlier,
            "earlier_stance": earlier_stance,
            "newer": newer,
            "newer_stance": newer_stance,
        })
        logger.info("analysis_step status=completed step=inconsistency_comparison classification=%s duration=%.3fs", response.classification, time.perf_counter() - started)
        return response
    except Exception:
        logger.exception("analysis_step status=failed step=inconsistency_comparison duration=%.3fs", time.perf_counter() - started)
        raise
