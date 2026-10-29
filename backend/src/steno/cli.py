"""`steno` command line: run the processes and manage local state."""

import logging

import typer

from steno.config import get_settings
from steno.db.models import JobMode

app = typer.Typer(no_args_is_help=True, help="Steno: org-wide context engine for AI agents.")
db_app = typer.Typer(no_args_is_help=True, help="Postgres schema.")
graph_app = typer.Typer(no_args_is_help=True, help="Neo4j schema and projections.")
job_app = typer.Typer(no_args_is_help=True, help="Ingestion jobs.")
rules_app = typer.Typer(no_args_is_help=True, help="Rule packs.")
app.add_typer(db_app, name="db")
app.add_typer(rules_app, name="rules")
app.add_typer(graph_app, name="graph")
app.add_typer(job_app, name="job")


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    # Neo4j logs "index already exists" etc. at INFO on every idempotent schema call
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


@app.command()
def api(reload: bool = typer.Option(False, help="Reload on code changes (development).")) -> None:
    """Serve the Admin API (/api) and the MCP server (/mcp)."""
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "steno.api.app:create_app", factory=True, host=s.api_host, port=s.api_port, reload=reload
    )


@app.command()
def worker(
    once: bool = typer.Option(False, help="Run at most one job, then exit."),
    processes: int = typer.Option(
        None,
        help="Worker processes, each running one job at a time (STENO_WORKER_PROCESSES; "
        "default: one per CPU, up to 8).",
    ),
) -> None:
    """Claim and run ingestion jobs from the Postgres queue. A job starts only when its memory
    fits what this machine has left (D128); STENO_MAX_PARALLEL_JOBS caps jobs across machines."""
    from steno.ingestion.resources import machine
    from steno.ingestion.worker import processes_for, run_forever, run_once, run_pool

    if once:
        if not run_once():
            typer.echo("queue empty")
    else:
        n = processes or processes_for(machine())
        run_pool(n) if n > 1 else run_forever()


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Apply Alembic migrations."""
    from steno.db.migrate import upgrade

    upgrade(revision)


@graph_app.command("init")
def graph_init() -> None:
    """Create Neo4j constraints and indexes, and sync the declared org and spaces (idempotent)."""
    from steno.db.session import session_scope
    from steno.graph.driver import database, get_driver
    from steno.graph.projection import sync_declared
    from steno.graph.schema import apply_schema

    stmts = apply_schema(get_driver(), database())
    with session_scope() as session:
        spaces = sync_declared(session, get_driver(), database())
    typer.echo(f"applied {len(stmts)} constraints/indexes; synced {spaces} spaces")


@job_app.command("enqueue")
def job_enqueue(
    repo: str = typer.Argument(..., help="Repository name"),
    mode: JobMode = typer.Option(JobMode.DRY_RUN),
) -> None:
    """Queue an ingestion job for a repository."""
    from sqlalchemy import select

    from steno.db.models import Repository
    from steno.db.session import session_scope
    from steno.ingestion.queue import RepositoryBusy, enqueue

    with session_scope() as session:
        repository = session.scalar(select(Repository).where(Repository.name == repo))
        if repository is None:
            raise typer.BadParameter(f"unknown repository {repo!r}")
        try:
            job = enqueue(session, repository.id, mode)
        except RepositoryBusy as busy:
            typer.echo(f"{repo}: {busy}")
            raise typer.Exit(1) from None
        typer.echo(f"queued job {job.id} ({mode}) for {repo}")


@job_app.command("recover")
def job_recover() -> None:
    """Mark jobs left `running` by a worker that's no longer running as failed, so their
    repositories can be ingested again. Only run it when no worker is running (make dev does)."""
    from steno.db.session import session_scope
    from steno.ingestion.queue import recover_interrupted

    with session_scope() as session:
        ids = recover_interrupted(session)
    if ids:
        typer.echo(f"marked {len(ids)} interrupted job(s) failed: {', '.join(map(str, ids))}")


@job_app.command("nightly")
def job_nightly() -> None:
    """Run the nightly check now: queue a full run for every repository whose default branch
    moved since it was last ingested (D59). Workers also do this on schedule."""
    from steno.db.session import session_scope
    from steno.ingestion import nightly

    with session_scope() as session:
        result = nightly.run(session)
    for key in ("queued", "unchanged", "busy", "failed"):
        typer.echo(f"{key}: {', '.join(result[key]) or '-'}")


@rules_app.command("list")
def rules_list() -> None:
    """Every rule pack: where it's from, its witness type, facets, and rule count."""
    from steno.rule_packs.packs import load_all_packs

    root = get_settings().rule_packs_dir
    for pack in load_all_packs():
        where = (
            pack.path.relative_to(root).parts[0]
            if pack.path.is_relative_to(root)
            else str(pack.path.parent)
        )
        facets = f"  [{', '.join(pack.facets)}]" if pack.facets else ""
        typer.echo(
            f"{pack.source:4s} {where:10s} {pack.witness_type:9s} {pack.name:40s} "
            f"{len(pack.rules):3d} rules{facets}"
        )


