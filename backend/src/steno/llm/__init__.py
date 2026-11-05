"""The LLM only writes text: summaries for applications, spaces, and the organization (D143).
Flows get a deterministic digest instead (`steno.graph.digest`).

Every call is logged in `llm_call` and its text cached in `llm_output` by input hash, so
unchanged inputs are never paid for twice (D36). In a dry run, nothing is generated: inputs
are token-counted (estimated) and cost is projected. The model is still Open
(docs/plan/decisions.md, open questions), decided from the dry run's numbers.

`providers.py`: who writes the text (LiteLLM for whatever the organization runs; the Claude Agent
SDK for development). `choice.py`: the organization's choice. `text.py`: `generate`, the cache,
and the log.
"""

from steno.llm.providers import Completion, LlmUnavailable, Provider, get_provider
from steno.llm.text import Generated, generate, input_hash

__all__ = [
    "Completion",
    "Generated",
    "LlmUnavailable",
    "Provider",
    "generate",
    "get_provider",
    "input_hash",
]
