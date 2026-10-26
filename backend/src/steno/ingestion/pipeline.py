"""The ingestion pipeline for one repository (docs/how-it-works/ingestion/README.md §2).

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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steno.assemblers.assemble import assemble as run_assemblers
from steno.claims.store import store_claims
from steno.config import get_settings
from steno.connectors.git import clone as git_clone
from steno.coverage.collect import collect as collect_coverage
from steno.coverage.patterns import record as record_patterns
from steno.db.models import (
    ChangeKind,
    Connector,
    CoverageItem,
    CoverageKind,
    CoverageStatus,
    FactChange,
    IgnoredFile,
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
from steno.identity.identify import deployables
from steno.identity.identify import identify as run_identify
from steno.identity.registry import SqlRegistry
from steno.ingestion import memory
from steno.reconcile.run import reconcile as run_reconcile
from steno.resolvers.java import JVM_BATCHED, JVM_HEAP_MB
from steno.rule_packs.packs import is_enabled, load_all_packs
from steno.rule_packs.versions import dependencies

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
    all_packs = load_all_packs()
    ctx.state["all_packs"] = all_packs  # coverage: which libraries any pack covers
    packs = [p for p in all_packs if is_enabled(p, ctx.workspace)]
    ctx.state["packs"] = packs
    # Library versions, checked against what each pack is tested on (coverage)
    ctx.state["dependencies"] = deps = dependencies(ctx.workspace)
    engine = Engine(ctx.workspace, packs)
    engine.parse()
    if engine.resolver is not None and engine.resolver.failed:
        # Every flow in that language would come out hollow, and writing it would remove the
        # repository's good facts: fail here so nothing is written
        raise RuntimeError(f"symbol resolution failed: {engine.resolver.failed}")
    ctx.state["engine"] = engine
    return {
        "packs": [p.name for p in packs],
        "files": len(engine.files),
        "resolvers": engine.resolver.stats() if engine.resolver else {},
        "libraries": len({d.name for d in deps}),
        "versions_known": len({d.name for d in deps if d.version}),
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


def identify(ctx: JobContext) -> dict[str, Any]:
    """Find the deployables (structure) and give each its permanent Steno ID and real name
    (docs/concepts/identity.md): its names from this run and from other witnesses, the name
    ladder, then the registry by location or canonical name. Applications no longer seen are
    tombstoned after the graph is written, so a failed run removes nothing."""
    engine: Engine = ctx.state["engine"]
    x = engine.out
    assert ctx.workspace is not None
    structure = detect_structure(ctx.workspace, {ep.origin.file for ep in x.entry_points})
    ctx.state["structure"] = structure
    found = deployables(ctx.workspace, ctx.repository.name, structure, x.claims)
    identities, report = run_identify(
        SqlRegistry(ctx.session, x.claims), ctx.repository.id, found, ctx.job.id, remove=False
    )
    ctx.state["identities"] = identities
    ctx.state["identity_report"] = report
    return {
        "applications": {
            i.canonical: {
                "steno_id": i.steno_id,
                **({"aliases": i.aliases} if i.aliases else {}),
                **({"provisional": True} if i.provisional else {}),
            }
            for i in identities.values()
        },
        **report.as_dict(),
    }


def flows(ctx: JobContext) -> dict[str, Any]:
    """Build the call graph in memory, walk it from every entry point, and plan the graph:
    architecture nodes, code nodes, each flow's trace and steps, and flow rollups. The call
    graph itself isn't kept (D60)."""
    engine: Engine = ctx.state["engine"]
    x = engine.out
    structure = ctx.state["structure"]
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
            identities=ctx.state.get("identities", {}),
        ),
        http_systems=[s for p in engine.packs for s in p.http_systems],
        packs=ctx.state["all_packs"],  # the ledger's I/O and harmless lists (D101)
    )
    ctx.state["plan"] = plan
    flow_nodes = [n for n in plan.nodes.values() if n.labels[0] == "Flow"]
    return {
        "modules": {m.path: sorted(m.roles) for m in structure.modules},
        "flows": len(flow_nodes),
        "steps": sum(1 for n in plan.nodes.values() if n.labels[0] == "Step"),
        "trace_entries": sum(n.props.get("trace_entries") or 0 for n in flow_nodes),
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
    # The run succeeded: applications it no longer found are removed (identity tombstones)
    report = ctx.state.get("identity_report")
    registry = SqlRegistry(ctx.session)
    for steno_id in report.removed_ids if report else []:
        registry.remove(steno_id, ctx.job.id)
    # What other witnesses say (PLATFORM, DECLARED): this run's claims replace the last run's
    engine: Engine = ctx.state["engine"]
    stored = store_claims(
        ctx.session, ctx.repository.id, ctx.job.id, ctx.job.to_sha, engine.out.claims
    )
    ctx.state["stored_claims"] = stored
    return {
        **plan.counts(),
        "delta": delta.counts(),
        "fact_changes": len(rows),
        "claims": {"added": stored.added, "removed": stored.removed, "kept": stored.kept},
    }


def reconcile(ctx: JobContext) -> dict[str, Any]:
    """Compare the code with other witnesses for every subject this job touched, and write
    agreement states, citations per witness, and declared-only edges into the graph
    (docs/how-it-works/ingestion/claims-and-reconcile.md §7)."""
    stored = ctx.state.get("stored_claims")
    identities = ctx.state.get("identities", {})
    applications = {
        ids.application_id(i.steno_id): {n.name for n in i.names} for i in identities.values()
    }
    summary = run_reconcile(
        ctx.session,
        get_driver(),
        database(),
        stored.touched if stored else set(),
        applications,
    )
    ctx.state["reconcile"] = summary
    return summary.counts()


def coverage(ctx: JobContext) -> dict[str, Any]:
    """What no rule explained (docs/how-it-works/ingestion/coverage-report.md):
    one `coverage_item` per gap, keyed by kind and target. Triage carries over from this
    repository's previous run: an item someone ignored stays ignored (D62)."""
    engine: Engine = ctx.state["engine"]
    report = collect_coverage(
        engine.out,
        engine.resolver,
        ctx.state["plan"],
        ctx.state["all_packs"],
        _application_of(ctx),
        enabled=ctx.state.get("packs"),
        deps=ctx.state.get("dependencies"),
        **_agreement_items(ctx),
        files=[p.relative_to(engine.repo).as_posix() for p in engine.files],
        read=engine.read,
        root=engine.repo,
        in_library=_in_library(ctx),
        ignored_files={(f.fingerprint, f.name) for f in ctx.session.scalars(select(IgnoredFile))},
        config_used=(
            engine.config_used
            | (getattr(engine.resolver.properties, "used", set()) if engine.resolver else set())
        ),
    )
    previous = _previous_triage(ctx)
    statuses: dict[str, CoverageStatus] = {}
    for it in report.items:
        status = previous.get((it.kind, it.target), CoverageStatus.UNEXPLAINED)
        # "Explained" means a rule covers it now; if it's back, it isn't
        if status == CoverageStatus.EXPLAINED:
            status = CoverageStatus.UNEXPLAINED
        statuses[it.fingerprint] = status
        ctx.session.add(
            CoverageItem(
                job_id=ctx.job.id,
                repository_id=ctx.repository.id,
                kind=CoverageKind(it.kind),
                signal=it.signal,
                label=it.label,
                target_symbol=it.target,
                occurrences=it.occurrences,
                samples=it.samples,
                applications=it.applications,
                status=status,
            )
        )
    # The organization's patterns: what rule curation works from (D121)
    patterns = record_patterns(ctx.session, ctx.repository.id, ctx.job.id, report.items, statuses)
    return {**report.metrics, "patterns": patterns}


def _in_library(ctx: JobContext) -> Any:
    """Whether a file is in one of the repository's library modules (D122 evidence)."""
    structure = ctx.state["structure"]

    def check(file: str) -> bool:
        module = structure.module_of(file)
        return module is not None and "Library" in module.roles

    return check


def _application_of(ctx: JobContext) -> Any:
    """The application a file belongs to: its service module's. Code outside every service
    (scripts, shared libraries, tooling) belongs to the repository's application when it has
    exactly one, and to none otherwise: it's still in the repository's items, but it isn't an
    application of its own."""
    structure = ctx.state["structure"]
    identities = ctx.state.get("identities", {})
    repo = ctx.repository.name

    def name(module: Any) -> str:
        identity = identities.get(module.path)
        return identity.canonical if identity else str(structure.application_name(repo, module))

    services = [m for m in structure.modules if "Service" in m.roles]
    only = name(services[0]) if len(services) == 1 else ""

    def app_of(file: str | None) -> str:
        module = structure.module_of(file) if file else None
        if module is not None and "Service" in module.roles:
            return name(module)
        return only

    return app_of


def _agreement_items(ctx: JobContext) -> dict[str, Any]:
    """This repository's share of reconcile's findings: disagreements about its applications
    or its own claims, and names its claims use that match no application."""
    summary = ctx.state.get("reconcile")
    if summary is None:
        return {}
    repo = ctx.repository.name
    apps = {ids.application_id(i.steno_id) for i in ctx.state.get("identities", {}).values()}
    disagreements = [
        r
        for r in summary.results
        if r["agreement"] != "confirmed"
        and (r["app"] in apps or any(s.get("repo") == repo for s in r["sources"]))
    ]
    unmatched = [u for u in summary.unmatched if u.get("repo") == repo]
    return {"disagreements": disagreements, "unmatched": unmatched}


def _previous_triage(ctx: JobContext) -> dict[tuple[str, str], CoverageStatus]:
    """Each item's status in this repository's latest earlier run that has coverage items."""
    last = ctx.session.scalar(
        select(func.max(CoverageItem.job_id)).where(
            CoverageItem.repository_id == ctx.repository.id, CoverageItem.job_id != ctx.job.id
        )
    )
    if last is None:
        return {}
    rows = ctx.session.scalars(select(CoverageItem).where(CoverageItem.job_id == last))
    return {(r.kind.value, r.target_symbol): r.status for r in rows}


def cards(ctx: JobContext) -> dict[str, Any]:
    """Generate LLM purposes and narratives. In a dry run: count tokens and project cost only."""
    raise Skip("not built yet")


STAGES: list[tuple[StageName, StageFn]] = [
    (StageName.CLONE, clone),
    (StageName.DEPS, deps),
    (StageName.PARSE, parse),
    (StageName.EXTRACT, extract),
    (StageName.ASSEMBLE, assemble),
    (StageName.IDENTIFY, identify),
    (StageName.FLOWS, flows),
    (StageName.WRITE, write),
    (StageName.RECONCILE, reconcile),
    (StageName.COVERAGE, coverage),
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
    # The JVM heap this repository needed last time, with headroom (D128)
    resources = (job.stats or {}).get("resources") or {}
    heap = resources.get("jvm_heap_mb")
    token = JVM_HEAP_MB.set(heap if isinstance(heap, int) else None)
    batched = JVM_BATCHED.set(bool(resources.get("batched")))
    try:
        for name, fn in STAGES:
            _run_stage(session, ctx, name, fn)
    finally:
        JVM_HEAP_MB.reset(token)
        JVM_BATCHED.reset(batched)
        if ctx.workspace is not None and ctx.workspace.exists():
            shutil.rmtree(ctx.workspace, ignore_errors=True)


def _run_stage(session: Session, ctx: JobContext, name: StageName, fn: StageFn) -> None:
    stage = IngestionStage(
        job_id=ctx.job.id, stage=name, status=StageStatus.RUNNING, started_at=_now()
    )
    session.add(stage)
    session.commit()

    started = time.perf_counter()
    measured = memory.reset_peak()
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
    peak = memory.peak_mb() if measured else None
    stage.metrics = {
        **metrics,
        "seconds": round(time.perf_counter() - started, 3),
        **({"rss_peak_mb": peak} if peak is not None else {}),
    }
    stage.finished_at = _now()
    session.commit()
    log.info("job %s: %s %s", ctx.job.id, name, stage.status)


def _now() -> datetime:
    return datetime.now(UTC)
