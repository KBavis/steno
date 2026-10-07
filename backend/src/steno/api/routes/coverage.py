"""The coverage report (docs/ingestion.md §4): what no rule explained, across the organization.

Read at any scope of the containment tree, like the graph: the organization, a space (its
sub-spaces and applications), or one application. Each scope rolls up its applications'
numbers, lists its children worst first, and ranks its items by reach (how many applications
and spaces one fix would help), so one rule's value is visible (D62). Triage applies to an
item everywhere it appears, and carries over to later runs.
"""

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from steno.api.deps import SessionDep
from steno.api.links import source_url
from steno.db.models import (
    CoverageItem,
    CoverageKind,
    CoverageStatus,
    IngestionJob,
    IngestionStage,
    Repository,
    Space,
    StageName,
    StageStatus,
)

router = APIRouter(prefix="/coverage", tags=["coverage"])

UNASSIGNED = "none"  # applications in repositories no space owns
NUMBERS = ("functions", "functions_reachable", "io_calls", "io_calls_explained")


class Triage(BaseModel):
    kind: CoverageKind
    target: str
    status: CoverageStatus


@router.get("")
def report(session: SessionDep, scope: str = "org") -> dict[str, Any]:
    """`scope`: `org`, `space:<id>` (or `space:none` for applications no space owns), or
    `app:<name>`."""
    latest = _latest_runs(session)
    repos = {r.id: r for r in session.scalars(select(Repository))}
    spaces = {s.id: s for s in session.scalars(select(Space))}

    # Every application in a repository's latest run: its numbers, its space, its run
    apps: dict[str, dict[str, Any]] = {}
    for repo_id, (job, stage) in latest.items():
        repo = repos[repo_id]
        metrics = stage.metrics or {}
        per_app = metrics.get("applications") or {repo.name: metrics}
        for name, numbers in per_app.items():
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

    items = _items(session, latest, repos, apps, spaces, in_scope)
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
    rows = [{**c, **rollup(c.pop("apps"))} for c in children if c["apps"]]
    rows.sort(
        key=lambda r: (r["io_explained_pct"] is None, r["io_explained_pct"] or 0, -r["open_items"])
    )
    return {
        "scope": scope,
        "breadcrumbs": _breadcrumbs(kind, key, apps, spaces),
        "summary": rollup(in_scope),
        "children": rows,
        "items": items,
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
) -> list[dict[str, Any]]:
    """Items in the scope, grouped by what they're about across repositories, ranked by reach:
    applications, then spaces, then occurrences."""
    jobs = {job.id: repo_id for repo_id, (job, _) in latest.items()}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    statuses: dict[tuple[str, str], list[str]] = defaultdict(list)
    for it in session.scalars(select(CoverageItem).where(CoverageItem.job_id.in_(jobs))):
        repo = repos[jobs[it.job_id]]
        where = it.applications or {repo.name: it.occurrences}
        here = {a: n for a, n in where.items() if a in in_scope}
        if not here:
            continue
        key = (it.kind.value, it.target_symbol)
        g = grouped.setdefault(
            key,
            {
                "kind": it.kind.value,
                "signal": it.signal,
                "target": it.target_symbol,
                "label": it.label or it.target_symbol,
                "occurrences": 0,
                "applications": [],
                "samples": [],
            },
        )
        g["occurrences"] += sum(here.values())
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
    out.sort(key=lambda g: (-len(g["applications"]), -g["spaces"], -g["occurrences"], g["label"]))
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
    """Set an item's status in every repository's latest run; later runs carry it over."""
    jobs = [job.id for job, _ in _latest_runs(session).values()]
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
    return {"updated": len(rows)}


def _latest_runs(session: Any) -> dict[int, tuple[IngestionJob, IngestionStage]]:
    """Each repository's latest job whose coverage stage succeeded, with that stage."""
    last = (
        select(IngestionJob.repository_id, func.max(IngestionJob.id).label("job_id"))
        .join(IngestionStage, IngestionStage.job_id == IngestionJob.id)
        .where(
            IngestionStage.stage == StageName.COVERAGE,
            IngestionStage.status == StageStatus.SUCCEEDED,
        )
        .group_by(IngestionJob.repository_id)
        .subquery()
    )
    rows = session.execute(
        select(IngestionJob, IngestionStage)
        .join(last, last.c.job_id == IngestionJob.id)
        .join(IngestionStage, IngestionStage.job_id == IngestionJob.id)
        .where(IngestionStage.stage == StageName.COVERAGE)
    ).all()
    return {job.repository_id: (job, stage) for job, stage in rows}


def _overall(statuses: list[str]) -> str:
    """One status across repositories: open anywhere means open."""
    if "unexplained" in statuses:
        return "unexplained"
    if all(s == "explained" for s in statuses):
        return "explained"
    return "ignored"
