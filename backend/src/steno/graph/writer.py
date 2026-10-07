"""Send a graph plan to Neo4j, writing only what changed (D59; docs/ingestion.md §7).

Read what the graph holds for this repository, compare it with the plan by stable ID
(`delta.py`), then: delete facts this repository no longer has, write facts that are new or
changed, and remove shared nodes nothing references any more. Unchanged facts aren't
touched. All of it is one transaction: a failed write changes nothing.
"""

import logging
import re
from collections import defaultdict
from typing import Any

from neo4j import Driver

from steno.graph.build import SHARED, GraphPlan
from steno.graph.delta import Delta, Existing, diff, nulls_for
from steno.graph.schema import ARCHITECTURE_LABELS, CODE_LABELS

log = logging.getLogger(__name__)
BATCH = 500
_CONSTRAINED = set(ARCHITECTURE_LABELS) | set(CODE_LABELS)
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _primary(labels: list[str]) -> str:
    """The label with a uniqueness constraint on `id`, so MERGE uses its index."""
    return next((lbl for lbl in labels if lbl in _CONSTRAINED), labels[0])


def _safe(label: str) -> str:
    if not _LABEL.match(label):  # labels and types can't be query parameters
        raise ValueError(f"invalid label or relationship type {label!r}")
    return label


def write_plan(driver: Driver, database: str, plan: GraphPlan, repo: str) -> Delta:
    """Write the plan's differences from the graph and return them."""
    with driver.session(database=database) as session:
        return session.execute_write(_write_all, plan, repo)


def _write_all(tx: Any, plan: GraphPlan, repo: str) -> Delta:
    delta = diff(plan, _read_existing(tx, plan, repo))
    primary = {nid: _primary(labels) for nid, labels in delta.labels.items()}

    def label_of(node_id: str) -> str:
        return primary.get(node_id) or _existing_label(node_id)

    # 1. What this repository no longer has
    by_type: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for etype, src, dst in delta.edges_to_remove():
        by_type[(etype, label_of(src), label_of(dst))].append({"src": src, "dst": dst})
    for (etype, src, dst), rows in by_type.items():
        _run(
            tx,
            f"UNWIND $rows AS row MATCH (a:{_safe(src)} {{id: row.src}})"
            f"-[r:{_safe(etype)}]->(b:{_safe(dst)} {{id: row.dst}}) DELETE r",
            rows,
        )
    by_label: dict[str, list[str]] = defaultdict(list)
    for nid in delta.nodes_to_remove():
        by_label[label_of(nid)].append(nid)
    for label, node_ids in by_label.items():
        _run(
            tx,
            f"UNWIND $rows AS id MATCH (n:{_safe(label)} {{id: id}}) DETACH DELETE n",
            node_ids,
        )

    # 2. New and changed nodes, then edges
    before = {nid: b for nid, b, _ in delta.nodes}
    nodes: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for nid in delta.nodes_to_write():
        node = plan.nodes[nid]
        props = _clean(node.props)
        nodes[tuple(node.labels)].append(
            {"id": nid, "props": {**props, **nulls_for(before[nid], props)}}
        )
    for labels, rows in nodes.items():
        main = _safe(_primary(list(labels)))
        extra = "".join(f":{_safe(lbl)}" for lbl in labels if lbl != main)
        _run(
            tx,
            f"UNWIND $rows AS row MERGE (n:{main} {{id: row.id}}) "
            "ON CREATE SET n.first_seen = row.props.last_seen "
            f"SET n += row.props {('SET n' + extra) if extra else ''}",
            rows,
        )
    edge_before = {key: b for key, b, _ in delta.edges}
    edges: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for key in delta.edges_to_write():
        etype, src, dst = key
        props = _clean(plan.edges[key].props)
        edges[(etype, label_of(src), label_of(dst))].append(
            {"src": src, "dst": dst, "props": {**props, **nulls_for(edge_before[key], props)}}
        )
    for (etype, src, dst), rows in edges.items():
        _run(
            tx,
            f"UNWIND $rows AS row "
            f"MATCH (a:{_safe(src)} {{id: row.src}}) MATCH (b:{_safe(dst)} {{id: row.dst}}) "
            f"MERGE (a)-[r:{_safe(etype)}]->(b) SET r += row.props",
            rows,
        )

    # 3. Shared nodes go only when nothing references them any more
    shared = " OR ".join(f"n:{lbl}" for lbl in sorted(SHARED))
    orphans = tx.run(
        f"MATCH (n) WHERE ({shared} OR n.shared) AND NOT (n)--() DELETE n RETURN count(n) AS n"
    ).single()["n"]
    if orphans:
        log.info("removed %s orphaned shared nodes", orphans)
    return delta


def _read_existing(tx: Any, plan: GraphPlan, repo: str) -> Existing:
    """This repository's nodes and edges, plus plan nodes another repository wrote (shared
    tables, stores, channels), so they aren't mistaken for new ones."""
    existing = Existing()
    for row in tx.run(
        "MATCH (n) WHERE n.repo = $repo RETURN n.id AS id, labels(n) AS labels, "
        "properties(n) AS props",
        repo=repo,
    ):
        existing.nodes[row["id"]] = (row["labels"], row["props"])
        existing.repo_nodes.add(row["id"])
    by_label: dict[str, list[str]] = defaultdict(list)
    for nid, node in plan.nodes.items():
        if nid not in existing.nodes:
            by_label[_primary(node.labels)].append(nid)
    for label, node_ids in by_label.items():
        for row in tx.run(
            f"MATCH (n:{_safe(label)}) WHERE n.id IN $ids "
            "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props",
            ids=node_ids,
        ):
            existing.nodes[row["id"]] = (row["labels"], row["props"])
    for row in tx.run(
        "MATCH (a)-[r]->(b) WHERE r.repo = $repo "
        "RETURN type(r) AS type, a.id AS src, b.id AS dst, properties(r) AS props",
        repo=repo,
    ):
        existing.edges[(row["type"], row["src"], row["dst"])] = row["props"]
    return existing


def _run(tx: Any, query: str, rows: list[Any]) -> None:
    for chunk in _chunks(rows):
        tx.run(query, rows=chunk).consume()


def _existing_label(node_id: str) -> str:
    """Nodes this plan points at but doesn't write (the declared org and spaces, another
    repository's nodes), labeled from their stable-ID prefix. Channels are the only IDs
    without a fixed prefix (`kafkatopic:orders`, `pxchannel:billing`), and they're Interfaces."""
    prefix = node_id.split(":", 1)[0]
    return ID_PREFIXES.get(prefix, "Interface")


ID_PREFIXES = {
    "org": "Organization",
    "space": "Space",
    "repo": "Repository",
    "module": "Module",
    "file": "File",
    "app": "Application",
    "flow": "Flow",
    "step": "Step",
    "endpoint": "Interface",
    "entity": "Entity",
    "table": "Table",
    "datastore": "DataStore",
    "external": "ExternalSystem",
}


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    """Neo4j properties are scalars or lists of scalars."""
    out = {}
    for k, v in props.items():
        if isinstance(v, (str, int, float, bool)) or (
            isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v)
        ):
            out[k] = v
    return out


def _chunks(rows: list[Any]) -> Any:
    for i in range(0, len(rows), BATCH):
        yield rows[i : i + BATCH]
