"""Stable node IDs built from natural keys (docs/knowledge-graph.md §3).

Re-ingesting finds the same nodes because the same facts always produce the same IDs,
and a stub merges with the real node when its owner is ingested.
"""


def organization_id(declared_id: int) -> str:
    """Declared in Postgres (one row per deployment), so keyed by row ID, not its editable name."""
    return f"org:{declared_id}"


def space_id(declared_id: int) -> str:
    """Spaces are declared in Postgres, so their natural key is the declared row's ID.

    Not the name: an admin can rename or move a space, and everything that belongs to
    it must keep pointing at the same node.
    """
    return f"space:{declared_id}"


def repository_id(repo: str) -> str:
    return f"repo:{repo}"


def application_id(name: str) -> str:
    return f"app:{name}"


def function_id(repo: str, qualified_name: str, param_types: list[str] | None = None) -> str:
    """`param_types` is given only where the language has overloading."""
    base = f"fn:{repo}:{qualified_name}"
    return base if param_types is None else f"{base}({','.join(param_types)})"


def http_endpoint_id(app: str, method: str, path: str) -> str:
    return f"endpoint:{app}:{method.upper()}:{path}"


def topic_id(cluster: str, name: str) -> str:
    return f"topic:{cluster}:{name}"


def table_id(datastore: str, schema: str | None, name: str) -> str:
    return f"table:{datastore}:{schema or ''}:{name}"


def flow_id(app: str, trigger: str) -> str:
    return f"flow:{app}:{trigger}"
