# from langchain_huggingface import HuggingFaceEndpoint
from langchain_community.chat_models import ChatOpenAI

# # Setup Hugging Face model
# llm = HuggingFaceEndpoint(
#     repo_id='tiiuae/falcon-7b-instruct',
#     huggingfacehub_api_token=os.getenv('HUGGINGFACE_API_KEY'),
#     task='text-generation'
# )

llm = ChatOpenAI(openai_api_base="http://127.0.0.1:1234/v1")

def llm_invoke(*args, **kwargs):
    response = llm.invoke(*args, **kwargs)
    return response