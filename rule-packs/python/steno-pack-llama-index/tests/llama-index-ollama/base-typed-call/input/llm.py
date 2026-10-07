from llama_index.core.llms.function_calling import FunctionCallingLLM
from llama_index.llms.ollama import Ollama


class BaseProvider:
    def get_llm(self) -> FunctionCallingLLM:   # typed as the library base class
        raise NotImplementedError

    async def complete(self, prompt: str):
        llm = self.get_llm()
        return await llm.acomplete(prompt)


class OllamaProvider(BaseProvider):
    def get_llm(self) -> FunctionCallingLLM:
        return Ollama(model="gpt-oss:latest", base_url="http://localhost:11434")
