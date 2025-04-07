from langchain_huggingface import HuggingFaceEndpoint
import warnings
import os
warnings.filterwarnings("ignore")

HF_API_TOKEN = os.getenv('HUGGINGFACE_API_KEY')

llm = HuggingFaceEndpoint(
    repo_id='tiiuae/falcon-7b-instruct',
    huggingfacehub_api_token=HF_API_TOKEN,
    task='text-generation'
)

response = llm.invoke('How to compute pi?')
print(response)