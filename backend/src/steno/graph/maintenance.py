"""Graph maintenance: counting what the graph holds, and resetting it.

Neo4j must stay rebuildable from Postgres + git, so a reset deletes only the graph:
every node and edge in Neo4j. What Steno was told and did stays in Postgres (connectors,
repositories, the identity registry, so Steno IDs survive; claims; coverage patterns;
job history). After a reset the repositories are marked not ingested, so the next
nightly run (or `--queue`) re-ingests every one and reconcile rebuilds agreement.
"""

from typing import Any

from neo4j import Driver

# Neo4j deletes in batches of this many nodes, so a large graph never needs one huge transaction
DELETE_BATCH = 10_000


def stats(driver: Driver, database: str) -> dict[str, Any]:
    """What the graph holds: nodes by label, edges by type, flows, and reconcile's agreement
    states. Two snapshots compare with `diff`."""

    def rows(query: str) -> list[Any]:
        records, _, _ = driver.execute_query(query, database_=database)
        return records

    nodes = {r["label"]: r["n"] for r in rows(
        "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n"
    )}  # fmt: skip
    edges = {r["type"]: r["n"] for r in rows(
        "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n"
    )}  # fmt: skip
    agreement = {r["state"]: r["n"] for r in rows(
        "MATCH ()-[r]->() WHERE r.reconciled RETURN r.agreement AS state, count(*) AS n"
    )}  # fmt: skip
    total = rows("MATCH (n) RETURN count(n) AS n")[0]["n"]
    return {
        "nodes": total,
        "edges": sum(edges.values()),
        "by_label": dict(sorted(nodes.items())),
        "by_type": dict(sorted(edges.items())),
        "agreement": dict(sorted((str(k), v) for k, v in agreement.items())),
    }


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """One line per count that changed between two snapshots: "Flow: 412 → 380 (-32)"."""
    out = []
    for key in ("nodes", "edges"):
        if before.get(key) != after.get(key):
            out.append(_line(key, before.get(key, 0), after.get(key, 0)))
    for group in ("by_label", "by_type", "agreement"):
        a, b = before.get(group, {}), after.get(group, {})
        for name in sorted(set(a) | set(b)):
            if a.get(name, 0) != b.get(name, 0):
                out.append(_line(f"{group}.{name}", a.get(name, 0), b.get(name, 0)))
    return out


def _line(name: str, a: int, b: int) -> str:
    return f"{name}: {a} → {b} ({b - a:+d})"


def wipe(driver: Driver, database: str) -> int:
    """Delete every node and edge, in batches. Returns how many nodes were deleted. Constraints
    and indexes stay; `steno graph init` recreates anything missing."""
    deleted = 0
    while True:
        records, _, _ = driver.execute_query(
            "MATCH (n) WITH n LIMIT $batch DETACH DELETE n RETURN count(n) AS n",
            batch=DELETE_BATCH,
            database_=database,
        )
        n = records[0]["n"]
        deleted += n
        if n < DELETE_BATCH:
            return deleted


def confirmed(typed: str | None, database: str) -> bool:
    """A reset goes ahead only when the person typed the database's name exactly."""
    return (typed or "").strip() == database
