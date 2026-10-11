import logging
import os
from typing import Any

from langchain_openai.chat_models import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import ConfigurableField, RunnablePassthrough

from . import prompts
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


# Configure logging
log_dir = "./logs"
os.makedirs(log_dir, exist_ok=True)  # Ensure the logs directory exists
log_file = os.path.join(log_dir, "model_usage.log")

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("model-usage")


class _LLMUsageLogger(BaseCallbackHandler):
    """Log token usage for both plain and structured-output model calls."""

    def __init__(self):
        self._usage_types: dict[Any, str] = {}

    def _remember_usage_type(self, run_id, metadata=None):
        metadata = metadata or {}
        self._usage_types[run_id] = metadata.get("usage_type", "unspecified")

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._remember_usage_type(run_id, metadata)

    def on_llm_start(self, serialized, prompts, *, run_id, metadata=None, **kwargs):
        self._remember_usage_type(run_id, metadata)

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
        logger.info(
            "%s - total_tokens: %s, input_tokens: %s, output_tokens: %s, "
            "reasoning_tokens: %s",
            self._usage_types.pop(run_id, "unspecified"),
            usage_metadata.get("total_tokens", 0),
            usage_metadata.get("input_tokens", 0),
            usage_metadata.get("output_tokens", 0),
            output_token_details.get("reasoning", 0),
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        logger.error(
            "%s - status: failed, error: %s",
            self._usage_types.pop(run_id, "unspecified"),
            type(error).__name__,
        )

# === Basics ===


def llm_invoke(*args, usage_type="unspecified", temperature=None, **kwargs):
    """
    Invoke the language model with logging of token usage and purpose.
    
    Args:
        *args: Arguments to pass to the model's invoke method
        usage_type: String describing what the LLM is being used for (e.g. "event_scoring", "sentiment_analysis")
        temperature: The temperature to use for the model   
        model: The model to use ('main' for llm_main, 'text' for llm_text)
        **kwargs: Keyword arguments to pass to the model's invoke method
    
    Returns:
        The model's response
    """
    config = kwargs.pop("config", None) or {}
    if temperature is not None:
        configurable = dict(config.get("configurable", {}))
        configurable["temperature"] = temperature
        config["configurable"] = configurable

    config["callbacks"] = [
        *config.get("callbacks", []),
        _USAGE_LOGGER,
    ]
    config["metadata"] = {
        **config.get("metadata", {}),
        "usage_type": usage_type,
    }
    return llm_main.invoke(*args, config=config, **kwargs)


_USAGE_LOGGER = _LLMUsageLogger()


def _structured_output(output_model, usage_type):
    """Create a structured-output model whose underlying call is usage-logged."""
    return llm_main.with_config(
        callbacks=[_USAGE_LOGGER],
        metadata={"usage_type": usage_type},
    ).with_structured_output(output_model)


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
    )


    refined_extraction_chain = (
        RunnablePassthrough.assign(extraction=extraction_chain)
        | prompts.CITATION_EXTRACTION_REFINING_PROMPT
        | _structured_output(
            prompts.CitationExtractionOutput,
            "citation_refinement",
        )
    )

    response = refined_extraction_chain.invoke({
    'article_title': article_title,
    'article_content': article_content
    })

    return response

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
        | _structured_output(prompts.TopicExtractionOutput, "topic_extraction")
        | prompts.CITATION_EVALUATION_PROMPT
        | _structured_output(prompts.CitationEvaluationOutput, "citation_evaluation")
    )

    response = sentiment_chain.invoke({
        'speaker_name': speaker['speaker_name'],
        'speaker_role': speaker['speaker_role'],
        'aliases': speaker['aliases'],
        'citations': speaker['citations']
    })

    return response


def compare_attitudes(
    topic_name: str,
    earlier: str,
    earlier_stance: str | None,
    newer: str,
    newer_stance: str | None,
) -> prompts.InconsistencyComparison:
    chain = prompts.INCONSISTENCY_COMPARISON_PROMPT | _structured_output(
        prompts.InconsistencyComparison,
        "inconsistency_comparison",
    )
    return chain.invoke({
        "topic_name": topic_name,
        "earlier": earlier,
        "earlier_stance": earlier_stance,
        "newer": newer,
        "newer_stance": newer_stance,
    })
