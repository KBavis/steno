"""Steno's MCP server: tools for agents over Streamable HTTP (docs/retrieval-and-mcp.md §5).

Stateless (DESIGN_DOC §6). The first tools (D54): `search`, `get_node`, `get_flow`,
`find_dependents`, `view_flow_code`. Every result carries citations (D29), is cut to its
`max_tokens`, and every call is logged in `tool_call` (tool, parameters, latency, result count).
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

import steno
from steno.db.models import ToolCall
from steno.db.session import session_scope
from steno.graph.driver import database, get_driver
from steno.mcp.tools import ToolError, Tools, fit

log = logging.getLogger(__name__)

mcp = MCPServer(
    name="steno",
    version=steno.__version__,
    instructions=(
        "Steno answers questions about how an organization's software fits together: "
        "applications, the interfaces they expose and call, the flows that run when an "
        "interface or schedule triggers them, the data stores they read and write, and the "
        "code behind each step. Start with `search` (names) or `get_node` (the organization, "
        "a space, or an application), follow a flow with `get_flow`, read its code with "
        "`view_flow_code`, and ask who depends on something with `find_dependents`. Every "
        "answer cites the file, lines, and commit each claim comes from."
    ),
)


@mcp.tool()
def ping() -> dict[str, Any]:
    """Check that Steno is up and the knowledge graph is reachable."""
    with get_driver().session(database=database()) as session:
        nodes = session.run("MATCH (n) RETURN count(n) AS n").single(strict=True)["n"]
    return {"status": "ok", "version": steno.__version__, "graph_nodes": nodes}


@mcp.tool()
def search(
    query: str, ctx: Context, scope: str | None = None, limit: int = 10, max_tokens: int = 2000
) -> dict[str, Any]:
    """Find applications, flows, interfaces, tables, and entities by name, or flows by the
    functions they run. `scope`: a space or application name to search within. Matches names
    and words, not meaning (yet): use the words the code uses (`job`, `create_project`)."""
    return _run(
        ctx, "search", locals(), max_tokens, lambda t: t.search(query, scope=scope, limit=limit)
    )


@mcp.tool()
def get_node(ref: str, ctx: Context, max_tokens: int = 4000) -> dict[str, Any]:
    """Describe a node by ID or name, at its level: the organization or a space (what it
    contains and how its parts communicate), an application (its flows, data, outbound calls,
    modules), a flow (its steps), or anything else (its properties and connections). Start
    with `get_node("organization")`-level names from `search`, or an ID from another result."""
    return _run(ctx, "get_node", locals(), max_tokens, lambda t: t.get_node(ref))


@mcp.tool()
def get_flow(
    ref: str,
    ctx: Context,
    detail: str = "significant",
    expand: str = "none",
    levels: int | None = None,
    under: str | None = None,
    max_tokens: int = 6000,
) -> dict[str, Any]:
    """A flow's steps in the order they run: each function with its file and lines, what it
    reads, writes, and calls, and branches, loops, and async work. `detail`: `significant`
    (functions that touch data or call out, and their callers) or `all` (every function,
    helpers included). `expand`: `none`, `sync` (follow calls into other applications' flows),
    or `all` (also messages it sends). Large flows are shown as an outline (as deep as fits in
    about 25 steps, or `levels` deep): a
    folded step says how many steps are `inside` and what they touch (`inside_does`); open it
    with `under=<its path>`. Step `path`s feed `view_flow_code`."""
    return _run(
        ctx,
        "get_flow",
        locals(),
        max_tokens,
        lambda t: t.get_flow(ref, detail=detail, expand=expand, levels=levels, under=under),
    )


@mcp.tool()
def view_flow_code(
    ref: str, ctx: Context, paths: list[str] | None = None, max_tokens: int = 8000
) -> dict[str, Any]:
    """The source code of a flow's steps, in one call, read from the git host at the commit
    Steno ingested. `paths`: the step paths from `get_flow` to read (default: all its steps)."""
    return _run(ctx, "view_flow_code", locals(), max_tokens, lambda t: t.view_flow_code(ref, paths))


@mcp.tool()
def find_dependents(
    ref: str,
    ctx: Context,
    direction: str = "upstream",
    depth: int = 2,
    via: str | None = None,
    max_tokens: int = 4000,
) -> dict[str, Any]:
    """Who depends on something, grouped by application and space. `upstream`: the flows
    that call, read, write, or send to it (for an application: who calls its interfaces; for
    a flow: who triggers it). `downstream`: what its flows touch and the flows that starts
    (blast radius). `depth`: hops through other flows. `via`: `http`, `messaging`, or `data`."""
    return _run(
        ctx,
        "find_dependents",
        locals(),
        max_tokens,
        lambda t: t.find_dependents(ref, direction=direction, depth=depth, via=via),
    )


# ------------------------------------------------------------------- plumbing

COUNTED = ("results", "steps", "flows", "code", "dependents", "downstream_flows", "contains")


def _run(
    ctx: Context, tool: str, params: dict[str, Any], max_tokens: int, call: Callable[[Tools], Any]
) -> dict[str, Any]:
    """Run a tool against the graph, cut its answer to `max_tokens`, and log the call."""
    params = {k: v for k, v in params.items() if k != "ctx"}
    started = time.perf_counter()
    with session_scope() as session:
        try:
            result = call(Tools(get_driver(), database(), session))
        except ToolError as exc:
            result = {"error": str(exc)}
        result = fit(result, max_tokens)
        session.add(
            ToolCall(
                session_id=_session_id(ctx),
                request_id=_request_id(ctx),
                tool=tool,
                params=params,
                latency_ms=round((time.perf_counter() - started) * 1000),
                result_count=next(
                    (len(result[k]) for k in COUNTED if isinstance(result.get(k), list)), None
                ),
            )
        )
    return result


def _request_id(ctx: Context) -> str | None:
    try:
        return str(ctx.request_id)
    except (ValueError, AttributeError):
        return None


def _session_id(ctx: Context) -> str | None:
    """The client's session, when it sends one (the server is stateless)."""
    try:
        headers = ctx.headers or {}
    except (ValueError, AttributeError):
        return None
    return headers.get("mcp-session-id") or headers.get("x-steno-session")
