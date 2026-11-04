"""The coverage report: what no rule explained, across the organization.

See docs/how-it-works/ingestion/coverage-report.md.

Read at any scope of the containment tree, like the graph: the organization, a space (its
sub-spaces and applications), or one application. Each scope rolls up its applications'
numbers, lists its children worst first, and groups its items by what was missed (D146: the
catalog's sections, connections between applications first), ranked within each group by
reach (how many applications and spaces one fix would help). Quiet items (unread files, rule
health) are left out unless asked for. One-sided joins are read from the graph. Triage applies
to an item everywhere it appears, and carries over to later runs.
"""

import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from steno.api.deps import SessionDep
from steno.api.links import source_url
from steno.coverage.joins import one_sided
from steno.coverage.patterns import triage as pattern_triage
from steno.coverage.runs import GROUP_OF, GROUPS, SIGNAL_RANK, latest_runs
from steno.db.models import (
    CoverageItem,
    CoverageKind,
    CoveragePattern,
    CoverageStatus,
    IngestionJob,
    IngestionStage,
    PatternState,
    Repository,
    Space,
)
from steno.graph.driver import database, get_driver

log = logging.getLogger(__name__)

router = APIRouter(prefix="/coverage", tags=["coverage"])

UNASSIGNED = "none"  # applications in repositories no space owns
NUMBERS = ("functions", "functions_reachable", "io_calls", "io_calls_explained")


class Triage(BaseModel):
    kind: CoverageKind
    target: str
    status: CoverageStatus
    note: str | None = None


@router.get("")
def report(session: SessionDep, scope: str = "org", quiet: bool = False) -> dict[str, Any]:
    """`scope`: `org`, `space:<id>` (or `space:none` for applications no space owns), or
    `app:<name>`. `quiet`: include what's kept but left out of the report (D146)."""
    latest = latest_runs(session)
    repos = {r.id: r for r in session.scalars(select(Repository))}
    spaces = {s.id: s for s in session.scalars(select(Space))}

    # Every application in a repository's latest run: its numbers, its space, its run
    apps: dict[str, dict[str, Any]] = {}
    for repo_id, (job, stage) in latest.items():
        repo = repos[repo_id]
        metrics = stage.metrics or {}
        per_app = metrics.get("applications") or {repo.name: metrics}
        for name, numbers in per_app.items():
            if name == repo.name and len(per_app) > 1:
                continue  # code outside every application (runs before this was split out)
            apps[name] = {
                "name": name,
                "space": repo.space_id,
                "repository": repo.name,
                "job_id": job.id,
                "commit": job.to_sha,
                "finished_at": stage.finished_at,
                **{k: int(numbers.get(k) or 0) for k in NUMBERS},
            }

    kind, _, key = scope.partition(":")
    wanted: int | None = None
    if kind == "org":
        in_scope = set(apps)
    elif kind == "space":
        wanted = None if key == UNASSIGNED else int(key) if key.isdigit() else -1
        if wanted not in spaces and wanted is not None:
            raise HTTPException(404, f"no space {key!r}")
        in_scope = {a for a, v in apps.items() if _within(v["space"], wanted, spaces)}
    elif kind == "app":
        if key not in apps:
            raise HTTPException(404, f"no application {key!r} in the coverage report")
        in_scope = {key}
    else:
        raise HTTPException(422, "scope is org, space:<id>, or app:<name>")

    def repo_in_scope(repo: Repository) -> bool:
        if kind == "org":
            return True
        if kind == "space":
            return _within(repo.space_id, wanted, spaces)
        return False

    items = _items(session, latest, repos, apps, spaces, in_scope, repo_in_scope, quiet)
    items += _one_sided(session, apps, spaces, in_scope, kind == "org")
    items.sort(key=_rank)
    open_by_app: dict[str, int] = defaultdict(int)
    for it in items:
        if it["status"] == "unexplained":
            for a in it["applications"]:
                open_by_app[a["name"]] += 1

    def rollup(names: set[str]) -> dict[str, Any]:
        sums = {k: sum(apps[n][k] for n in names) for k in NUMBERS}
        open_items = sum(
            1
            for it in items
            if it["status"] == "unexplained" and any(a["name"] in names for a in it["applications"])
        )
        return {
            **sums,
            "applications": len(names),
            "io_explained_pct": _pct(sums["io_calls_explained"], sums["io_calls"]),
            "reachable_pct": _pct(sums["functions_reachable"], sums["functions"]),
            "open_items": open_items,
        }

    children = _children(kind, key, apps, spaces, in_scope)

    def worst(r: dict[str, Any]) -> tuple[bool, float, int]:
        return (r["io_explained_pct"] is None, r["io_explained_pct"] or 0, -r["open_items"])

    rows = []
    for c in children:
        names = c.pop("apps")
        if not names:
            continue
        row = {**c, **rollup(names)}
        if c["kind"] == "space":  # its applications, for the row's dropdown
            row["application_rows"] = sorted(
                (
                    {"scope": f"app:{n}", "kind": "application", "label": n, **rollup({n})}
                    for n in names
                ),
                key=worst,
            )
        rows.append(row)
    rows.sort(key=worst)
    return {
        "scope": scope,
        "breadcrumbs": _breadcrumbs(kind, key, apps, spaces),
        "summary": rollup(in_scope),
        "children": rows,
        "items": items,
        "groups": [
            {"key": key, "label": label, "items": sum(1 for it in items if it["group"] == key)}
            for key, label, _ in GROUPS
        ],
        "runs": sorted(
            (
                {
                    "application": n,
                    "repository": apps[n]["repository"],
                    "job_id": apps[n]["job_id"],
                    "commit": apps[n]["commit"],
                    "finished_at": apps[n]["finished_at"],
                }
                for n in in_scope
            ),
            key=lambda r: r["application"],
        ),
    }


