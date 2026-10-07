"""The ingestion pipeline for one repository (docs/ingestion.md §2).

Each stage is timed and recorded in `ingestion_stage`; together those rows are the
dry-run report (time per stage, graph size, projected LLM cost). Stages that aren't
built yet are recorded as skipped, with the reason.
"""

import json
import logging
import shutil
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from steno.assemblers.assemble import assemble as run_assemblers
from steno.config import get_settings
from steno.connectors.git import clone as git_clone
from steno.db.models import (
    ChangeKind,
    Connector,
    FactChange,
    IngestionJob,
    IngestionStage,
    JobMode,
    Organization,
    Repository,
    StageName,
    StageStatus,
)
from steno.extraction.engine import Engine
from steno.extraction.report import to_json
from steno.graph import ids
from steno.graph.build import Context as GraphContext
from steno.graph.build import build as build_graph
from steno.graph.delta import changes
from steno.graph.driver import database, get_driver
from steno.graph.structure import detect as detect_structure
from steno.graph.writer import write_plan
from steno.rule_packs.packs import is_enabled, load_packs

log = logging.getLogger(__name__)


@dataclass
class JobContext:
    job: IngestionJob
    repository: Repository
    connector: Connector
    session: Session
    workspace: Path | None = None
    # Handed from one stage to the next (the rule engine, facts, call graph, ...)
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def dry_run(self) -> bool:
        return self.job.mode == JobMode.DRY_RUN


class Skip(Exception):
    """A stage that doesn't apply, or isn't built yet. The message says why."""


StageFn = Callable[[JobContext], dict[str, Any]]


def clone(ctx: JobContext) -> dict[str, Any]:
    """Clone the default branch into a temporary workspace and record the commit."""
    workspace = get_settings().workspace_dir / f"job-{ctx.job.id}"
    if workspace.exists():
        shutil.rmtree(workspace)
    repo = ctx.repository
    sha = git_clone(
        repo.clone_url,
        repo.default_branch,
        workspace,
        ctx.connector.kind,
        ctx.connector.credentials_ref,
    )
    ctx.workspace = workspace
    ctx.job.from_sha = repo.last_ingested_sha
    ctx.job.to_sha = sha
    files = [p for p in workspace.rglob("*") if p.is_file() and ".git" not in p.parts]
    return {"sha": sha, "files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def deps(ctx: JobContext) -> dict[str, Any]:
    """Fetch dependency JARs for Java symbol resolution."""
    raise Skip("dependency JARs are only needed for Java")


def parse(ctx: JobContext) -> dict[str, Any]:
    """Pick the rule packs that apply, then read the code once: the symbol resolver's symbol table
    and a syntax tree per file. No rules run yet."""
    assert ctx.workspace is not None
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, ctx.workspace)]
    engine = Engine(ctx.workspace, packs)
    engine.parse()
    ctx.state["engine"] = engine
    return {
        "packs": [p.name for p in packs],
        "files": len(engine.files),
        "python_modules": len(engine.resolver.modules) if engine.resolver else 0,
        "errors": len(engine.out.errors),
    }


def extract(ctx: JobContext) -> dict[str, Any]:
    """Run every rule on every file: facts, facts with blanks, and clues."""
    engine: Engine = ctx.state["engine"]
    x = engine.extract()
    return {
        "matched": {"nodes": len(x.nodes), "edges": len(x.edges), "clues": len(x.clues)},
        "errors": len(x.errors),
    }


def assemble(ctx: JobContext) -> dict[str, Any]:
    """Fill in the blanks from the clues: prefix chains, table_of, SDK clients, partial
    identities."""
    engine: Engine = ctx.state["engine"]
    run_assemblers(engine.out, engine.resolver, engine.repo)
    x = engine.out
    # Kept for review alongside the graph: every fact with its source line
    facts_path = get_settings().workspace_dir / f"job-{ctx.job.id}-facts.json"
    facts_path.write_text(json.dumps(to_json(x), indent=2, default=str))
    ctx.job.stats = {**(ctx.job.stats or {}), "facts_file": str(facts_path)}
    return {
        "nodes": dict(Counter(n.label for n in x.nodes)),
        "edges": dict(Counter(e.type for e in x.edges)),
        "clues": dict(Counter(c.kind for c in x.clues)),
        "entry_points": len(x.entry_points),
        "dropped": len(x.dropped),
        "facts_file": str(facts_path),
    }


