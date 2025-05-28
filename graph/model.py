import logging, os, sys
from langchain_openai.chat_models import ChatOpenAI
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import USE_TWO_LLMS

llm_main = ChatOpenAI(openai_api_base="http://127.0.0.1:1234/v1",
                 model='gemma-3-4b-it-qat',
                 temperature=0.1)

if USE_TWO_LLMS:
    llm_text = ChatOpenAI(openai_api_base="http://127.0.0.1:1234/v1",
                    model='qwen3-4b',
                    temperature=0.1)


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

def llm_invoke(*args, usage_type="unspecified", model='main', **kwargs):
    """
    Invoke the language model with logging of token usage and purpose.
    
    Args:
        *args: Arguments to pass to the model's invoke method
        usage_type: String describing what the LLM is being used for (e.g. "event_scoring", "sentiment_analysis")
        model: The model to use ('main' for llm_main, 'text' for llm_text)
        **kwargs: Keyword arguments to pass to the model's invoke method
    
    Returns:
        The model's response
    """
    if model == 'main':
        response = llm_main.invoke(*args, **kwargs)
    elif model == 'text':
        # Check if llm_text is declared, else use llm_main
        if 'llm_text' in globals() and llm_text is not None:
            response = llm_text.invoke(*args, **kwargs)
        else:
            response = llm_main.invoke(*args, **kwargs)

    # Extract token usage from response metadata
    token_usage = response.response_metadata.get('token_usage', {})
    total_tokens = token_usage.get('total_tokens', 0)
    prompt_tokens = token_usage.get('prompt_tokens', 0)
    completion_tokens = token_usage.get('completion_tokens', 0)
    
    # Log the token usage with usage type
    logging.info(f"{usage_type} - total_tokens: {total_tokens}, prompt_tokens: {prompt_tokens}, completion_tokens: {completion_tokens}")
    
    return response