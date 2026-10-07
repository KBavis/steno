from collections.abc import Callable

from llama_index.core.tools import FunctionTool


class Tools:
    async def _grep_search(self, pattern: str) -> str:
        ...

    def build(self):
        return self._tool(async_fn=self._grep_search, name="grep_search")

    def _tool(self, async_fn: Callable, name: str) -> FunctionTool:
        return FunctionTool.from_defaults(async_fn=async_fn, name=name)
