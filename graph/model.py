import logging, os, sys
# from langchain_huggingface import HuggingFaceEndpoint
from langchain_openai.chat_models import ChatOpenAI
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# # Setup Hugging Face model
# llm = HuggingFaceEndpoint(
#     repo_id='tiiuae/falcon-7b-instruct',
#     huggingfacehub_api_token=os.getenv('HUGGINGFACE_API_KEY'),
#     task='text-generation'
# )


llm = ChatOpenAI(openai_api_base="http://127.0.0.1:1234/v1",
                 model='gemma-3-4b-it-qat',
                 temperature=0)


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

def llm_invoke(*args, **kwargs):
    response = llm.invoke(*args, **kwargs)
    
    # Extract token usage from response metadata
    token_usage = response.response_metadata.get('token_usage', {})
    total_tokens = token_usage.get('total_tokens', 0)
    prompt_tokens = token_usage.get('prompt_tokens', 0)
    completion_tokens = token_usage.get('completion_tokens', 0)
    
    # Log the token usage
    logging.info(f"total_tokens: {total_tokens}, prompt_tokens: {prompt_tokens}, completion_tokens: {completion_tokens}")
    
    return response