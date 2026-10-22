"""Rule drafters (D124, D130): an LLM investigates a pattern and drafts a rule with its tests.

One contract for every provider: a `DraftRequest` (the pattern, its samples, what the kit
found) goes in, a `DraftResult` (a proven rule, or why not) comes out. The proof sits outside
the model: a drafted rule is accepted only when its own test cases pass (`kit.test_rule`).

Providers: `claude_agent_sdk` (built: the Claude Agent SDK, using the local Claude Code login or
an organization's `ANTHROPIC_API_KEY`); `openai`, `gemini`, `azure_openai` (registered, not
implemented yet). `STENO_RULE_DRAFTER` chooses.
"""

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from steno.config import get_settings
from steno.curation import kit

log = logging.getLogger(__name__)

RULE_FORMAT = Path(__file__).resolve().parents[4] / "docs/how-it-works/ingestion/rules-and-packs.md"


@dataclass
class Sample:
    repository: str
    file: str
    line: int | None
    function: str | None
    snippet: str  # the code around the occurrence, with line numbers


@dataclass
class DraftRequest:
    fingerprint: str
    signal: str
    label: str
    owner: str | None
    reach: dict[str, int]  # repositories, applications, occurrences
    samples: list[Sample]
    siblings: list[str] = field(default_factory=list)  # other patterns the same owner has
    language: str = "java"


@dataclass
class DraftResult:
    kind: str  # rule | ignore | vocabulary | needs_person
    summary: str = ""
    facet: str | None = None
    rule: str | None = None  # the rule's YAML
    cases: dict[str, Any] = field(default_factory=dict)  # name → {files, expected}
    pack: dict[str, Any] = field(default_factory=dict)  # suggested pack.yaml fields (D129)
    notes: str = ""
    proof: dict[str, Any] = field(default_factory=dict)  # the test results it passed
    cost_usd: float = 0.0
    turns: int = 0
    error: str | None = None


class Drafter(Protocol):
    name: str

    def draft(self, request: DraftRequest, read_file: Callable[[str, str], str]) -> DraftResult:
        """Investigate one pattern and propose. `read_file(repository, path)` reads a file from
        the git host at the ingested commit, for looking around the samples."""
        ...


class NotImplementedDrafter:
    """A provider with a place in the contract and no implementation yet."""

    def __init__(self, name: str):
        self.name = name

    def draft(self, request: DraftRequest, read_file: Callable[[str, str], str]) -> DraftResult:
        raise NotImplementedError(
            f"the {self.name} drafter isn't implemented yet; set STENO_RULE_DRAFTER="
            "claude_agent_sdk"
        )


def get_drafter(name: str | None = None) -> Drafter:
    name = name or get_settings().rule_drafter
    if name == "claude_agent_sdk":
        return ClaudeAgentSdkDrafter()
    if name in ("openai", "gemini", "azure_openai"):
        return NotImplementedDrafter(name)
    raise ValueError(f"unknown rule drafter {name!r}")


# ------------------------------------------------------------------ the prompt

SYSTEM = """You write rules for Steno, which builds a knowledge graph of an organization's software
by matching code with deterministic rules (ast-grep patterns plus what the match means).
Coverage found a pattern no rule explains. Investigate it and submit exactly one proposal
with the `submit` tool:

- kind "rule": a rule that turns this pattern into graph facts, with test cases built from the
  real samples (at least one positive case, and a negative case when the pattern could
  over-match). It's accepted only if its own cases pass, so run `test_rule` first and fix what
  fails.
- kind "ignore": the pattern serves no facet (logging, formatting, test helpers, plain
  utilities): say which and why.
- kind "vocabulary": representing it needs a node, edge, or property type `vocabulary` doesn't
  have. Describe what's needed and which use case it serves. Never invent types in a rule.
- kind "needs_person": you can't tell what it means from the code available.

Use only the emits, node types, relationships, clue and claim types that `vocabulary` lists;
the rule format is in `rule_format`. Check `find_rules` first: extend the idea of an existing
rule instead of duplicating it. Read more code with `read_file` when the samples don't settle
what the pattern means (the framework's own source, if it's in a repository, is the best
documentation). In the pack you suggest, a rule for a public library (Spring, Kafka clients,
anything from a public registry) belongs in a core pack; a rule for the organization's own
framework belongs in an organization pack.

The `summary` is what the reviewer reads first: one sentence, at most 25 words, saying what
code it matches and what fact it records (or, for ignore and vocabulary, why). No hedging,
no background."""


