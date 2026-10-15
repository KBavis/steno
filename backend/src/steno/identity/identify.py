"""Identify: give every deployable a permanent Steno ID and its real name
(docs/concepts/identity.md §2–§6; docs/how-it-works/ingestion/identify-and-reconcile.md §2).

For each deployable structure found in this repository:

1. **Collect its names**: the CODE names from this run (path, build artifact, runtime
   self-name), then every name another witness links to one of them exactly (a PLATFORM deploy
   name, a DECLARED manifest name), from the claim store.
2. **Pick the canonical name** by the name ladder: admin, deploy, manifest, build artifact,
   runtime, path (provisional).
3. **Look it up**: by location (repository + module path) first, so a rename in place keeps its
   ID; then by canonical name (a move, or a removed application coming back); then by an
   admin's pending rename. A canonical name active in another repository is a conflict:
   flagged, never merged. Otherwise, a new Steno ID.
4. **Tombstone** every application of this repository not seen in this run.

Two exact lookups, no fingerprints or fuzzy matching: a wrong match creates a false dependency.
"""

import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from steno.graph.structure import Structure
from steno.identity.names import Name, build_name, path_name

# Ties on the same rung: the more authoritative witness wins
WITNESS_ORDER = ("ADMIN", "PLATFORM", "DECLARED", "CODE")


@dataclass
class Record:
    steno_id: str
    canonical_name: str
    display_name: str
    repository_id: int | None
    module_path: str | None
    state: str = "active"  # active | removed | deprecated
    conflict_with: str | None = None


@dataclass
class Deployable:
    module_path: str
    names: list[Name]  # its CODE names from this run; the path name is always among them


@dataclass
class AppIdentity:
    steno_id: str
    canonical: str
    display: str
    names: list[Name]  # every name it goes by, canonical included
    conflict_with: str | None = None

    @property
    def provisional(self) -> bool:
        """Named only by its folder: nothing states a real name yet."""
        return all(n.slot == "path" for n in self.names if n.name == self.canonical)

    @property
    def aliases(self) -> list[str]:
        return sorted({n.name for n in self.names} - {self.canonical})


@dataclass
class Report:
    new: list[str] = field(default_factory=list)
    renamed: list[dict[str, str]] = field(default_factory=list)
    moved: list[dict[str, str]] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    # Removed X and added Y in one run: maybe one application renamed and moved at once,
    # which neither lookup can catch; an admin can link them
    possible_renames: list[dict[str, str]] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v and k != "removed_ids"}


class Registry(Protocol):
    def by_location(self, repository_id: int, module_path: str) -> Record | None: ...
    def by_canonical(self, name: str) -> list[Record]: ...
    def get(self, steno_id: str) -> Record | None: ...
    def active_in(self, repository_id: int) -> list[Record]: ...
    # Admin renames declared ahead of time: (link id, the Steno ID it continues)
    def pending_rename(self, names: set[str]) -> tuple[int, str] | None: ...
    def apply_link(self, link_id: int, job_id: int | None) -> None: ...
    # Names other witnesses state, linked to any of these names: (application, Name) pairs
    def linked_names(self, names: set[str]) -> list[tuple[str, Name]]: ...
    def declared_display(self, names: set[str]) -> str | None: ...
    def save(self, record: Record, job_id: int | None) -> None: ...
    def save_names(
        self, steno_id: str, names: list[Name], past: list[str], job_id: int | None
    ) -> None: ...
    def remove(self, steno_id: str, job_id: int | None) -> None: ...
    def new_id(self) -> str: ...