@rules_app.command("test")
def rules_test(
    packs: list[str] = typer.Argument(None, help="Pack names; all packs if omitted"),
) -> None:
    """Run rule packs' test cases and compare with expected.yaml."""
    from steno.rule_packs.packs import load_all_packs
    from steno.rule_packs.testing import run_pack_tests

    all_packs = load_all_packs()
    selected = [p for p in all_packs if not packs or p.name in packs]
    if packs and len(selected) != len(packs):
        unknown = set(packs) - {p.name for p in selected}
        raise typer.BadParameter(f"unknown pack(s): {', '.join(sorted(unknown))}")
    failed = total = 0
    for pack in selected:
        for result in run_pack_tests(pack, all_packs):
            total += 1
            mark = (
                typer.style("PASS", fg="green") if result.passed else typer.style("FAIL", fg="red")
            )
            typer.echo(f"{mark}  {result.pack} / {result.rule} / {result.case}")
            if not result.passed:
                failed += 1
                for err in result.errors:
                    typer.echo(f"        error: {err}")
                for section, items in result.missing.items():
                    for item in items:
                        typer.echo(f"        missing {section[:-1]}: {item}")
                for section, items in result.unexpected.items():
                    for item in items:
                        typer.echo(f"        unexpected {section[:-1]}: {item}")
    typer.echo(f"\n{total - failed}/{total} cases passed")
    if failed:
        raise typer.Exit(1)


@app.command()
def extract(
    path: str = typer.Argument(..., help="A local repository or folder"),
    output: str = typer.Option(None, "--json", help="Write every fact to this JSON file"),
) -> None:
    """Parse, extract and assemble a local folder and summarize the facts (no flows or database)."""
    import json
    from collections import Counter
    from pathlib import Path

    from steno.extraction.report import to_json
    from steno.ingestion.local import run_folder
    from steno.rule_packs.packs import is_enabled, load_all_packs

    repo = Path(path).resolve()
    packs = [p for p in load_all_packs() if is_enabled(p, repo)]
    typer.echo(f"packs: {', '.join(p.name for p in packs) or '(none enabled)'}")
    x = run_folder(repo, packs).out
    by = Counter
    typer.echo(f"nodes: {dict(by(n.label for n in x.nodes))}")
    typer.echo(f"edges: {dict(by(e.type for e in x.edges))}")
    typer.echo(f"clues: {dict(by(c.kind for c in x.clues))}")
    typer.echo(
        f"entry points: {len(x.entry_points)}   dropped: {len(x.dropped)}   errors: {len(x.errors)}"
    )
    for err in x.errors[:10]:
        typer.echo(f"  error: {err}")
    if output:
        Path(output).write_text(json.dumps(to_json(x), indent=2, default=str))
        typer.echo(f"wrote {output}")


@app.command("resolver-check")
def resolver_check(
    path: str = typer.Argument(..., help="A local Java repository"),
    batch_files: int = typer.Option(200, help="Files per batch for the batched pass"),
) -> None:
    """Resolve a Java repository in one pass and in batches and compare them (D127): the
    batched path must give the same answers. Prints each pass's time and memory."""
    import json
    from pathlib import Path

    from steno.resolvers.java import compare_batched

    out = compare_batched(Path(path).resolve(), batch_files)
    typer.echo(f"files: {out['files']}")
    for name in ("one_pass", "batched"):
        s = out[name]
        typer.echo(f"{name}: {s.get('ms')} ms, batches {s.get('batches')}, "
                   f"memory {json.dumps(s.get('memory'))}")  # fmt: skip
    diffs = out["differences"]
    typer.echo(f"differences: {len(diffs)}")
    for d in diffs:
        typer.echo(f"  {d}")
    raise typer.Exit(1 if diffs else 0)