def prompt_of(request: DraftRequest) -> str:
    parts = [
        f"Pattern: {request.label}",
        f"Fingerprint: {request.fingerprint} (signal: {request.signal})",
        f"Owner: {request.owner or 'the application itself'}",
        f"Reach: {json.dumps(request.reach)}",
        f"Language: {request.language}",
    ]
    if request.siblings:
        parts.append(
            "Other unexplained patterns with the same owner: " + "; ".join(request.siblings[:15])
        )
    for i, s in enumerate(request.samples, start=1):
        where = f"{s.repository}:{s.file}" + (f":{s.line}" if s.line else "")
        parts.append(f"\nSample {i} ({where}, in {s.function or '?'}):\n```\n{s.snippet}\n```")
    return "\n".join(parts)


CASES_HELP = (
    'JSON: {"case-name": {"files": {"path": "code"}, "expected": {"nodes": [...], '
    '"edges": [...], "entry_points": [...], "clues": [...]}}}'
)
PACK_HELP = (
    'JSON pack.yaml fields, e.g. {"name": "acme-messaging", "language": "java", '
    '"source": "org", "enabled_when": {"dependencies": ["com.acme:messaging"]}}'
)
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "rule": {"type": "string", "description": "the rule's YAML"},
        "cases": {"type": "string", "description": CASES_HELP},
        "pack": {"type": "string", "description": PACK_HELP},
    },
    "required": ["rule", "cases"],
}


# -------------------------------------------------------------- Claude Agent SDK


