from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.llms.openai import OpenAI


def openai_llm() -> OpenAI:
    return OpenAI(model="gpt-4o-mini")


def azure_llm() -> AzureOpenAI:
    # A subclass of OpenAI, but named differently: left to llama-index-azure-openai
    return AzureOpenAI(engine="chat", model="gpt-4o", azure_endpoint="https://contoso.openai.azure.com")
