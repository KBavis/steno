from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI

ENDPOINT = "https://contoso.openai.azure.com"


def llm(deployment: str) -> AzureOpenAI:
    return AzureOpenAI(engine=deployment, model="gpt-4o", azure_endpoint=ENDPOINT, api_version="2024-06-01")


def embedder() -> AzureOpenAIEmbedding:
    return AzureOpenAIEmbedding(model="text-embedding-3-small", azure_endpoint=ENDPOINT)
