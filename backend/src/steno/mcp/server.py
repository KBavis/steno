"""Steno's MCP server: tools for agents over Streamable HTTP.

See docs/how-it-works/retrieval-and-mcp.md §5.

Stateless (docs/how-it-works/README.md). The first tools (D54): `search`, `get_node`, `get_flow`,
`find_dependents`, `view_flow_code`; then `route_request` (D132). Every result carries
citations (D29), is cut to its `max_tokens`, and every call is logged in `tool_call` (tool,
parameters, latency, result count).
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

import steno
from steno.config import get_settings
from steno.db.models import ToolCall
from steno.db.session import session_scope
from steno.graph.driver import database, get_driver
from steno.mcp.tools import ToolError, Tools, fit

log = logging.getLogger(__name__)

# The trace ablation (D140): serve each flow's reach only, and tell agents so
REACH = get_settings().flow_view == "reach"
FLOW_HELP = (
    "3. `get_flow` for what a flow touches and the functions it reaches, by file; "
    "`leads_to` names the flows it leads to in other applications.\n"
    "4. `view_flow_code` only for the functions the question is about.\n"
    if REACH
    else "3. `get_flow` for a flow's steps; open a folded step with `under=<path>`; "
    "`expand='sync'` follows calls into other applications.\n"
    "4. `view_flow_code` only for the steps the question is about.\n"
)
GET_FLOW_REACH = (
    "What a flow touches and what it reaches: its trigger, every table, topic, and service it "
    "reads, writes, or calls (each call site cited), and the functions it reaches with their "
    "lines, grouped by file (not in the order they run). `leads_to`: flows in other "
    "applications it calls or sends to. Function names feed `view_flow_code`."
)

mcp = MCPServer(
    name="steno",
    version=steno.__version__,
    instructions=(
        "Steno answers questions about how an organization's software fits together: "
        "applications, the interfaces they expose and call, the flows that run when an "
        "interface, topic, or schedule triggers them, the data stores and topics they use, and "
        "the code behind each step.\n\n"
        "Route the question first: find the application it's about with `search` (read "
        '`where`) or `get_node`. When it could mean several ("the backend" when '
        "`budget-app` and `contextualized-backend` both fit; `also_named` in `get_node`), "
        "don't pick one: list the candidates, each with a line on what it is, and ask which "
        "is meant. Answer directly only when one application clearly fits.\n\n"
        "Every `search` answer without a `scope` carries `routing`: the spaces, applications, "
        "and flows it judged the question is about, each with a confidence, and the "
        "`alternatives` it didn't choose. With a `band`: high, proceed; medium, proceed and "
        "say it's a best match; low, show the options and ask the user which is meant. "
        "`confidence: uncalibrated` (no band): the choices are rankings from keyword "
        "matches, not judgments; treat them as medium. `shortlist`: routing couldn't tell the "
        "flows apart, so pick from the listed flows by what each `does`, or ask. "
        "`disambiguation`: several applications' names fit; ask which is meant unless the "
        "question settles it. `question_kind.next` suggests the next tool.\n\n"
        "Work top-down; an organization has thousands of flows, and no page shows them all:\n"
        "1. `search` with keywords, not the question: the nouns and verbs that would be in the "
        'code or its names ("how do I delete a conversation?" → `delete conversation`; '
        '"how do we predict categories?" → `predict category`). Try a few short searches '
        "with other words (synonyms, the domain's terms, the code's: `suggestion` for "
        "prediction) before concluding nothing exists. When the question names a kind of "
        'thing, pass `kind` (`topic` for "which topic…", `table`, `endpoint`, `flow`). '
        "Typos and the organization's abbreviations (`clt` for client, `prd` for prod) are "
        "matched; read `matched` to see why each hit came back, and `dropped` for words "
        "nothing matched. Read `where` first: every space and application with matches, and "
        "how many. `results` shows only some; when several applications match, `search` "
        "again with `scope=<application>` for each.\n"
        "2. `get_node` on an application (its flows, data, calls, names, topic agreement), "
        "a space, or `organization` (the spaces and how they communicate).\n"
        + FLOW_HELP
        + "5. `find_dependents` for who uses something, or `direction='downstream'` for what "
        "a change affects.\n"
        "6. `route_request` when you have a URL from outside (`GET /api/vet/vets`): which "
        "gateway route takes it, and which application, endpoint, and flow serve it.\n\n"
        "Before saying 'all', 'only', or 'none', check what the answer couldn't see: `where` "
        "and `more` in search; `completeness` everywhere else: `checked` is what Steno looked "
        "at, `not_checked` what it couldn't, and `unexplained` counts code Steno sees but no "
        "rule explains. No answer claims to be complete. `possible_callers` and "
        "`possible_targets` are unconfirmed hints. "
        "Where another witness checked a fact, `agreement` is `confirmed`, `code_only` (no "
        "grant or declaration), or `declared_only` (stated, no code found): report the last "
        "two as findings. Every answer cites file, lines, and commit; pass the citations on.\n\n"
        "Steno answers *what* and *where* across the organization; you do the reasoning. It "
        "records each application's placement, every way into it (endpoints, consumed topics, "
        "schedules) and out of it (calls, publishes, table reads and writes, external systems), "
        "and what each flow reaches; it doesn't record validation rules, what an interceptor "
        "requires, or what a function computes. For those, and for *exactly how*, read the "
        "cited code (`view_flow_code`) before concluding. `sources` "
        "lists the repositories an answer cites, at the commit Steno read (`head_moved`: the "
        "code has changed since), with the folders that matter: clone those, and only those, "
        "when the task needs a working copy (editing, building, running tests) or code newer "
        "than Steno's."
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
    query: str,
    ctx: Context,
    scope: str | None = None,
    kind: str | None = None,
    limit: int = 10,
    max_tokens: int = 2000,
) -> dict[str, Any]:
    """Find applications, flows, topics, endpoints, tables, data stores, and external systems
    by name, by what each flow does (its trigger, what it reads, writes, calls, and its docs),
    by what each application is for (its summary), or flows by the functions they run.
    `query`: keywords, not a sentence (`delete conversation`); every word must match, else one
    is dropped (`dropped`). Typos, word forms (`categorize` → category), and abbreviations
    (`clt` for client) match. Top-down: `where` lists every space and application with matches
    and how many; `results` takes each application's best hits in turn and may not show them
    all (`more`); `matched` says why a hit came back. `scope`: a space or application name to
    search within. `kind`: only application, flow, topic, endpoint, table, entity, datastore,
    external, interface, space, or organization. Matches words, not meaning: try other words
    when nothing fits."""
    return _run(
        ctx,
        "search",
        locals(),
        max_tokens,
        lambda t: t.search(query, scope=scope, limit=limit, kind=kind),
    )


@mcp.tool()
def get_node(
    ref: str, ctx: Context, claims: bool = False, max_tokens: int = 4000
) -> dict[str, Any]:
    """Describe a node by ID or name, at its level: the organization or a space (what it
    contains and how its parts communicate), an application (its flows, data, outbound calls,
    modules, names, and the topics it uses with each one's `agreement`), a flow (its steps),
    or anything else (its properties and connections). Agreement compares the code with other
    witnesses (deploy and ACL files, manifests): `confirmed`, `code_only`, or `declared_only`.
    `claims=true` also lists the raw statements other witnesses make about it. Start with
    `get_node("organization")`-level names from `search`, or an ID from another result."""
    return _run(ctx, "get_node", locals(), max_tokens, lambda t: t.get_node(ref, claims=claims))


@mcp.tool(description=GET_FLOW_REACH if REACH else None)
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
    Steno ingested. `paths`: the step paths or function names from `get_flow` to read
    (default: all its steps)."""
    return _run(ctx, "view_flow_code", locals(), max_tokens, lambda t: t.view_flow_code(ref, paths))


@mcp.tool()
def route_request(
    path: str, ctx: Context, method: str | None = None, max_tokens: int = 3000
) -> dict[str, Any]:
    """What serves a request from outside, such as `GET /api/vet/vets`: the gateway route its
    path matches, the request as the target application sees it after the route's rewrite
    (`GET /vets`), and the endpoint and flow it reaches. Use it when you have a URL a user,
    frontend, or another system calls; an endpoint's own `get_node` says how it's reached."""
    return _run(ctx, "route_request", locals(), max_tokens, lambda t: t.route_request(path, method))


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
        tools = Tools(get_driver(), database(), session, request_id=_request_id(ctx))
        try:
            result = call(tools)
            if result.get("citations"):
                result["sources"] = tools.sources(result["citations"])
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
