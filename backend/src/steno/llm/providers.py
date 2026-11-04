"""Who writes the text. One call in, one text out: no tools, no turns, no agent.

- `claude_agent_sdk`: the Claude Agent SDK on this machine's Claude Code login (or
  ANTHROPIC_API_KEY). For development; no built-in tools, no local settings or CLAUDE.md.
- `fake`: deterministic text for tests and offline runs.

An organization's gateway (Azure) is another provider behind the same `complete`.
"""

import asyncio
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Protocol

from steno.config import get_settings

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
        return asyncio.run(self._complete(system, prompt))

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


def get_provider(name: str | None = None, model: str | None = None) -> Provider:
    name = name or get_settings().llm_provider
    if name == "claude_agent_sdk":
        return ClaudeAgentSdk(model)
    if name == "fake":
        return Fake(model or "fake")
    raise ValueError(f"unknown LLM provider {name!r}: claude_agent_sdk | fake")
