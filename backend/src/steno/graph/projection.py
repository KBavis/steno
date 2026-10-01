"""Project declared structure from Postgres into Neo4j (D33).

Spaces are declared by admins and live in Postgres; Neo4j gets a copy so the
containment tree (Organization → Space → …) exists before anything is ingested.
"""

from neo4j import Driver
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.db.models import Space
from steno.graph import ids

_MERGE_ORG = """
MERGE (o:Organization {id: $id})
SET o.name = $name
"""

_MERGE_SPACE = """
MERGE (s:Space {id: $id})
SET s.name = $name, s.description = $description
WITH s
MATCH (owner {id: $owner_id})
MERGE (s)-[:BELONGS_TO]->(owner)
"""


def project_spaces(session: Session, driver: Driver, database: str, org_name: str) -> int:
    spaces = {s.id: s for s in session.scalars(select(Space))}

    def path(space: Space) -> list[str]:
        names = [space.name]
        while space.parent_id is not None:
            space = spaces[space.parent_id]
            names.append(space.name)
        return names[::-1]

    org = ids.organization_id(org_name)
    # Parents before children, so each space's owner already exists.
    ordered = sorted(spaces.values(), key=lambda s: len(path(s)))
    with driver.session(database=database) as neo:
        neo.run(_MERGE_ORG, id=org, name=org_name).consume()
        for space in ordered:
            p = path(space)
            owner = ids.space_id(p[:-1]) if len(p) > 1 else org
            neo.run(
                _MERGE_SPACE,
                id=ids.space_id(p),
                name=space.name,
                description=space.description,
                owner_id=owner,
            ).consume()
    return len(ordered)
