from llama_index.llms.ollama import Ollama


class OllamaProvider:
    def get_llm(self) -> Ollama:
        return Ollama(model="gpt-oss:latest", base_url="http://localhost:11434", request_timeout=300)


async def summarize(provider: OllamaProvider, text: str):
    llm = provider.get_llm()
    return await llm.acomplete(f"Summarize: {text}")
