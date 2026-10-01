from llama_index.llms.openai_like import OpenAILike

API_BASE = "https://gateway.example.com/openai"


class GatewayOpenAILike(OpenAILike):
    """The app's own subclass: a constructor rule must still recognize it."""


def get_llm(model: str) -> GatewayOpenAILike:
    return GatewayOpenAILike(model=model, api_base=f"{API_BASE}/{model}/v1", is_chat_model=True)
