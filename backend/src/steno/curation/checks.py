"""Checks a drafted rule passes before a person sees it (rule-curation §6). Its own tests prove
it on the samples the drafter chose; these run it where its pattern really occurs:

- **Recall:** every file the pattern occurs in (up to `MAX_FILES`), read from the git host:
  which the rule explains, and which it misses, so a blind spot is visible ("explains 3 of 5").
- **Identity:** the application names its claims would create. A generic name (`backend`,
  `api`) or one another repository's application already has would merge or split
  applications across the organization, so it's a problem, not a detail.

A rule with a problem goes back to the drafter once with what was found; if it still has one,
it reaches the reviewer flagged.
"""

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.coverage.runs import latest_runs
from steno.db.models import (
    ApplicationAlias,
    ApplicationIdentity,
    CoverageOccurrence,
    CoveragePattern,
    Repository,
)
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import Pack, load_all_packs, load_pack

log = logging.getLogger(__name__)

MAX_FILES = 20
# Names too common to identify one application across an organization
GENERIC_NAMES = {
    "api", "app", "application", "backend", "client", "common", "core", "db", "database",
    "default", "dev", "frontend", "gateway", "local", "main", "nginx", "postgres", "prod",
    "proxy", "redis", "server", "service", "test", "ui", "web", "worker", "kafka", "mysql",
    "zookeeper", "mongo", "rabbitmq", "elasticsearch", "cache", "admin", "auth",
}  # fmt: skip


@dataclass
class RuleCheck:
    files: int = 0  # files the pattern occurs in that were checked
    explained: list[str] = field(default_factory=list)  # "repository/path"
    missed: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # application names its claims create
    generic: list[str] = field(default_factory=list)
    taken: list[dict[str, str]] = field(default_factory=list)  # {name, by, repository}
    errors: list[str] = field(default_factory=list)

    def problems(self) -> list[str]:
        """What a reviewer must know, each in one line; empty when the rule is clean."""
        out = []
        if self.generic:
            out.append(
                f"identity: names too generic to identify one application: "
                f"{', '.join(self.generic)}"
            )
        for t in self.taken:
            out.append(f"identity: '{t['name']}' is already {t['by']} (in {t['repository']})")
        if self.missed:
            out.append(
                f"recall: explains {len(self.explained)} of {self.files} files where the "
                f"pattern occurs; misses {', '.join(self.missed[:5])}"
            )
        return out

    def blocking(self) -> bool:
        """Problems a reviewer shouldn't be asked to approve: identity ones."""
        return bool(self.generic or self.taken)

    def to_json(self) -> dict[str, Any]:
        return {
            "files": self.files, "explained": self.explained, "missed": self.missed,
            "names": self.names, "generic": self.generic, "taken": self.taken,
            "errors": self.errors,
        }  # fmt: skip


def check_rule(
    session: Session,
    pattern: CoveragePattern,
    rule: str,
    pack: dict[str, Any] | None,
    read: Callable[[str, str], str],
    packs: list[Pack] | None = None,
) -> RuleCheck:
    """Run a drafted rule over every file its pattern occurs in, and check what it would
    create (module docstring)."""
    out = RuleCheck()
    files: dict[str, str] = {}
    repo_of: dict[str, int] = {}
    for repo_id, repo_name, path in _occurrence_files(session, pattern):
        try:
            files[f"{repo_name}/{path}"] = read(repo_name, path)
            repo_of[f"{repo_name}/{path}"] = repo_id
        except Exception as exc:  # noqa: BLE001 - a file we can't read is noted, not fatal
            out.errors.append(f"can't read {repo_name}/{path}: {exc}")
    out.files = len(files)
    if not files:
        return out
    try:
        x = run_draft(rule, pack, files, packs)
    except Exception as exc:  # noqa: BLE001 - the draft's own tests passed; report, don't fail
        out.errors.append(f"the rule didn't run over its pattern's files: {exc}")
        return out

    matched = {o.file for o in _origins(x)}
    out.explained = sorted(f for f in files if f in matched)
    out.missed = sorted(f for f in files if f not in matched)

    # Identity: the application names its claims would create, by the file they came from
    names: dict[str, set[int]] = {}
    for c in x.claims:
        if c.origin.pack != DRAFT:
            continue
        for key in ("application", "name"):
            value = c.fields.get(key)
            if isinstance(value, str) and value and not value.startswith("<"):
                names.setdefault(value, set()).add(repo_of.get(c.origin.file, -1))
    out.names = sorted(names)
    out.generic = sorted(n for n in names if n.lower() in GENERIC_NAMES)
    out.taken = _taken(session, names)
    return out