def _items(
    session: Any,
    latest: dict[int, tuple[IngestionJob, IngestionStage]],
    repos: dict[int, Repository],
    apps: dict[str, dict[str, Any]],
    spaces: dict[int, Space],
    in_scope: set[str],
    repo_in_scope: Any,
    quiet: bool = False,
) -> list[dict[str, Any]]:
    """Items in the scope, grouped by what they're about across repositories."""
    jobs = {job.id: repo_id for repo_id, (job, _) in latest.items()}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    statuses: dict[tuple[str, str], list[str]] = defaultdict(list)
    query = select(CoverageItem).where(CoverageItem.job_id.in_(jobs))
    if not quiet:
        query = query.where(CoverageItem.quiet.is_(False))
    for it in session.scalars(query):
        repo = repos[jobs[it.job_id]]
        where = {a: n for a, n in (it.applications or {}).items() if a in apps}
        here = {a: n for a, n in where.items() if a in in_scope}
        # Code outside every application (scripts, tooling): listed at the organization and
        # in its repository's spaces, under no application
        outside = not where and repo_in_scope(repo)
        if not here and not outside:
            continue
        key = (it.kind.value, it.target_symbol)
        g = grouped.setdefault(
            key,
            {
                "kind": it.kind.value,
                "signal": it.signal,
                "group": GROUP_OF.get(it.signal, "ways_out"),
                "quiet": it.quiet,
                "target": it.target_symbol,
                "label": it.label or it.target_symbol,
                "occurrences": 0,
                "applications": [],
                "samples": [],
            },
        )
        g["occurrences"] += sum(here.values()) if here else it.occurrences
        for a, n in sorted(here.items()):
            space = apps[a]["space"]
            g["applications"].append(
                {
                    "name": a,
                    "occurrences": n,
                    "space": spaces[space].name if space in spaces else None,
                }
            )
        job = latest[jobs[it.job_id]][0]
        for s in it.samples:
            if len(g["samples"]) >= 8 or (
                s.get("application") and s["application"] not in in_scope
            ):
                continue
            g["samples"].append(
                {
                    **s,
                    "repository": repo.name,
                    "url": source_url(repo.clone_url, job.to_sha, s.get("file"), s.get("line")),
                }
            )
        statuses[key].append(it.status.value)

    out = []
    for key, g in grouped.items():
        g["status"] = _overall(statuses[key])
        g["spaces"] = len({a["space"] for a in g["applications"]})
        out.append(g)
    return out


def _rank(g: dict[str, Any]) -> tuple[Any, ...]:
    """The catalog's order (connections first), then reach: applications, spaces, occurrences."""
    groups = [key for key, _, _ in GROUPS]
    group = groups.index(g["group"]) if g["group"] in groups else len(groups)
    signal = SIGNAL_RANK.get(g["signal"], len(SIGNAL_RANK))
    return (group, signal, -len(g["applications"]), -g["spaces"], -g["occurrences"], g["label"])


def _one_sided(
    session: Any,
    apps: dict[str, dict[str, Any]],
    spaces: dict[int, Space],
    in_scope: set[str],
    everything: bool,
) -> list[dict[str, Any]]:
    """One-sided joins (D146), read from the graph now: the other end may be in a repository
    ingested since. Shown in a scope when an application in it holds the end that exists. A
    pattern triaged expected or ignored carries its status."""
    try:
        found = one_sided(get_driver(), database())
    except Exception as exc:  # noqa: BLE001 - the graph being down mustn't break the report
        log.warning("one-sided joins not read: %s", exc)
        return []
    states = {
        p.fingerprint: p.state
        for p in session.scalars(
            select(CoveragePattern).where(
                CoveragePattern.fingerprint.in_([f["fingerprint"] for f in found])
            )
        )
    } if found else {}  # fmt: skip
    status = {PatternState.IGNORED: "ignored", PatternState.EXPECTED: "expected"}
    out = []
    for f in found:
        here = [a for a in f["apps"] if a in in_scope]
        if not here and not everything:
            continue
        rows = [
            {
                "name": a,
                "occurrences": 1,
                "space": spaces[apps[a]["space"]].name if apps.get(a, {}).get("space") in spaces
                else None,
            }
            for a in here
        ]  # fmt: skip
        out.append(
            {
                "kind": f["kind"],
                "signal": f["signal"],
                "group": GROUP_OF["one_sided"],
                "quiet": False,
                "target": f["target"],
                "label": f["label"],
                "occurrences": max(len(here), 1),
                "applications": rows,
                "samples": [{"detail": f["detail"]}],
                "status": status.get(states.get(f["fingerprint"]), "unexplained"),  # type: ignore[arg-type]
                "spaces": len({r["space"] for r in rows}),
            }
        )
    return out


