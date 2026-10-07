from llama_index.core.tools import FunctionTool


def lookup(key: str) -> str:
    ...


class Tools:
    async def _grep_search(self, pattern: str) -> str:
        ...

    def build(self):
        return [
            FunctionTool.from_defaults(async_fn=self._grep_search, name="grep_search"),
            FunctionTool.from_defaults(fn=lookup, name="lookup"),
        ]