def identify(
    registry: Registry,
    repository_id: int,
    deployables: Iterable[Deployable],
    job_id: int | None,
    remove: bool = True,
) -> tuple[dict[str, AppIdentity], Report]:
    """Each deployable's identity, by module path, and what changed. `remove=False` reports
    the applications not seen without tombstoning them, for the caller to do once the run has
    succeeded (a failed run must not remove anything)."""
    report = Report()
    found: dict[str, tuple[Deployable, list[Name], Name]] = {}
    for d in sorted(deployables, key=lambda d: d.module_path):
        names = expand(registry, d.names)
        found[d.module_path] = (d, names, canonical_of(names))

    matched: dict[str, Record] = {}
    claimed: set[str] = set()
    # Pass 1: by location, so a rename in place keeps its ID even if another deployable takes
    # its old name
    for path in found:
        rec = registry.by_location(repository_id, path)
        if rec is not None:
            matched[path] = rec
            claimed.add(rec.steno_id)
    # Pass 2: by canonical name, then by an admin's pending rename
    conflicts: dict[str, str] = {}
    for path, (_, names, canonical) in found.items():
        if path in matched:
            continue
        candidates = [r for r in registry.by_canonical(canonical.name) if r.steno_id not in claimed]
        here = [r for r in candidates if r.repository_id == repository_id]
        gone = [r for r in candidates if r.state == "removed"]
        elsewhere = [
            r for r in candidates if r.state != "removed" and r.repository_id != repository_id
        ]
        rec = (here or gone or [None])[0]
        if rec is None and elsewhere:
            conflicts[path] = elsewhere[0].steno_id
        if rec is None:
            pending = registry.pending_rename({n.name for n in names})
            if pending is not None:
                link_id, steno_id = pending
                rec = registry.get(steno_id)
                if rec is not None:
                    registry.apply_link(link_id, job_id)
        if rec is not None:
            matched[path] = rec
            claimed.add(rec.steno_id)

    out: dict[str, AppIdentity] = {}
    for path, (_, names, canonical) in found.items():
        display = registry.declared_display({n.name for n in names}) or canonical.name
        rec = matched.get(path)
        past: list[str] = []
        if rec is None:
            rec = Record(registry.new_id(), canonical.name, display, repository_id, path)
            report.new.append(canonical.name)
        else:
            if rec.state == "removed":
                report.restored.append(canonical.name)
            if rec.canonical_name != canonical.name:
                report.renamed.append({"from": rec.canonical_name, "to": canonical.name})
                past.append(rec.canonical_name)
            if (rec.repository_id, rec.module_path) != (repository_id, path):
                report.moved.append({"name": canonical.name, "to": path})
        # A canonical name another repository's active application also has: flagged
        conflict = conflicts.get(path) or next(
            (
                r.steno_id
                for r in registry.by_canonical(canonical.name)
                if r.steno_id != rec.steno_id
                and r.state != "removed"
                and r.repository_id != repository_id
            ),
            None,
        )
        if conflict:
            report.conflicts.append({"name": canonical.name, "with": conflict})
        rec.canonical_name, rec.display_name = canonical.name, display
        rec.repository_id, rec.module_path, rec.state = repository_id, path, "active"
        rec.conflict_with = conflict
        registry.save(rec, job_id)
        registry.save_names(rec.steno_id, names, past, job_id)
        out[path] = AppIdentity(rec.steno_id, canonical.name, display, names, conflict)

    seen = {i.steno_id for i in out.values()}
    for r in registry.active_in(repository_id):
        if r.steno_id not in seen:
            if remove:
                registry.remove(r.steno_id, job_id)
            report.removed.append(r.canonical_name)
            report.removed_ids.append(r.steno_id)
    if report.removed and report.new:
        report.possible_renames = [
            {"removed": a, "added": b} for a in report.removed for b in report.new
        ]
    return out, report


def deployables(
    repo: Path, repo_name: str, structure: Structure, claims: list[Any]
) -> list[Deployable]:
    """Every deployable structure found, with the names this run gives it: its path name, the
    build artifact name its build declares, and CODE `name` claims from files inside it
    (`spring.application.name`)."""
    out = []
    for module in structure.modules:
        if "Service" not in module.roles:
            continue
        names = [path_name(repo_name, structure, module)]
        built = build_name(repo, module)
        if built:
            names.append(built)
        for c in claims:
            if c.claim_type != "name" or c.witness_type != "CODE":
                continue
            if structure.module_of(c.origin.file) is not module:
                continue
            source = {"witness": c.witness, "file": c.origin.file, "line": c.origin.line}
            name = Name(c.fields["name"], c.fields["slot"], "CODE", source)
            if name not in names:
                names.append(name)
        out.append(Deployable(module.path, names))
    return out