def _children(
    kind: str,
    key: str,
    apps: dict[str, dict[str, Any]],
    spaces: dict[int, Space],
    in_scope: set[str],
) -> list[dict[str, Any]]:
    """The rows under a scope: spaces (with the applications under them) and applications."""
    if kind == "app":
        return []
    parent = None if kind == "org" else (None if key == UNASSIGNED else int(key))
    rows: list[dict[str, Any]] = []
    if kind == "org" or key != UNASSIGNED:
        for s in sorted(spaces.values(), key=lambda s: s.name.lower()):
            if s.parent_id == parent:
                under = {a for a in in_scope if _within(apps[a]["space"], s.id, spaces)}
                rows.append(
                    {"scope": f"space:{s.id}", "kind": "space", "label": s.name, "apps": under}
                )
    if kind == "org":
        unassigned = {a for a in in_scope if apps[a]["space"] not in spaces}
        rows.append(
            {
                "scope": f"space:{UNASSIGNED}",
                "kind": "space",
                "label": "No space",
                "apps": unassigned,
            }
        )
    else:
        for a in sorted(in_scope):
            direct = apps[a]["space"] == parent or (
                key == UNASSIGNED and apps[a]["space"] not in spaces
            )
            if direct:
                rows.append({"scope": f"app:{a}", "kind": "application", "label": a, "apps": {a}})
    return rows


def _within(space: int | None, wanted: int | None, spaces: dict[int, Space]) -> bool:
    """Whether an application in `space` is under `wanted` (None: owned by no space)."""
    if wanted is None:
        return space not in spaces
    seen = 0
    while space is not None and space in spaces and seen < 64:
        if space == wanted:
            return True
        space, seen = spaces[space].parent_id, seen + 1
    return False


def _breadcrumbs(
    kind: str, key: str, apps: dict[str, dict[str, Any]], spaces: dict[int, Space]
) -> list[dict[str, str]]:
    crumbs = [{"scope": "org", "label": "Organization"}]
    space: int | None = None
    if kind == "space":
        if key == UNASSIGNED:
            return [*crumbs, {"scope": f"space:{UNASSIGNED}", "label": "No space"}]
        space = int(key)
    elif kind == "app":
        space = apps[key]["space"]
    chain = []
    while space is not None and space in spaces and len(chain) < 64:
        chain.append({"scope": f"space:{space}", "label": spaces[space].name})
        space = spaces[space].parent_id
    crumbs += list(reversed(chain))
    if kind == "app":
        crumbs.append({"scope": f"app:{key}", "label": key})
    return crumbs


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


@router.put("/triage")
def triage(body: Triage, session: SessionDep) -> dict[str, int]:
    """Set an item's status in every repository's latest run; later runs carry it over. A
    one-sided join (read from the graph, no items) is triaged through its pattern."""
    if body.kind == CoverageKind.INCONSISTENCY and not body.target.startswith(
        ("code_only|", "declared_only|")
    ):
        state = {
            CoverageStatus.IGNORED: PatternState.IGNORED,
            CoverageStatus.EXPECTED: PatternState.EXPECTED,
        }.get(body.status, PatternState.OPEN)
        return {"updated": pattern_triage(session, [f"one_sided:{body.target}"], state, body.note)}
    jobs = [job.id for job, _ in latest_runs(session).values()]
    rows = list(
        session.scalars(
            select(CoverageItem).where(
                CoverageItem.job_id.in_(jobs),
                CoverageItem.kind == body.kind,
                CoverageItem.target_symbol == body.target,
            )
        )
    )
    if not rows:
        raise HTTPException(404, f"no coverage item {body.kind.value} {body.target!r}")
    for row in rows:
        row.status = body.status
    # The pattern follows (D121): ignored here is ignored everywhere; reopened is open again
    fingerprints = {f"{row.signal}:{row.target_symbol}" for row in rows}
    for pattern in session.scalars(
        select(CoveragePattern).where(CoveragePattern.fingerprint.in_(fingerprints))
    ):
        if body.status == CoverageStatus.IGNORED:
            pattern.state = PatternState.IGNORED
            pattern.note = body.note or "ignored in the coverage report"
        elif body.status == CoverageStatus.EXPECTED:
            pattern.state = PatternState.EXPECTED
            pattern.note = body.note or "expected: outside the organization"
        elif pattern.state in (PatternState.IGNORED, PatternState.EXPECTED):
            pattern.state, pattern.note = PatternState.OPEN, "reopened in the coverage report"
    return {"updated": len(rows)}


def _overall(statuses: list[str]) -> str:
    """One status across repositories: open anywhere means open."""
    if "unexplained" in statuses:
        return "unexplained"
    if all(s == "explained" for s in statuses):
        return "explained"
    if all(s in ("expected", "explained") for s in statuses):
        return "expected"
    return "ignored"