def flows(ctx: JobContext) -> dict[str, Any]:
    """Build the call graph in memory, walk it from every entry point, and plan the graph:
    architecture nodes, code nodes, each flow's trace and steps, and flow rollups. The call
    graph itself isn't kept (D60)."""
    engine: Engine = ctx.state["engine"]
    x = engine.out
    structure = detect_structure(ctx.workspace, {ep.origin.file for ep in x.entry_points})  # type: ignore[arg-type]
    org = ctx.session.get(Organization, 1)
    plan = build_graph(
        x,
        engine.resolver,
        structure,
        GraphContext(
            repo=ctx.repository.name,
            space_id=ids.space_id(ctx.repository.space_id) if ctx.repository.space_id else None,
            org_id=ids.organization_id(org.id) if org else None,
            job_id=ctx.job.id,
            commit=ctx.job.to_sha,
        ),
        http_systems=[s for p in engine.packs for s in p.http_systems],
    )
    ctx.state["plan"] = plan
    flow_nodes = [n for n in plan.nodes.values() if n.labels[0] == "Flow"]
    return {
        "modules": {m.path: sorted(m.roles) for m in structure.modules},
        "flows": len(flow_nodes),
        "steps": sum(1 for n in plan.nodes.values() if n.labels[0] == "Step"),
        "trace_entries": sum(n.props.get("trace", "").count('"path"') for n in flow_nodes),
        **plan.stats,
        "unmet_joins": len(plan.stats.get("unmet_joins", [])),
        "unmet_join_samples": plan.stats.get("unmet_joins", [])[:20],
    }


def write(ctx: JobContext) -> dict[str, Any]:
    """Write only what changed since this repository's last run (D59): compare the plan with
    the graph by stable ID, delete what's gone, write what's new or changed, and record each
    change in `fact_change` (D18). A dry run writes the graph too: it only skips LLM text (D26).
    """
    plan = ctx.state["plan"]
    delta = write_plan(get_driver(), database(), plan, ctx.repository.name)
    rows = changes(delta)
    ctx.session.add_all(
        FactChange(
            job_id=ctx.job.id,
            fact_id=r["fact_id"],
            fact_type=r["fact_type"],
            change=ChangeKind(r["change"]),
            before=r["before"],
            after=r["after"],
        )
        for r in rows
    )
    ctx.repository.last_ingested_sha = ctx.job.to_sha
    return {**plan.counts(), "delta": delta.counts(), "fact_changes": len(rows)}


def cards(ctx: JobContext) -> dict[str, Any]:
    """Generate LLM purposes and narratives. In a dry run: count tokens and project cost only."""
    raise Skip("not built yet")


STAGES: list[tuple[StageName, StageFn]] = [
    (StageName.CLONE, clone),
    (StageName.DEPS, deps),
    (StageName.PARSE, parse),
    (StageName.EXTRACT, extract),
    (StageName.ASSEMBLE, assemble),
    (StageName.FLOWS, flows),
    (StageName.WRITE, write),
    (StageName.CARDS, cards),
]


def run_job(session: Session, job: IngestionJob) -> None:
    """Run every stage in order, committing each stage's row so progress is visible."""
    repository = session.get_one(Repository, job.repository_id)
    ctx = JobContext(
        job=job,
        repository=repository,
        connector=session.get_one(Connector, repository.connector_id),
        session=session,
    )
    try:
        for name, fn in STAGES:
            _run_stage(session, ctx, name, fn)
    finally:
        if ctx.workspace is not None and ctx.workspace.exists():
            shutil.rmtree(ctx.workspace, ignore_errors=True)


def _run_stage(session: Session, ctx: JobContext, name: StageName, fn: StageFn) -> None:
    stage = IngestionStage(
        job_id=ctx.job.id, stage=name, status=StageStatus.RUNNING, started_at=_now()
    )
    session.add(stage)
    session.commit()

    started = time.perf_counter()
    try:
        metrics = fn(ctx)
        stage.status = StageStatus.SUCCEEDED
    except Skip as reason:
        metrics = {"reason": str(reason)}
        stage.status = StageStatus.SKIPPED
    except Exception as exc:
        stage.status = StageStatus.FAILED
        stage.metrics = {"error": f"{type(exc).__name__}: {exc}"}
        stage.finished_at = _now()
        session.commit()
        raise
    stage.metrics = {**metrics, "seconds": round(time.perf_counter() - started, 3)}
    stage.finished_at = _now()
    session.commit()
    log.info("job %s: %s %s", ctx.job.id, name, stage.status)


def _now() -> datetime:
    return datetime.now(UTC)
