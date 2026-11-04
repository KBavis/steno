"""What's on an application's surface (D136): the facts a rule may state.

Used by the coverage report (a dropped match is an item only when its rule states a surface
fact, D146) and by `steno rules check` (a rule that states no surface fact is refused: the
pattern should be marked harmless instead, D147).
"""

from typing import Any

from steno.rule_packs.packs import Rule

SURFACE_NODES = {
    "Application", "Interface", "HttpEndpoint", "GrpcMethod", "KafkaTopic", "Queue", "Table",
    "DataStore", "ExternalSystem", "Route", "Gateway", "Schedule",
}  # fmt: skip
SURFACE_EDGES = {"CALLS", "PRODUCES", "CONSUMES", "READS_FROM", "WRITES_TO", "STARTS", "EXPOSES"}
# Clues that complete a surface fact elsewhere: a table name, a config value, a client
SURFACE_CLUES = {"table_of", "property", "client", "provides", "prefix", "route"}


def on_surface(rule: Rule, clues: bool = False) -> bool:
    """Whether a rule states something on the surface: an entry point, a claim, an
    interceptor, or a node or edge an application's surface is made of. With `clues`, a clue
    that completes a surface fact counts too (a `table_of` that names an entity's table)."""
    for e in rule.emit:
        if not isinstance(e, dict):
            continue
        if {"entry_point", "claim", "interceptor"} & set(e):
            return True
        if names(e.get("node")) & SURFACE_NODES or names(e.get("edge")) & SURFACE_EDGES:
            return True
        if names(e.get("to")) & SURFACE_NODES:
            return True
        if clues and names(e.get("clue")) & SURFACE_CLUES:
            return True
    return False


def names(value: Any) -> set[str]:
    """The type names an emit field holds: `KafkaTopic`, `[Table, View]`, `{KafkaTopic: {…}}`."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {str(k) for k in value}
    if isinstance(value, list):
        return {n for v in value for n in names(v)}
    return set()


__all__ = ["SURFACE_EDGES", "SURFACE_NODES", "names", "on_surface"]