@app.command()
def curate(
    limit: int | None = typer.Option(None, help="At most this many patterns (default: all)"),
    fingerprint: list[str] = typer.Option(None, help="Work on these patterns only"),
    drafter: str = typer.Option(None, help="claude_agent_sdk (default) | openai | gemini | …"),
    budget: float = typer.Option(None, help="Dollars this run may spend (default: setting)"),
    model: str = typer.Option(None, help="The model to draft with (default: setting)"),
) -> None:
    """Rule curation (D120): draft proposals for the open patterns, in score order, until the
    run's budget is spent. Every rule is proven
    against its own test cases before it's proposed; a person approves it."""
    from steno.curation.drafters import get_drafter
    from steno.curation.run import curate as run_curation
    from steno.db.models import RuleProposal
    from steno.db.session import session_scope

    with session_scope() as session:
        run = run_curation(
            session, limit=limit, fingerprints=fingerprint or None,
            drafter=get_drafter(drafter, model), budget_usd=budget, model=model,
        )  # fmt: skip
        typer.echo(f"curation run {run.id}: {run.status}, ${run.spent_usd:.2f} spent, {run.stats}")
        for p in session.query(RuleProposal).filter(RuleProposal.run_id == run.id):
            typer.echo(f"  #{p.id} {p.kind.value:<12} {p.status.value:<13} {p.title}")
            typer.echo(f"      {p.summary}")


@app.command()
def proposals(status: str = typer.Option(None, help="ready, needs_person, failed, …")) -> None:
    """Rule proposals waiting for review (or in a status)."""
    from steno.db.models import ProposalStatus, RuleProposal
    from steno.db.session import session_scope

    with session_scope() as session:
        q = session.query(RuleProposal).order_by(RuleProposal.id.desc())
        if status:
            q = q.filter(RuleProposal.status == ProposalStatus(status))
        for p in q.limit(50):
            typer.echo(
                f"#{p.id} {p.kind.value:<12} {p.status.value:<13} ${p.cost_usd:.2f} {p.title}"
            )


@app.command()
def proposal(proposal_id: int) -> None:
    """One proposal in full: summary, the rule, its tests and their results."""
    import json

    import yaml

    from steno.db.models import RuleProposal
    from steno.db.session import session_scope

    with session_scope() as session:
        p = session.get(RuleProposal, proposal_id)
        if p is None:
            raise typer.BadParameter(f"no proposal #{proposal_id}")
        typer.echo(f"#{p.id} {p.kind.value} · {p.status.value} · {p.title}")
        typer.echo(f"patterns: {', '.join(p.fingerprints)}")
        typer.echo(f"facet: {p.facet}   pack: {p.target_pack}   drafter: {p.drafter}   "
                   f"${p.cost_usd:.2f}, {p.turns} turns")  # fmt: skip
        typer.echo(f"\n{p.summary}\n")
        if p.rule:
            typer.echo(f"--- rule\n{p.rule}")
        if p.tests:
            typer.echo(f"--- tests\n{yaml.safe_dump(p.tests, sort_keys=False)}")
        if p.proof:
            typer.echo(f"--- proof\n{json.dumps(p.proof, indent=1)}")
        for flag in p.flags:
            typer.echo(f"! {flag}")


@app.command()
def approve(
    proposal_id: int,
    by: str = typer.Option(None, help="Who approves (default: your git user)"),
) -> None:
    """Approve a ready proposal: a rule is written into its pack with its tests, which must
    pass, and published (D131: the steno/rules pull request, or a push, per destination);
    "ignore" ignores the pattern; "vocabulary" sends it to the backlog."""
    from steno.curation.approve import approve as approve_proposal
    from steno.db.models import RuleProposal
    from steno.db.session import session_scope

    with session_scope() as session:
        p = session.get(RuleProposal, proposal_id)
        if p is None:
            raise typer.BadParameter(f"no proposal #{proposal_id}")
        out = approve_proposal(session, p, by or _git_user())
        typer.echo(f"#{p.id} approved ({p.kind.value})")
        for k, v in out.items():
            typer.echo(f"  {k}: {v}")


@app.command()
def reject(
    proposal_id: int,
    why: str = typer.Option(..., help="Why: kept with the proposal and the pattern"),
    by: str = typer.Option(None, help="Who rejects (default: your git user)"),
) -> None:
    """Reject a proposal; its pattern is rejected and isn't proposed again unless it grows."""
    from steno.curation.approve import reject as reject_proposal
    from steno.db.models import RuleProposal
    from steno.db.session import session_scope

    with session_scope() as session:
        p = session.get(RuleProposal, proposal_id)
        if p is None:
            raise typer.BadParameter(f"no proposal #{proposal_id}")
        reject_proposal(session, p, by or _git_user(), why)
        typer.echo(f"#{p.id} rejected")


def _git_user() -> str:
    import subprocess

    out = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    return out.stdout.strip() or "cli"