class ClaudeAgentSdkDrafter:
    """Drafts with the Claude Agent SDK (D130). No built-in tools (no shell, no file writes):
    only Steno's own, in-process. Capped by turns and dollars per pattern. Authentication is
    the Claude Code login on this machine, or `ANTHROPIC_API_KEY` when an organization sets
    one."""

    name = "claude_agent_sdk"

    def draft(self, request: DraftRequest, read_file: Callable[[str, str], str]) -> DraftResult:
        return asyncio.run(self._draft(request, read_file))

    async def _draft(
        self, request: DraftRequest, read_file: Callable[[str, str], str]
    ) -> DraftResult:
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            ResultMessage,
            create_sdk_mcp_server,
            query,
            tool,
        )

        settings = get_settings()
        submitted: dict[str, DraftResult] = {}

        def text(value: Any, error: bool = False) -> dict[str, Any]:
            body = value if isinstance(value, str) else json.dumps(value, indent=1, default=str)
            return {
                "content": [{"type": "text", "text": body}],
                **({"is_error": True} if error else {}),
            }

        @tool("vocabulary", "Everything a rule may emit: node types and their identity, "
              "relationships, clue and claim types, anchors.", {})  # fmt: skip
        async def vocabulary(args: dict[str, Any]) -> dict[str, Any]:
            return text(kit.vocabulary())

        @tool("rule_format", "The rule format: match, where, emit, clues, config rules, packs, "
              "tests (Markdown).", {})  # fmt: skip
        async def rule_format(args: dict[str, Any]) -> dict[str, Any]:
            return text(RULE_FORMAT.read_text() if RULE_FORMAT.exists() else "unavailable")

        @tool("find_rules", "Existing rules whose id, description, or matched names contain "
              "the query (e.g. an annotation or type name).", {"query": str})  # fmt: skip
        async def find_rules(args: dict[str, Any]) -> dict[str, Any]:
            found = kit.find_rules(args["query"])[:20]
            rows = [
                {"pack": r.pack, "rule": r.rule, "description": r.description,
                 "mentions": r.mentions[:10]}
                for r in found
            ]  # fmt: skip
            return text(rows or "no rule mentions it")

        @tool("read_file", "Read a file from a repository at the ingested commit.",
              {"repository": str, "path": str})  # fmt: skip
        async def read(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return text(read_file(args["repository"], args["path"])[:60_000])
            except Exception as exc:  # noqa: BLE001 - shown to the drafter, which can try another
                return text(f"can't read it: {exc}", error=True)

        schema = DRAFT_SCHEMA

        @tool("test_rule", "Run a draft rule against test cases, like `steno rules test`. "
              "Returns each case's missing and unexpected facts.", schema)  # fmt: skip
        async def test(args: dict[str, Any]) -> dict[str, Any]:
            try:
                results = _run_cases(args)
            except Exception as exc:  # noqa: BLE001 - a malformed draft is the drafter's to fix
                return text(f"the draft didn't run: {type(exc).__name__}: {exc}", error=True)
            return text(results)

        submit_schema = {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["rule", "ignore", "vocabulary", "needs_person"]},
            "summary": {"type": "string", "description": "one or two sentences for the reviewer: "
                        "what it recognizes and what facts it produces (or why not)"},
            "facet": {"type": "string", "description": "the facet it serves: Behavior, Interfaces, "
                      "Messaging, Data, Schedules, Routing, Components, Artifacts, Structure"},
            "rule": {"type": "string"}, "cases": {"type": "string"}, "pack": {"type": "string"},
            "notes": {"type": "string"},
        }, "required": ["kind", "summary"]}  # fmt: skip

        @tool("submit", "Submit your one proposal. A rule is accepted only if all its cases "
              "pass; otherwise the failures come back to fix.", submit_schema)  # fmt: skip
        async def submit(args: dict[str, Any]) -> dict[str, Any]:
            result = DraftResult(kind=args["kind"], summary=args.get("summary", ""),
                                 facet=args.get("facet"), notes=args.get("notes", ""))  # fmt: skip
            if result.kind == "rule":
                try:
                    proof = _run_cases(args)
                except Exception as exc:  # noqa: BLE001
                    return text(f"not accepted: the draft didn't run: {exc}", error=True)
                if not all(c["passed"] for c in proof):
                    return text({"accepted": False, "cases": proof}, error=True)
                result.rule = args["rule"]
                result.cases = json.loads(args["cases"])
                result.pack = json.loads(args.get("pack") or "{}")
                result.proof = {"cases": proof}
            submitted["result"] = result
            return text("accepted; you're done")

        server = create_sdk_mcp_server(
            "steno", tools=[vocabulary, rule_format, find_rules, read, test, submit]
        )
        names = ["vocabulary", "rule_format", "find_rules", "read_file", "test_rule", "submit"]
        cost, turns, error = 0.0, 0, None
        with tempfile.TemporaryDirectory(prefix="steno-drafter-") as cwd:
            options = ClaudeAgentOptions(
                tools=[],  # no built-in tools: no shell, no file writes
                allowed_tools=[f"mcp__steno__{n}" for n in names],
                mcp_servers={"steno": server},
                strict_mcp_config=True,
                setting_sources=[],  # nothing from this machine's Claude settings or CLAUDE.md
                system_prompt=SYSTEM,
                model=settings.rule_drafter_model,
                effort=settings.rule_drafter_effort,
                max_turns=settings.rule_drafter_max_turns,
                max_budget_usd=settings.rule_drafter_max_usd,
                cwd=cwd,
            )
            try:
                async for message in query(prompt=prompt_of(request), options=options):
                    if isinstance(message, ResultMessage):
                        cost = message.total_cost_usd or 0.0
                        turns = message.num_turns
                        if message.is_error and "result" not in submitted:
                            error = f"{message.subtype}: {message.result or ''}".strip()
            except Exception as exc:  # noqa: BLE001 - recorded on the proposal, never raised
                error = f"{type(exc).__name__}: {exc}"
        result = submitted.get("result") or DraftResult(
            kind="needs_person", summary="the drafter submitted nothing", error=error
        )
        result.cost_usd, result.turns = cost, turns
        result.error = result.error or error
        return result


def _run_cases(args: dict[str, Any]) -> list[dict[str, Any]]:
    cases = json.loads(args["cases"]) if isinstance(args["cases"], str) else args["cases"]
    pack = (
        json.loads(args.get("pack") or "{}")
        if isinstance(args.get("pack"), str)
        else args.get("pack")
    )
    results = kit.test_rule(
        args["rule"], cases, pack={"language": (pack or {}).get("language", "java")}
    )
    return [{"case": r.case, "passed": r.passed, "missing": r.missing, "unexpected": r.unexpected,
             "errors": r.errors[:5]} for r in results]  # fmt: skip