DRAFT = "steno-draft-check"


def run_draft(
    rule: str, pack: dict[str, Any] | None, files: dict[str, str], packs: list[Pack] | None
) -> Any:
    """Run a draft rule (with the other packs beside it, for the clues they emit) over files
    written to a scratch folder like a small repository. The draft is always enabled: whether
    its pack would turn on is a separate question."""
    parsed = yaml.safe_load(rule)
    meta = {"version": "0.0.0", "language": "java", "source": "org", **(pack or {}),
            "name": DRAFT, "enabled_when": {"always": True}}  # fmt: skip
    meta.pop("folder", None)
    with tempfile.TemporaryDirectory(prefix="steno-check-") as tmp:
        root = Path(tmp) / DRAFT
        (root / "rules").mkdir(parents=True)
        (root / "pack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
        (root / "rules" / f"{parsed['id']}.yaml").write_text(yaml.safe_dump(parsed))
        draft = load_pack(root)
        repo = Path(tmp) / "repo"
        for rel, text in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        others = [p for p in (packs if packs is not None else load_all_packs())]
        return run_folder(repo, [*others, draft]).out


def _origins(x: Any) -> list[Any]:
    every = [
        *(n.origin for n in x.nodes), *(e.origin for e in x.edges),
        *(c.origin for c in x.clues), *(e.origin for e in x.entry_points),
        *(i.origin for i in x.interceptors), *(c.origin for c in x.claims),
    ]  # fmt: skip
    return [o for o in every if o.pack == DRAFT]


def _occurrence_files(session: Session, pattern: CoveragePattern) -> list[tuple[int, str, str]]:
    """The files the pattern occurs in, in each repository's latest run, up to MAX_FILES,
    spread across repositories."""
    current = set(latest_runs(session))
    names = {r.id: r.name for r in session.scalars(select(Repository))}
    by_repo: dict[int, list[str]] = {}
    for o in session.scalars(
        select(CoverageOccurrence).where(CoverageOccurrence.pattern_id == pattern.id)
    ):
        if o.repository_id in current and o.file and o.file not in by_repo.get(o.repository_id, []):
            by_repo.setdefault(o.repository_id, []).append(o.file)
    out: list[tuple[int, str, str]] = []
    while len(out) < MAX_FILES and any(by_repo.values()):
        for repo_id in list(by_repo):  # round-robin, so one large repository doesn't fill it
            if by_repo[repo_id] and len(out) < MAX_FILES:
                out.append((repo_id, names.get(repo_id, "?"), by_repo[repo_id].pop(0)))
    return out


def _taken(session: Session, names: dict[str, set[int]]) -> list[dict[str, str]]:
    """Names an application in another repository already goes by."""
    if not names:
        return []
    repos = {r.id: r.name for r in session.scalars(select(Repository))}
    out = []
    rows = session.execute(
        select(ApplicationAlias.name, ApplicationIdentity.canonical_name,
               ApplicationIdentity.repository_id)
        .join(ApplicationIdentity, ApplicationIdentity.steno_id == ApplicationAlias.steno_id)
        .where(ApplicationAlias.name.in_(names))
    )  # fmt: skip
    seen = set()
    for name, canonical, repo_id in rows:
        if repo_id in names[name] or (name, canonical) in seen:
            continue  # the same repository's own application: no clash
        seen.add((name, canonical))
        by = "the name of" if name == canonical else "an alias of"
        out.append({"name": name, "by": f"{by} {canonical}",
                    "repository": repos.get(repo_id, "?") if repo_id else "?"})  # fmt: skip
    return out
