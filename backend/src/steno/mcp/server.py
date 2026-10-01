"""Steno's MCP server: tools for agents over Streamable HTTP (docs/retrieval-and-mcp.md §5).

Stateless (DESIGN_DOC §6). Every tool that returns facts must carry citations (D29) and
accept `max_tokens`. Only `ping` exists so far; the tool set in D19 comes next.
"""

from typing import Any

from mcp.server.mcpserver import MCPServer

import steno
from steno.graph.driver import database, get_driver

mcp = MCPServer(
    name="steno",
    version=steno.__version__,
    instructions=(
        "Steno answers questions about how an organization's software fits together: "
        "applications, interfaces, flows, data stores, and the code behind them."
    ),
)


@mcp.tool()
def ping() -> dict[str, Any]:
    """Check that Steno is up and the knowledge graph is reachable."""
    with get_driver().session(database=database()) as session:
        nodes = session.run("MATCH (n) RETURN count(n) AS n").single(strict=True)["n"]
    return {"status": "ok", "version": steno.__version__, "graph_nodes": nodes}
