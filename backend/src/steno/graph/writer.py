"""Send a graph plan to Neo4j: MERGE by stable ID, then remove what an earlier run of the
same repository left behind (docs/ingestion.md §7, Stable IDs and replacement by scope).

All of it is one transaction: a failed write changes nothing."""

import logging
import re
from collections import defaultdict
from typing import Any

from neo4j import Driver

from steno.graph.build import SHARED, GraphPlan
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


def write_plan(
    driver: Driver, database: str, plan: GraphPlan, repo: str, job_id: int
) -> dict[str, Any]:
    by_labels: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for n in plan.nodes.values():
        by_labels[tuple(n.labels)].append({"id": n.id, "props": _clean(n.props)})
    primary = {n.id: _primary(n.labels) for n in plan.nodes.values()}
    by_type: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in plan.edges.values():
        src, dst = primary.get(e.src), primary.get(e.dst)
        if src is None:  # an edge into a node written by another job (e.g. a Space)
            src = _existing_label(e.src)
        if dst is None:
            dst = _existing_label(e.dst)
        by_type[(e.type, src, dst)].append({"src": e.src, "dst": e.dst, "props": _clean(e.props)})

    # One transaction: if anything fails, Neo4j keeps exactly what the previous run wrote
    with driver.session(database=database) as session:
        removed = session.execute_write(_write_all, by_labels, by_type, repo, job_id)
    return {**plan.counts(), "removed": removed}


def _write_all(
    tx: Any,
    by_labels: dict[tuple[str, ...], list[dict[str, Any]]],
    by_type: dict[tuple[str, str, str], list[dict[str, Any]]],
    repo: str,
    job_id: int,
) -> dict[str, int]:
    for labels, rows in by_labels.items():
        main = _safe(_primary(list(labels)))
        extra = "".join(f":{_safe(lbl)}" for lbl in labels if lbl != main)
        query = (
            f"UNWIND $rows AS row MERGE (n:{main} {{id: row.id}}) "
            "ON CREATE SET n.first_seen = row.props.last_seen "
            f"SET n += row.props {('SET n' + extra) if extra else ''}"
        )
        for chunk in _chunks(rows):
            tx.run(query, rows=chunk).consume()
    for (etype, src, dst), rows in by_type.items():
        query = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{_safe(src)} {{id: row.src}}) MATCH (b:{_safe(dst)} {{id: row.dst}}) "
            f"MERGE (a)-[r:{_safe(etype)}]->(b) SET r += row.props"
        )
        for chunk in _chunks(rows):
            tx.run(query, rows=chunk).consume()
    return _remove_stale(tx, repo, job_id)


def _remove_stale(tx: Any, repo: str, job_id: int) -> dict[str, int]:
    """Facts this repository's earlier runs wrote that this run didn't: gone from the code."""
    edges = tx.run(
        "MATCH ()-[r]->() WHERE r.repo = $repo AND r.ingestion_job <> $job "
        "DELETE r RETURN count(r) AS n",
        repo=repo,
        job=job_id,
    ).single()["n"]
    shared = " AND ".join(f"NOT n:{lbl}" for lbl in sorted(SHARED))
    nodes = tx.run(
        f"MATCH (n) WHERE n.repo = $repo AND n.ingestion_job <> $job AND {shared} "
        "DETACH DELETE n RETURN count(n) AS n",
        repo=repo,
        job=job_id,
    ).single()["n"]
    # Shared nodes go only when nothing references them any more
    orphans = tx.run(
        "MATCH (n) WHERE (n:Table OR n:DataStore OR n:ExternalSystem) AND NOT (n)--() "
        "DELETE n RETURN count(n) AS n"
    ).single()["n"]
    return {"edges": edges, "nodes": nodes, "orphaned_shared_nodes": orphans}


def _existing_label(node_id: str) -> str:
    """Nodes this plan points at but doesn't write: the declared org and spaces."""
    prefix = node_id.split(":", 1)[0]
    return {"org": "Organization", "space": "Space"}.get(prefix, "Space")


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
