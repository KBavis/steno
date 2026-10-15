"""The identity registry in Postgres (D81; docs/how-it-works/storage.md): the Registry that
`identify` reads and writes during a job. Other witnesses' names come from the claim store."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from steno.db.models import (
    ApplicationAlias,
    ApplicationIdentity,
    ApplicationLink,
    Claim,
    IdentityState,
    LinkKind,
)
from steno.identity.identify import Record, new_steno_id
from steno.identity.names import Name

# Who decides a display name, in order (docs/concepts/witnesses-and-facets.md §5)
DISPLAY_ORDER = ("ADMIN", "DECLARED", "PLATFORM")


class SqlRegistry:
    def __init__(self, session: Session, run_claims: list[Any] | None = None):
        """`run_claims`: this run's claims (ClaimFacts), not stored yet, so a repository's own
        manifest names it in the same run that reads it."""
        self.s = session
        self.run_claims = [c for c in run_claims or [] if c.witness_type != "CODE"]

    # ---------------------------------------------------------------- reading

    def by_location(self, repository_id: int, module_path: str) -> Record | None:
        row = self.s.scalar(
            select(ApplicationIdentity).where(
                ApplicationIdentity.repository_id == repository_id,
                ApplicationIdentity.module_path == module_path,
            )
        )
        return _record(row) if row else None

    def by_canonical(self, name: str) -> list[Record]:
        rows = self.s.scalars(
            select(ApplicationIdentity).where(ApplicationIdentity.canonical_name == name)
        )
        return [_record(r) for r in rows]

    def get(self, steno_id: str) -> Record | None:
        row = self.s.get(ApplicationIdentity, steno_id)
        return _record(row) if row else None

    def active_in(self, repository_id: int) -> list[Record]:
        rows = self.s.scalars(
            select(ApplicationIdentity).where(
                ApplicationIdentity.repository_id == repository_id,
                ApplicationIdentity.state == IdentityState.ACTIVE,
            )
        )
        return [_record(r) for r in rows]

    def pending_rename(self, names: set[str]) -> tuple[int, str] | None:
        link = self.s.scalar(
            select(ApplicationLink).where(
                ApplicationLink.kind == LinkKind.RENAME,
                ApplicationLink.pending_name.in_(names),
                ApplicationLink.applied_job.is_(None),
                ApplicationLink.from_steno_id.is_not(None),
            )
        )
        return (link.id, link.from_steno_id) if link and link.from_steno_id else None

    def linked_names(self, names: set[str]) -> list[tuple[str, Name]]:
        """Other witnesses' `name` claims about any of these names (not CODE: this repository's
        own names come from this run, not from stored claims)."""
        if not names:
            return []
        rows = self.s.scalars(
            select(Claim).where(
                Claim.claim_type == "name",
                Claim.removed_job.is_(None),
                Claim.witness_type != "CODE",
                Claim.subjects.overlap([f"application:{n}" for n in names]),
            )
        )
        out = [
            (
                c.fields["application"],
                Name(
                    c.fields["name"],
                    c.fields["slot"],
                    c.witness_type,
                    {"witness": c.witness, "file": c.file, "line": c.line},
                ),
            )
            for c in rows
        ]
        for c in self.run_claims:
            if c.claim_type == "name" and {c.fields["application"], c.fields["name"]} & names:
                source = {"witness": c.witness, "file": c.origin.file, "line": c.origin.line}
                pair = (
                    c.fields["application"],
                    Name(c.fields["name"], c.fields["slot"], c.witness_type, source),
                )
                if pair not in out:
                    out.append(pair)
        return out

    def declared_display(self, names: set[str]) -> str | None:
        if not names:
            return None
        rows = list(
            self.s.scalars(
                select(Claim).where(
                    Claim.claim_type == "deployable",
                    Claim.removed_job.is_(None),
                    Claim.subjects.overlap([f"application:{n}" for n in names]),
                )
            )
        )
        found = [
            (r.witness_type, r.fields["display_name"]) for r in rows if r.fields.get("display_name")
        ]
        found += [
            (c.witness_type, c.fields["display_name"])
            for c in self.run_claims
            if c.claim_type == "deployable"
            and c.fields.get("display_name")
            and c.fields["application"] in names
        ]
        found.sort(key=lambda f: DISPLAY_ORDER.index(f[0]) if f[0] in DISPLAY_ORDER else 9)
        return found[0][1] if found else None

    # ---------------------------------------------------------------- writing

    def apply_link(self, link_id: int, job_id: int | None) -> None:
        self.s.execute(
            update(ApplicationLink).where(ApplicationLink.id == link_id).values(applied_job=job_id)
        )

    def save(self, record: Record, job_id: int | None) -> None:
        row = self.s.get(ApplicationIdentity, record.steno_id)
        if row is None:
            row = ApplicationIdentity(steno_id=record.steno_id, first_seen_job=job_id)
            self.s.add(row)
        row.canonical_name = record.canonical_name
        row.display_name = record.display_name
        row.repository_id = record.repository_id
        row.module_path = record.module_path
        row.state = IdentityState(record.state)
        row.conflict_with = record.conflict_with
        row.last_seen_job = job_id
        row.removed_job = None
        self.s.flush()

    def save_names(
        self, steno_id: str, names: list[Name], past: list[str], job_id: int | None
    ) -> None:
        existing = {
            (a.name, a.slot): a
            for a in self.s.scalars(
                select(ApplicationAlias).where(ApplicationAlias.steno_id == steno_id)
            )
        }
        for n in names:
            row = existing.get((n.name, n.slot))
            if row is None:
                row = ApplicationAlias(
                    steno_id=steno_id, name=n.name, slot=n.slot, first_seen_job=job_id
                )
                self.s.add(row)
                existing[(n.name, n.slot)] = row
            row.witness_type = n.witness_type
            row.source = dict(n.source)
            row.is_past_name = False
            row.last_seen_job = job_id
        for (name, _), row in existing.items():
            if name in past:
                row.is_past_name = True
        self.s.flush()

    def remove(self, steno_id: str, job_id: int | None) -> None:
        row = self.s.get_one(ApplicationIdentity, steno_id)
        row.state = IdentityState.REMOVED
        row.removed_job = job_id
        self.s.flush()

    def new_id(self) -> str:
        while True:
            candidate = new_steno_id(set())
            if self.s.get(ApplicationIdentity, candidate) is None:
                return candidate


def _record(row: Any) -> Record:
    return Record(
        row.steno_id,
        row.canonical_name,
        row.display_name,
        row.repository_id,
        row.module_path,
        row.state.value if hasattr(row.state, "value") else str(row.state),
        row.conflict_with,
    )


def resolve(session: Session, name: str) -> str | None:
    """The active application a name means: its canonical name, else an exact alias (a past
    name still means it). None when nothing matches, or when the name is ambiguous (two
    applications have it): never guessed (docs/concepts/identity.md §3)."""
    by_canonical = list(
        session.scalars(
            select(ApplicationIdentity.steno_id).where(
                ApplicationIdentity.canonical_name == name,
                ApplicationIdentity.state == IdentityState.ACTIVE,
            )
        )
    )
    if len(by_canonical) == 1:
        return by_canonical[0]
    if by_canonical:
        return None
    by_alias = set(
        session.scalars(
            select(ApplicationAlias.steno_id)
            .join(ApplicationIdentity, ApplicationIdentity.steno_id == ApplicationAlias.steno_id)
            .where(ApplicationAlias.name == name, ApplicationIdentity.state == IdentityState.ACTIVE)
        )
    )
    return next(iter(by_alias)) if len(by_alias) == 1 else None
