"""Reconcile: line up every claim and code fact about the subjects a job touched, and write the
result into the graph (docs/how-it-works/ingestion/claims-and-reconcile.md §7).

Runs after write in every job, under one lock. Code facts are in the graph and claims are in
Postgres, so jobs can run in any order and the result converges: a platform repository's job
reconciles its topics with whatever code is in the graph; an application's job reconciles its
topics with whatever claims are stored.

What it writes is owned by `reconcile`, not by any repository, so no repository's write stage
reads or removes it:

- an application-level edge, `(:Application)-[:PRODUCES|CONSUMES]->(channel)`, per application
  and role on a checked channel, with `agreement` and `sources` (one per witness);
- a stub application for a name no application has ("known from XLD, not ingested"), and a
  stub channel a witness declares that no code mentions.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from neo4j import Driver
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steno.claims.store import active
from steno.claims.types import channel_key
from steno.db.models import Claim, Repository, UnmatchedName, UnmatchedStatus
from steno.identity.registry import resolve
from steno.reconcile.compare import ROLES, Result, Source, Use, compare

log = logging.getLogger(__name__)

OWNER = "reconcile"  # the `repo` of everything reconcile writes
LOCK = 0x5E7E_0002  # one reconcile at a time (pg_advisory_xact_lock)
CHANNEL_LABELS = ("KafkaTopic", "Queue")
EDGE = {"produces": "PRODUCES", "consumes": "CONSUMES"}


@dataclass
class Summary:
    channels: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    stubs: int = 0

    def counts(self) -> dict[str, Any]:
        by: dict[str, int] = {}
        for r in self.results:
            by[r["agreement"]] = by.get(r["agreement"], 0) + 1
        return {
            "channels": self.channels,
            "agreement": by,
            "unmatched_names": sorted({u["name"] for u in self.unmatched}),
            "stub_applications": self.stubs,
        }


def reconcile(
    session: Session,
    driver: Driver,
    database: str,
    touched: set[str],
    applications: dict[str, set[str]],
) -> Summary:
    """`touched`: the subjects of claims this job added or removed. `applications`: the job's
    applications (node ID → every name they go by), whose channels and names are touched too."""
    session.execute(select(func.pg_advisory_xact_lock(LOCK)))
    run = _Run(session, driver, database)
    subjects = set(touched)
    for names in applications.values():
        subjects |= {f"application:{n}" for n in names}
    subjects |= {f"channel:{c}" for c in run.channels_of(list(applications))}
    # Claims about these applications' names reach the channels they grant
    for claim in active(session, {s for s in subjects if s.startswith("application:")}):
        subjects |= {s for s in claim.subjects if s.startswith("channel:")}
    summary = Summary()
    for channel in sorted(s.removeprefix("channel:") for s in subjects if s.startswith("channel:")):
        summary.results += run.channel(channel)
        summary.channels += 1
    run.deployables(
        {s.removeprefix("application:") for s in subjects if s.startswith("application:")}
    )
    summary.stubs = run.tidy_stubs()
    summary.unmatched = run.unmatched
    return summary


class _Run:
    def __init__(self, session: Session, driver: Driver, database: str):
        self.s, self.driver, self.database = session, driver, database
        self.repos = {r.id: r.name for r in session.scalars(select(Repository))}
        self.unmatched: list[dict[str, Any]] = []

    def _read(self, query: str, **params: Any) -> list[Any]:
        with self.driver.session(database=self.database) as s:
            return list(s.run(query, **params))

    def _write(self, query: str, **params: Any) -> None:
        with self.driver.session(database=self.database) as s:
            s.run(query, **params).consume()

    # ------------------------------------------------------------- reading

    def channels_of(self, apps: list[str]) -> set[str]:
        """Channels the applications' flows produce to or are started by, plus channels
        reconcile already linked them to (so a use that disappeared is recomputed)."""
        if not apps:
            return set()
        rows = self._read(
            "MATCH (a:Application) WHERE a.id IN $apps "
            "CALL (a) { "
            "  MATCH (a)<-[:BELONGS_TO]-(:Flow)<-[:STARTS]-(c:Interface) RETURN c "
            "  UNION MATCH (a)<-[:BELONGS_TO]-(:Flow)<-[:BELONGS_TO]-(:Step)"
            "-[:PRODUCES|CONSUMES]->(c:Interface) RETURN c "
            "  UNION MATCH (a)-[r]->(c:Interface) WHERE r.repo = $owner RETURN c "
            "} "
            "WITH c WHERE NOT c:HttpEndpoint AND NOT c:GrpcMethod "
            "RETURN DISTINCT c.id AS id",
            apps=apps,
            owner=OWNER,
        )
        return {r["id"] for r in rows}

    def code_uses(self, channel: str) -> list[Use]:
        """What the code shows: steps that produce to it, flows it starts, steps that poll it."""
        rows = self._read(
            "MATCH (c:Interface {id: $id}) "
            "CALL (c) { "
            "  MATCH (s:Step)-[r:PRODUCES]->(c) "
            "  MATCH (s)-[:BELONGS_TO]->(:Flow)-[:BELONGS_TO]->(a:Application) "
            "  RETURN a.id AS app, 'produces' AS role, r AS r "
            "  UNION MATCH (c)-[r:STARTS]->(:Flow)-[:BELONGS_TO]->(a:Application) "
            "  RETURN a.id AS app, 'consumes' AS role, r AS r "
            "  UNION MATCH (s:Step)-[r:CONSUMES]->(c) "
            "  MATCH (s)-[:BELONGS_TO]->(:Flow)-[:BELONGS_TO]->(a:Application) "
            "  RETURN a.id AS app, 'consumes' AS role, r AS r "
            "} "
            "RETURN app, role, r.extracted_by AS rule, r.source_file AS file, "
            "r.source_line AS line, r.repo AS repo, r.commit AS commit",
            id=channel,
        )
        return [
            Use(
                r["app"],
                r["role"],
                [Source("CODE", r["rule"] or "code", r["file"], r["line"], r["repo"], r["commit"])],
            )
            for r in rows
        ]

    # ------------------------------------------------------------- channels

    def channel(self, channel: str) -> list[dict[str, Any]]:
        claims = [
            c
            for c in active(self.s, {f"channel:{channel}"})
            if not c.generated and c.claim_type in ("channel", *ROLES)
        ]
        claimed = []
        for c in claims:
            if c.claim_type not in ROLES:
                continue
            app = self.application(c.fields["application"], c)
            claimed.append(Use(app, ROLES[c.claim_type], [self._source(c)]))
        results = compare(channel, self.code_uses(channel), claimed, checked=bool(claims))
        label = _label(claims, channel)
        self._write_channel(channel, label, results)
        names = {
            r["id"]: r["name"]
            for r in self._read(
                "MATCH (a:Application) WHERE a.id IN $ids RETURN a.id AS id, a.name AS name",
                ids=[r.app for r in results],
            )
        }
        return [{**_result_dict(r), "app_name": names.get(r.app, r.app)} for r in results]

    def _write_channel(self, channel: str, label: str | None, results: list[Result]) -> None:
        keep = [[r.app, EDGE[r.role]] for r in results]
        self._write(
            "MATCH (a:Application)-[r:PRODUCES|CONSUMES]->(c:Interface {id: $id}) "
            "WHERE r.repo = $owner AND NOT [a.id, type(r)] IN $keep DELETE r",
            id=channel,
            owner=OWNER,
            keep=keep,
        )
        if not results:
            return
        if label:  # a channel a witness declares and no code mentions: a stub, so edges have it
            self._write(
                f"MERGE (c:Interface {{id: $id}}) ON CREATE SET c:{label}, c:Searchable, "
                "c.name = $name, c.stub = true, c.shared = true, c.repo = $owner, "
                "c.extracted_by = $owner",
                id=channel,
                name=channel.split(":", 1)[1],
                owner=OWNER,
            )
        for role, edge in EDGE.items():
            rows = [
                {
                    "app": r.app,
                    "agreement": r.agreement,
                    "sources": json.dumps([s.as_dict() for s in r.sources]),
                    "witness_types": sorted({s.witness_type for s in r.sources}),
                }
                for r in results
                if r.role == role
            ]
            if not rows:
                continue
            self._write(
                "UNWIND $rows AS row "
                "MATCH (a:Application {id: row.app}) MATCH (c:Interface {id: $id}) "
                f"MERGE (a)-[r:{edge}]->(c) "
                "SET r.agreement = row.agreement, r.sources = row.sources, "
                "r.witness_types = row.witness_types, r.repo = $owner, "
                "r.extracted_by = $owner, r.reconciled = true",
                rows=rows,
                id=channel,
                owner=OWNER,
            )

    # ------------------------------------------------------------- names

    def application(self, name: str, claim: Claim) -> str:
        """The node ID of the application a witness names: resolved through identity, or a stub
        that says the name matched nothing."""
        steno_id = resolve(self.s, name)
        if steno_id is not None:
            self._mapped(name, steno_id)
            return f"app:{steno_id}"
        return self._stub_application(name, claim)

    def deployables(self, names: set[str]) -> None:
        """A declared deployable no application matches is a stub, so it's visible."""
        if not names:
            return
        for c in active(self.s, {f"application:{n}" for n in names}):
            if c.claim_type == "deployable" and not c.generated:
                self.application(c.fields["application"], c)

    def _stub_application(self, name: str, claim: Claim) -> str:
        node = f"app:declared:{name}"
        source = self._source(claim).as_dict()
        self._write(
            "MERGE (a:Application {id: $id}) "
            "SET a:Searchable, a.name = $name, a.stub = true, a.declared_only = true, "
            "a.repo = $owner, a.extracted_by = $owner, "
            "a.note = 'known from ' + $witness + ', not ingested or not matched'",
            id=node,
            name=name,
            owner=OWNER,
            witness=claim.witness,
        )
        row = self.s.scalar(select(UnmatchedName).where(UnmatchedName.name == name))
        if row is None:
            row = UnmatchedName(name=name, sources=[])
            self.s.add(row)
        if source not in row.sources:
            row.sources = [*row.sources, source]
        row.last_seen = func.now()
        if row.status == UnmatchedStatus.MAPPED:
            row.status, row.mapped_to = UnmatchedStatus.OPEN, None
        self.unmatched.append({"name": name, **source})
        return node

    def _mapped(self, name: str, steno_id: str) -> None:
        row = self.s.scalar(select(UnmatchedName).where(UnmatchedName.name == name))
        if row is not None and row.status == UnmatchedStatus.OPEN:
            row.status, row.mapped_to = UnmatchedStatus.MAPPED, steno_id

    def tidy_stubs(self) -> int:
        """Remove stub applications whose name now resolves, or that nothing refers to any
        more (no edges, no deployable claim); return how many remain."""
        stubs = self._read(
            "MATCH (a:Application) WHERE a.repo = $owner AND a.stub "
            "RETURN a.id AS id, a.name AS name, COUNT { (a)--() } AS edges",
            owner=OWNER,
        )
        remove = []
        for s in stubs:
            declared = any(
                c.claim_type == "deployable" for c in active(self.s, {f"application:{s['name']}"})
            )
            if resolve(self.s, s["name"]) is not None or (not s["edges"] and not declared):
                remove.append(s["id"])
        if remove:
            self._write("MATCH (a:Application) WHERE a.id IN $ids DETACH DELETE a", ids=remove)
        self._write(
            "MATCH (c:Interface) WHERE c.repo = $owner AND c.stub AND NOT (c)--() DELETE c",
            owner=OWNER,
        )
        return len(stubs) - len(remove)

    def _source(self, claim: Claim) -> Source:
        return Source(
            claim.witness_type,
            claim.witness,
            claim.file,
            claim.line,
            self.repos.get(claim.repository_id) if claim.repository_id else None,
            claim.commit,
        )


def _label(claims: list[Claim], channel: str) -> str | None:
    """The channel's label (`KafkaTopic`), from the claims that name it."""
    for c in claims:
        ref = c.fields.get("channel")
        if isinstance(ref, dict) and len(ref) == 1:
            (label,) = ref
            if channel_key(ref) == channel and str(label).isidentifier():
                return str(label)
    return None


def _result_dict(r: Result) -> dict[str, Any]:
    return {
        "app": r.app,
        "role": r.role,
        "channel": r.channel,
        "agreement": r.agreement,
        "sources": [s.as_dict() for s in r.sources],
    }