def expand(registry: Registry, names: list[Name], rounds: int = 4) -> list[Name]:
    """The deployable's names plus every name another witness links to one of them exactly:
    a deploy descriptor naming `ledger-svc-prod` with the artifact `ledger-service`."""
    out = list(names)
    known = {n.name for n in out}
    for _ in range(rounds):
        added = False
        for application, name in registry.linked_names(known):
            for text in (application, name.name):
                if text not in known:
                    known.add(text)
                    added = True
            if name not in out:
                out.append(name)
            if application != name.name:
                alias = Name(application, name.slot, name.witness_type, name.source)
                if alias not in out:
                    out.append(alias)
        if not added:
            break
    return out


def canonical_of(names: list[Name]) -> Name:
    """The name ladder: the highest rung wins; on one rung, the more authoritative witness."""
    return min(
        names,
        key=lambda n: (
            n.rank,
            WITNESS_ORDER.index(n.witness_type) if n.witness_type in WITNESS_ORDER else 9,
            n.name,
        ),
    )


class MemoryRegistry:
    """The registry in memory: for tests, and for runs with no database (`steno extract`)."""

    def __init__(self) -> None:
        self.records: dict[str, Record] = {}
        self.names: dict[str, list[tuple[Name, bool]]] = {}  # steno_id → (name, is_past)
        self.links: list[dict[str, Any]] = []  # {id, from_steno_id, pending_name, applied}
        self.claims: list[tuple[str, Name]] = []  # other witnesses' name claims
        self.displays: dict[str, str] = {}  # name → declared display name

    def by_location(self, repository_id: int, module_path: str) -> Record | None:
        return next(
            (
                r
                for r in self.records.values()
                if (r.repository_id, r.module_path) == (repository_id, module_path)
            ),
            None,
        )

    def by_canonical(self, name: str) -> list[Record]:
        return [r for r in self.records.values() if r.canonical_name == name]

    def get(self, steno_id: str) -> Record | None:
        return self.records.get(steno_id)

    def active_in(self, repository_id: int) -> list[Record]:
        return [
            r
            for r in self.records.values()
            if r.repository_id == repository_id and r.state == "active"
        ]

    def pending_rename(self, names: set[str]) -> tuple[int, str] | None:
        for link in self.links:
            if not link.get("applied") and link["pending_name"] in names:
                return link["id"], link["from_steno_id"]
        return None

    def apply_link(self, link_id: int, job_id: int | None) -> None:
        for link in self.links:
            if link["id"] == link_id:
                link["applied"] = True

    def linked_names(self, names: set[str]) -> list[tuple[str, Name]]:
        return [(a, n) for a, n in self.claims if a in names or n.name in names]

    def declared_display(self, names: set[str]) -> str | None:
        return next((self.displays[n] for n in sorted(names) if n in self.displays), None)

    def save(self, record: Record, job_id: int | None) -> None:
        self.records[record.steno_id] = record

    def save_names(
        self, steno_id: str, names: list[Name], past: list[str], job_id: int | None
    ) -> None:
        kept = [(n, n.name in past) for n, _ in self.names.get(steno_id, []) if n not in names]
        self.names[steno_id] = [(n, False) for n in names] + [
            (n, True) for n, was_past in kept if was_past or n.name in past
        ]

    def remove(self, steno_id: str, job_id: int | None) -> None:
        self.records[steno_id].state = "removed"

    def new_id(self) -> str:
        return new_steno_id(set(self.records))


def new_steno_id(taken: set[str]) -> str:
    """Opaque and permanent: 12 hex characters, never derived from a name."""
    while True:
        candidate = secrets.token_hex(6)
        if candidate not in taken:
            return candidate
