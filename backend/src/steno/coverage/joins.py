"""One-sided joins: a connection where Steno found only one end.

A topic produced but never consumed, consumed but never produced, or an HTTP endpoint called
that no application exposes. Each means the other end was missed, or is legitimately outside
the organization (triaged `expected`). It's the only check that catches a miss without knowing
the mechanism: an internal framework's sender no rule understands still leaves its receiver
with no producer.

Read from the graph when the report is read, so it's never stale: the other end may be in a
repository ingested after this one. Only a triage decision is stored (a `one_sided:` pattern).
"""

from typing import Any

from neo4j import Driver

# Channels: a topic, a queue, an organization's own channel; not an endpoint or a gRPC method
CHANNELS = """
MATCH (i:Interface) WHERE NOT i:HttpEndpoint AND NOT i:GrpcMethod
OPTIONAL MATCH (pf:Flow)-[:PRODUCES]->(i)
OPTIONAL MATCH (pf)-[:BELONGS_TO]->(pa:Application)
WITH i, collect(DISTINCT pa.name) AS producers, count(DISTINCT pf) AS produced
OPTIONAL MATCH (i)-[:STARTS]->(sf:Flow)
OPTIONAL MATCH (sf)-[:BELONGS_TO]->(sa:Application)
WITH i, producers, produced, collect(DISTINCT sa.name) AS started_by, count(DISTINCT sf) AS starts
OPTIONAL MATCH (cf:Flow)-[:CONSUMES]->(i)
OPTIONAL MATCH (cf)-[:BELONGS_TO]->(ca:Application)
WITH i, producers, produced, started_by, starts,
     collect(DISTINCT ca.name) AS polled_by, count(DISTINCT cf) AS polls
WITH i, producers, produced, started_by + polled_by AS consumers, starts + polls AS consumed
WHERE (produced > 0) <> (consumed > 0)
RETURN i.id AS id, coalesce(i.name, i.id) AS name, producers, consumers, produced > 0 AS produced
"""

# Endpoints called that no application exposes and nothing starts from
ENDPOINTS = """
MATCH (f:Flow)-[:CALLS]->(e:HttpEndpoint)
WHERE NOT ()-[:EXPOSES]->(e) AND NOT (e)-[:STARTS]->()
OPTIONAL MATCH (f)-[:BELONGS_TO]->(a:Application)
RETURN e.id AS id, e.method AS method, e.path AS path, collect(DISTINCT a.name) AS callers
"""


def one_sided(driver: Driver, database: str) -> list[dict[str, Any]]:
    """Every one-sided join in the graph, as coverage items (see `items`)."""
    with driver.session(database=database) as s:
        channels = s.run(CHANNELS).data()
        endpoints = s.run(ENDPOINTS).data()
    return items(channels, endpoints)


def items(channels: list[dict[str, Any]], endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Query rows → items: the fingerprint, how it reads, and the applications whose side
    exists (the ones that can say what the other end should be)."""
    out = []
    for row in channels:
        name = str(row["name"]).split(":", 1)[-1]
        if row["produced"]:
            label = f"Produced, never consumed · {name}"
            apps, detail = row["producers"], "no application's code consumes it"
        else:
            label = f"Consumed, never produced · {name}"
            apps, detail = row["consumers"], "no application's code produces to it"
        out.append(_item(row["id"], label, apps, detail))
    for row in endpoints:
        what = f"{row.get('method') or ''} {row.get('path') or row['id']}".strip()
        out.append(
            _item(
                row["id"],
                f"Called, not exposed by any application · {what}",
                row["callers"],
                "no ingested application exposes it",
            )
        )
    out.sort(key=lambda i: (-len(i["apps"]), i["label"]))
    return out


def _item(target: str, label: str, apps: list[str], detail: str) -> dict[str, Any]:
    names = sorted(a for a in apps if a)
    return {
        "fingerprint": f"one_sided:{target}",
        "kind": "inconsistency",
        "signal": "one_sided",
        "target": target,
        "label": label,
        "apps": names,
        "detail": detail,
    }


__all__ = ["items", "one_sided"]
