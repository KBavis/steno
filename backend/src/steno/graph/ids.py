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


def step_id(flow: str, symbol: str, bound_to: str | None = None, occurrence: int = 1) -> str:
    """A step is keyed by the function it runs, not its position, so inserting a step doesn't
    renumber the others: `step:flow:app:POST /x:app.svc.Svc.run@app.svc.Impl#2`. `bound_to`
    names the subclass an inherited method runs for; `occurrence` counts repeats in one flow."""
    base = f"step:{flow}:{symbol}"
    if bound_to:
        base = f"{base}@{bound_to}"
    return f"{base}#{occurrence}" if occurrence > 1 else base


def trace_entry_id(flow: str, path: str) -> str:
    """Not a node: names one entry of a flow's trace, so views and details can point at it."""
    return f"trace:{flow}#{path}"


def channel_id(label: str, name: str) -> str:
    """A messaging interface known by its name alone (D61): a Kafka topic, a queue, or an
    org-defined channel. Not scoped to an application, so a sender and a receiver in different
    applications meet on the same node."""
    return f"{label.lower()}:{name}"


def http_endpoint_id(app: str, method: str, path: str) -> str:
    return f"endpoint:{app}:{method.upper()}:{path}"


def topic_id(cluster: str, name: str) -> str:
    return f"topic:{cluster}:{name}"


def table_id(datastore: str, schema: str | None, name: str) -> str:
    return f"table:{datastore}:{schema or ''}:{name}"


def flow_id(app: str, trigger: str) -> str:
    return f"flow:{app}:{trigger}"


def module_id(repo: str, path: str) -> str:
    return f"module:{repo}:{path}"


def file_id(repo: str, path: str) -> str:
    return f"file:{repo}:{path}"


def entity_id(app: str, symbol: str) -> str:
    return f"entity:{app}:{symbol}"


def datastore_id(vendor: str, host: str, database: str) -> str:
    return f"datastore:{vendor}:{host}:{database}"


def external_system_id(host_or_name: str) -> str:
    return f"external:{host_or_name}"
