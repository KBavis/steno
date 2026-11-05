"""Who writes the text. One call in, one text out: no tools, no turns, no agent.

- `litellm`: whatever the organization runs (D52, D149): its LLM gateway (any
  OpenAI-compatible address), Azure, Bedrock, Vertex, or a vendor's API, by LiteLLM's model
  name and, for a gateway, its address. The key is a reference (`env:NAME`), never stored.
- `claude_agent_sdk`: the Claude Agent SDK on this machine's Claude Code login (or
  ANTHROPIC_API_KEY). For development; no built-in tools, no local settings or CLAUDE.md.
- `fake`: deterministic text for tests and offline runs.

Which one runs is the organization's choice (`choice.py`). A provider that can't be reached
raises `LlmUnavailable`: summaries are skipped, with why, and the ingestion goes on.
"""

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Protocol

from steno.config import get_settings
from steno.connectors.git import CloneError, resolve_secret

TIMEOUT_S = 120
# Errors that say the provider can't be used right now, not that the input is bad
UNAVAILABLE = {"rate_limit", "billing_error", "authentication_failed"}


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class LlmUnavailable(RuntimeError):
    """The provider can't write right now (a usage limit, billing, a failed login): stop the
    run instead of recording empty text."""


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, prompt: str) -> Completion: ...


class ClaudeAgentSdk:
    name = "claude_agent_sdk"

    def __init__(self, model: str | None = None):
        self.model = model or get_settings().llm_model

    def complete(self, system: str, prompt: str) -> Completion:
        try:
            return asyncio.run(self._complete(system, prompt))
        except ImportError as exc:
            raise LlmUnavailable("the Claude Agent SDK isn't installed") from exc
        except Exception as exc:
            # No Claude Code on this machine, or it can't start: not a problem with the input
            if type(exc).__name__ in ("CLINotFoundError", "CLIConnectionError"):
                raise LlmUnavailable(f"Claude Code can't be used here: {exc}") from exc
            raise

    async def _complete(self, system: str, prompt: str) -> Completion:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            RateLimitEvent,
            ResultMessage,
            TextBlock,
            query,
        )

        started = time.perf_counter()
        parts: list[str] = []
        result: Any = None
        unavailable: str | None = None
        with tempfile.TemporaryDirectory(prefix="steno-llm-") as cwd:
            options = ClaudeAgentOptions(
                tools=[],  # no built-in tools: text in, text out
                allowed_tools=[],
                setting_sources=[],  # nothing from this machine's Claude settings or CLAUDE.md
                system_prompt=system,
                model=self.model,
                max_turns=1,
                cwd=cwd,
            )
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, RateLimitEvent):
                    if message.rate_limit_info.status == "rejected":
                        unavailable = "Claude's usage limit is reached"
                elif isinstance(message, AssistantMessage):
                    if message.error in UNAVAILABLE:
                        unavailable = f"Claude: {message.error.replace('_', ' ')}"
                    parts.extend(b.text for b in message.content if isinstance(b, TextBlock))
                elif isinstance(message, ResultMessage):
                    result = message
        if unavailable:
            raise LlmUnavailable(unavailable)
        if result is not None and result.is_error:
            raise RuntimeError(f"{result.subtype}: {result.result or ''}".strip())
        usage = (result.usage if result is not None else None) or {}
        return Completion(
            text="".join(parts).strip(),
            model=self.model,
            # Every input token, however it was billed: new, written to the cache, read from it
            input_tokens=sum(
                int(usage.get(k) or 0)
                for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
            ),
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cost_usd=float(getattr(result, "total_cost_usd", 0) or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class LiteLlm:
    """Any LLM the organization runs, through LiteLLM (D52). `model` is LiteLLM's name for it:
    `openai/<model>` with `base_url` for a gateway that speaks the OpenAI API,
    `azure/<deployment>`, `bedrock/<model>`, and so on."""

    name = "litellm"

    def __init__(self, model: str, base_url: str | None = None, api_key_ref: str | None = None):
        self.model = model
        self.base_url = base_url or None
        self.api_key_ref = api_key_ref or None

    def complete(self, system: str, prompt: str) -> Completion:
        key = self._key()
        # LiteLLM fetches its price list from the internet on import unless told not to
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        try:
            import litellm
        except ImportError as exc:
            raise LlmUnavailable("LiteLLM isn't installed") from exc
        litellm.suppress_debug_info = True  # its failures are raised, not printed
        started = time.perf_counter()
        try:
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                api_base=self.base_url,
                # A gateway may authenticate by network; LiteLLM still insists on a key
                api_key=key or ("none" if self.base_url else None),
                timeout=TIMEOUT_S,
                drop_params=True,  # a gateway may not take every parameter
            )
        except Exception as exc:  # noqa: BLE001 - every provider fails differently
            # Whatever went wrong (a login, a limit, a wrong model name, a gateway that's down),
            # it's about reaching the LLM: say so without the key, and skip the summaries
            text = str(exc).replace(key, "***") if key else str(exc)
            raise LlmUnavailable(f"{type(exc).__name__}: {text[:300]}") from exc
        usage = getattr(response, "usage", None)
        try:
            cost = float(litellm.completion_cost(completion_response=response) or 0)
        except Exception:  # noqa: BLE001 - a gateway's model has no public price
            cost = 0.0
        return Completion(
            text=(response.choices[0].message.content or "").strip(),
            model=self.model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cost_usd=cost,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _key(self) -> str | None:
        if not self.api_key_ref:
            return None
        try:
            return resolve_secret(self.api_key_ref)
        except CloneError as exc:
            raise LlmUnavailable(str(exc)) from exc


class Fake:
    """Text from the prompt itself, for tests: the same prompt always gets the same text."""

    name = "fake"

    def __init__(self, model: str = "fake", reply: str | None = None):
        self.model = model
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> Completion:
        self.calls.append((system, prompt))
        first = next((x for x in prompt.splitlines() if x.strip()), "")
        text = self.reply or f"LINE: Summary of {first.strip()}\nSUMMARY: Written from the inputs."
        return Completion(text=text, model=self.model, input_tokens=len(prompt) // 4)


def get_provider(
    name: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_ref: str | None = None,
) -> Provider:
    name = name or get_settings().llm_provider
    if name == "litellm":
        if not (model := model or get_settings().llm_model):
            raise ValueError("litellm needs a model")
        return LiteLlm(model, base_url, api_key_ref)
    if name == "claude_agent_sdk":
        return ClaudeAgentSdk(model)
    if name == "fake":
        return Fake(model or "fake")
    raise ValueError(f"unknown LLM provider {name!r}: litellm | claude_agent_sdk | fake")
