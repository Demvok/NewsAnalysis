import logging, os, sys
from langchain_openai.chat_models import ChatOpenAI
from langchain_core.runnables import ConfigurableField, RunnablePassthrough

import prompts


llm_main = ChatOpenAI(
    # base_url="http://host.docker.internal:1234/v1",
    base_url="http://127.0.0.1:1234/v1",
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

def llm_invoke(*args, usage_type="unspecified", reasoning="on", temperature=None, **kwargs):
    """
    Invoke the language model with logging of token usage and purpose.
    
    Args:
        *args: Arguments to pass to the model's invoke method
        usage_type: String describing what the LLM is being used for (e.g. "event_scoring", "sentiment_analysis")
        reasoning: String indicating whether to enable reasoning capabilities ("on" or "off")
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
        configurable["reasoning"] = reasoning
        config["configurable"] = configurable

    response = llm_main.invoke(*args, config=config, reasoning=reasoning, **kwargs)

    # Extract token usage from response metadata
    token_usage = response.response_metadata.get('token_usage', {})
    total_tokens = token_usage.get('total_tokens', 0)
    prompt_tokens = token_usage.get('prompt_tokens', 0)
    completion_tokens = token_usage.get('completion_tokens', 0)
    
    # Log the token usage with usage type
    logging.info(f"{usage_type} - total_tokens: {total_tokens}, prompt_tokens: {prompt_tokens}, completion_tokens: {completion_tokens}")
    
    return response


def extract_citations(article_title, article_content):
    """
    Extract citations from the given article using the citation extraction prompt.
    
    Args:
        article_title: The name of the article
        article_content: The content of the article
    """

    extraction_chain = prompts.CITATION_EXTRACTION_PROMPT | llm_main.with_structured_output(prompts.CitationExtractionOutput)

    response = extraction_chain.invoke({
    'article_title': article_title,
    'article_content': article_content
    })

    return response


def extract_citations_refined(article_title, article_content):
    """
    Extract citations from the given article using the citation extraction prompt.
    
    Args:
        article_title: The name of the article
        article_content: The content of the article
    """

    extraction_chain = prompts.CITATION_EXTRACTION_PROMPT | llm_main.with_structured_output(prompts.CitationExtractionOutput)


    refined_extraction_chain = (
        RunnablePassthrough.assign(extraction=extraction_chain)
        | prompts.CITATION_EXTRACTION_REFINING_PROMPT
        | llm_main.with_structured_output(prompts.CitationExtractionOutput)
    )

    response = refined_extraction_chain.invoke({
    'article_title': article_title,
    'article_content': article_content
    })

    return response

def sentiment_analysis(speaker: prompts.ExtractedSpeaker):
    """
    Perform sentiment analysis on the given content with respect to the specified topic.
    
    Args:
        topic: The topic to analyze sentiment for
        content: The text content to analyze
    """
    sentiment_chain = prompts.CITATION_EVALUATION_PROMPT | llm_main.with_structured_output(prompts.CitationEvaluationOutput)

    response = sentiment_chain.invoke({
        'speaker_name': speaker['speaker_name'],
        'speaker_role': speaker['speaker_role'],
        'aliases': speaker['aliases'],
        'citations': speaker['citations']
    })

    return response