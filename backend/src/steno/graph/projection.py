"""Project declared structure from Postgres into Neo4j (D33).

The organization and its spaces are declared by admins and live in Postgres; Neo4j gets
a copy so the containment tree (Organization → Space → …) exists before anything is
ingested. `sync_declared` makes Neo4j match Postgres exactly, so it's safe to call
after any change.
"""

from typing import Any

from neo4j import Driver, ManagedTransaction
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.db.models import Organization, Space
from steno.graph import ids

_MERGE_ORG = """
MERGE (o:Organization {id: $id})
SET o.name = $name, o.description = $description
WITH o
MATCH (other:Organization) WHERE other.id <> $id
DETACH DELETE other
"""

_MERGE_SPACES = """
UNWIND $spaces AS row
MERGE (s:Space {id: row.id})
SET s.name = row.name, s.description = row.description
"""

# Run after every space exists. The old BELONGS_TO is replaced, so a space moved to
# another parent keeps exactly one owner.
_SET_OWNERS = """
UNWIND $spaces AS row
MATCH (s:Space {id: row.id})
OPTIONAL MATCH (s)-[old:BELONGS_TO]->()
DELETE old
WITH s, row
MATCH (owner {id: row.owner_id})
MERGE (s)-[:BELONGS_TO]->(owner)
"""

_DELETE_STALE_SPACES = """
MATCH (s:Space) WHERE NOT s.id IN $ids
DETACH DELETE s
"""


def sync_declared(session: Session, driver: Driver, database: str) -> int:
    """Project the organization and its spaces. Returns the number of spaces."""
    org = session.get(Organization, 1)
    if org is None:
        return 0  # Nothing to hang spaces on yet; onboarding creates the organization first
    org_id = ids.organization_id(org.id)
    rows = [
        {
            "id": ids.space_id(s.id),
            "name": s.name,
            "description": s.description,
            "owner_id": ids.space_id(s.parent_id) if s.parent_id is not None else org_id,
        }
        for s in session.scalars(select(Space))
    ]
    with driver.session(database=database) as neo:
        neo.execute_write(
            _sync, {"id": org_id, "name": org.name, "description": org.description}, rows
        )
    return len(rows)


def _sync(tx: ManagedTransaction, org: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    tx.run(_MERGE_ORG, **org).consume()
    tx.run(_DELETE_STALE_SPACES, ids=[r["id"] for r in rows]).consume()
    tx.run(_MERGE_SPACES, spaces=rows).consume()
    tx.run(_SET_OWNERS, spaces=rows).consume()
