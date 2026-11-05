"""Calls between applications in different repositories, by the service a host names.

Each repository is built alone, so a call to another repository's service ends at an
`ExternalSystem`. Identity knows every application's names across repositories, so reconcile
joins them: a host names an application (`route_target`), and the application's endpoint with
that method and path is the call's target. Never from a config key's name or a path alone: a
path doesn't say which service a call reaches (D64).

Each link says how sure it is. An exact name is `confirmed`; a name with a part set at runtime
(`shop-${ROLE}-orders`) that fits one application is `inferred`. The call's own edge stays, so
if the target goes away the call is still there and the link is removed.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

from neo4j import Driver

from steno.claims.types import route_target
from steno.extraction.facts import split_url

LINKED_BY = "service_name"
PLACEHOLDER = re.compile(r"\$?\{[^{}]*\}")
MIN_FIXED = 3  # letters a name with a runtime part must fix, or it could fit anything


@dataclass(frozen=True)
class Call:
    source: str
    system: str
    method: str
    url: str


@dataclass(frozen=True)
class Endpoint:
    id: str
    application: str  # its Steno ID
    method: str
    path: str


@dataclass(frozen=True)
class Link:
    source: str
    endpoint: str
    method: str
    url: str
    certainty: str


def application_of(host: str, names: dict[str, set[str]]) -> tuple[str, str] | None:
    """The application a host names and how sure: `(steno_id, certainty)`. None when the host
    names no application, or the name fits several: never guessed."""
    name = route_target(host)
    if not name:
        return None
    if not PLACEHOLDER.search(name):
        found = names.get(name, set())
        return (next(iter(found)), "confirmed") if len(found) == 1 else None
    parts = PLACEHOLDER.split(name)
    if sum(len(p) for p in parts) < MIN_FIXED:
        return None
    pattern = re.compile(".+".join(re.escape(p) for p in parts) + r"\Z")
    fits = {sid for known, ids in names.items() if pattern.match(known) for sid in ids}
    return (next(iter(fits)), "inferred") if len(fits) == 1 else None


def _shape(path: str) -> str:
    return PLACEHOLDER.sub("{}", path.split("?", 1)[0]).rstrip("/") or "/"


def _serves(endpoint: str, called: str) -> bool:
    """Whether a path template serves what a call asks for, whatever each names its parameters."""
    pattern = re.escape(_shape(endpoint)).replace(r"\{\}", r"[^/]+")
    return _shape(endpoint) == _shape(called) or re.fullmatch(pattern, _shape(called)) is not None


def endpoint_for(method: str, path: str, endpoints: list[Endpoint]) -> Endpoint | None:
    """The application's endpoint for a call: the exact one, else the only template that fits."""
    same = [e for e in endpoints if e.method == method.upper()]
    exact = [e for e in same if _shape(e.path) == _shape(path)]
    if len(exact) == 1:
        return exact[0]
    fits = [e for e in same if _serves(e.path, path)]
    return fits[0] if len(fits) == 1 else None


def plan(
    calls: list[Call], names: dict[str, set[str]], endpoints: list[Endpoint]
) -> tuple[list[Link], dict[str, str]]:
    """The links these calls make, and each system that names an application (ID → Steno ID),
    so a call whose path that application doesn't serve can say which service it was for."""
    by_application: dict[str, list[Endpoint]] = {}
    for e in endpoints:
        by_application.setdefault(e.application, []).append(e)
    links, systems = [], {}
    for call in calls:
        host, path = split_url(call.url)
        target = application_of(host, names) if host else None
        if target is None:
            continue
        steno_id, certainty = target
        systems[call.system] = steno_id
        endpoint = endpoint_for(call.method, path, by_application.get(steno_id, []))
        if endpoint is not None:
            links.append(Link(call.source, endpoint.id, call.method, call.url, certainty))
    return links, systems


def link_services(driver: Driver, database: str, names: dict[str, set[str]], owner: str) -> int:
    """Rewrite the links to match the graph as it is now: either side may have just changed, in
    whichever order the repositories were ingested."""

    def read(query: str) -> list[Any]:
        with driver.session(database=database) as s:
            return list(s.run(query))

    calls = [
        Call(r["source"], r["system"], r["method"] or "GET", r["url"])
        for r in read(
            "MATCH (s)-[c:CALLS]->(e:ExternalSystem) WHERE c.url IS NOT NULL "
            "RETURN s.id AS source, e.id AS system, c.method AS method, c.url AS url"
        )
    ]
    endpoints = [
        Endpoint(r["id"], r["application"], r["method"], r["path"])
        for r in read(
            "MATCH (a:Application)-[:EXPOSES]->(i:HttpEndpoint) WHERE a.steno_id IS NOT NULL "
            "RETURN i.id AS id, a.steno_id AS application, i.method AS method, i.path AS path"
        )
    ]
    links, systems = plan(calls, names, endpoints)
    with driver.session(database=database) as s:
        s.run(
            "UNWIND $rows AS row MATCH (a {id: row.source}), (b {id: row.endpoint}) "
            "MERGE (a)-[l:CALLS]->(b) SET l.repo = $owner, l.linked_by = $by, "
            "l.method = row.method, l.url = row.url, l.certainty = row.certainty",
            rows=[asdict(link) for link in links],
            owner=owner,
            by=LINKED_BY,
        ).consume()
        s.run(
            "MATCH (a)-[l:CALLS {linked_by: $by}]->(b) WHERE NOT [a.id, b.id] IN $keep DELETE l",
            by=LINKED_BY,
            keep=[[link.source, link.endpoint] for link in links],
        ).consume()
        s.run(
            "UNWIND $rows AS row MATCH (e:ExternalSystem {id: row.id}) "
            "SET e.names_application = row.application",
            rows=[{"id": k, "application": v} for k, v in systems.items()],
        ).consume()
    return len(links)
