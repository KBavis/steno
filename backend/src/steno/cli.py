"""`steno` command line: run the processes and manage local state."""

import logging
from pathlib import Path

import typer

from steno.config import get_settings
from steno.db.models import JobMode

app = typer.Typer(no_args_is_help=True, help="Steno: org-wide context engine for AI agents.")
db_app = typer.Typer(no_args_is_help=True, help="Postgres schema.")
graph_app = typer.Typer(no_args_is_help=True, help="Neo4j schema and projections.")
job_app = typer.Typer(no_args_is_help=True, help="Ingestion jobs.")
rules_app = typer.Typer(no_args_is_help=True, help="Rule packs.")
coverage_app = typer.Typer(
    no_args_is_help=True,
    help="The coverage report's patterns: the rule author's worklist (D146, D147).",
)
app.add_typer(db_app, name="db")
app.add_typer(rules_app, name="rules")
app.add_typer(coverage_app, name="coverage")
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


@graph_app.command("stats")
def graph_stats(
    out: Path = typer.Option(None, "--json", help="Also save the snapshot to this file"),
    compare: Path = typer.Option(None, help="A snapshot saved earlier, to show what changed"),
) -> None:
    """What the graph holds: nodes by label, edges by type, agreement states. Save one before a
    reset or a rule change and compare after."""
    import json

    from steno.graph.driver import database, get_driver
    from steno.graph.maintenance import diff, stats

    now = stats(get_driver(), database())
    if out:
        out.write_text(json.dumps(now, indent=2))
    if compare:
        changes = diff(json.loads(compare.read_text()), now)
        typer.echo("\n".join(changes) if changes else "no change")
        return
    typer.echo(f"nodes: {now['nodes']}   edges: {now['edges']}")
    for group in ("by_label", "by_type", "agreement"):
        typer.echo(f"\n{group}:")
        for name, n in now[group].items():
            typer.echo(f"  {name:<28} {n}")


@graph_app.command("reset")
def graph_reset(
    queue: bool = typer.Option(False, "--queue", help="Queue a full run of every repository"),
    yes: bool = typer.Option(False, "--yes", help="Skip the typed confirmation (scripts only)"),
) -> None:
    """DESTRUCTIVE: delete every node and edge in Neo4j, then mark every repository not
    ingested so the next run rebuilds the graph from git. Postgres is kept: connectors,
    repositories, the identity registry (Steno IDs survive), claims, coverage."""
    from sqlalchemy import select, update

    from steno.db.models import IngestionJob, JobStatus, Repository, RepositorySelection
    from steno.db.session import session_scope
    from steno.graph.driver import database, get_driver
    from steno.graph.maintenance import confirmed, stats, wipe
    from steno.graph.projection import sync_declared
    from steno.graph.schema import apply_schema

    driver, db = get_driver(), database()
    with session_scope() as session:
        active = session.scalars(
            select(IngestionJob.id).where(
                IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
            )
        ).all()
        repos = session.scalars(
            select(Repository.name).where(Repository.selection == RepositorySelection.INCLUDED)
        ).all()
    if active:
        typer.echo(f"refusing: {len(active)} job(s) queued or running; wait or stop the workers")
        raise typer.Exit(1)

    before = stats(driver, db)
    s = get_settings()
    typer.secho("This deletes the whole Steno graph. It can't be undone.", fg="red", bold=True)
    typer.echo(f"  Neo4j: {s.neo4j_uri}, database {db!r}")
    typer.echo(f"  deletes {before['nodes']} nodes and {before['edges']} edges")
    typer.echo(f"  then marks {len(repos)} repositories not ingested"
               + (" and queues a full run of each" if queue else ""))  # fmt: skip
    typer.echo("  keeps Postgres: connectors, repositories, identity, claims, coverage")
    typer.echo("Tip: `steno graph stats --json before.json` first, to compare after re-ingesting.")
    if not yes:
        typed = typer.prompt(f"Type the database name ({db}) to delete it", default="")
        if not confirmed(typed, db):
            typer.echo("not confirmed; nothing was deleted")
            raise typer.Exit(1)

    # Marked first: if the wipe fails partway, the next nightly run still re-ingests everything
    with session_scope() as session:
        session.execute(update(Repository).values(last_ingested_sha=None))
    deleted = wipe(driver, db)
    apply_schema(driver, db)
    with session_scope() as session:
        sync_declared(session, driver, db)  # the organization and spaces come from Postgres
    typer.echo(f"deleted {deleted} nodes; repositories marked not ingested")
    if queue:
        from steno.db.models import JobTrigger
        from steno.ingestion.queue import RepositoryBusy, enqueue

        with session_scope() as session:
            for repo in session.scalars(
                select(Repository).where(Repository.selection == RepositorySelection.INCLUDED)
            ):
                try:
                    enqueue(session, repo.id, JobMode.FULL, JobTrigger.MANUAL)
                    typer.echo(f"queued {repo.name}")
                except RepositoryBusy:
                    typer.echo(f"{repo.name}: already queued")


@app.command("llm-check")
def llm_check() -> None:
    """Send one short prompt to the LLM the organization chose, to see that it answers."""
    from steno.db.session import session_scope
    from steno.llm import LlmUnavailable
    from steno.llm.choice import llm_choice, provider_for

    with session_scope() as session:
        choice = llm_choice(session)
        typer.echo(f"{choice['provider']} / {choice['model']} ({choice['source']})")
        try:
            done = provider_for(session).complete("Answer in one word.", "Say ok.")
        except LlmUnavailable as exc:
            typer.echo(f"not reachable: {exc}")
            raise typer.Exit(1) from exc
    typer.echo(f"answered in {done.latency_ms} ms: {done.text[:80]}")


@graph_app.command("summaries")
def graph_summaries(
    repo: str = typer.Option(None, help="Only this repository's applications (and their spaces)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count tokens; write nothing"),
    provider: str = typer.Option(
        None, help="litellm | claude_agent_sdk | fake (default: the organization's Settings)"
    ),
    model: str = typer.Option(None, help="The model (default: the organization's Settings)"),
) -> None:
    """Write the generated summaries (D143) for applications, then their spaces, then the
    organization, from the graph as it is. Only those whose inputs changed are written."""
    import json

    from steno.db.session import session_scope
    from steno.graph.driver import database, get_driver
    from steno.llm.choice import provider_for
    from steno.summaries import summarize
    from steno.summaries.run import host_reader

    driver, db = get_driver(), database()
    apps = None
    if repo:
        with driver.session(database=db) as s:
            apps = s.run(
                "MATCH (a:Application {repo: $repo}) RETURN a.id AS id ORDER BY a.id", repo=repo
            ).value("id")
        if not apps:
            raise typer.BadParameter(f"no applications from repository {repo!r} in the graph")
    with session_scope() as session:
        report = summarize(
            driver,
            db,
            session,
            provider_for(session, provider, model),
            applications=apps,
            read=host_reader(session),
            dry_run=dry_run,
        )
    typer.echo(json.dumps(report.as_dict(), indent=2))


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
    from steno.ingestion.queue import recover_interrupted, remove_leftovers

    with session_scope() as session:
        ids = recover_interrupted(session)
    remove_leftovers(ids)  # the clones and indexes they left (no worker is running: see above)
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


@rules_app.command("destinations")
def rules_destinations() -> None:
    """Where the rule author's rules go: the repository for each kind of rule (D129, D148)."""
    import subprocess
    from pathlib import Path

    def origin(folder: Path) -> str:
        found = subprocess.run(
            ["git", "-C", str(folder), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )  # fmt: skip
        return found.stdout.strip() or "no git remote"

    s = get_settings()
    if s.rule_pack_dirs:
        for folder in s.rule_pack_dirs:
            typer.echo(f"organization rules: {folder} -> {origin(folder)}")
    else:
        typer.echo("organization rules: no folder configured (set STENO_RULE_PACK_DIRS)")
    if s.rules_org_only:
        typer.echo(
            "public-library rules: not committed (STENO_RULES_ORG_ONLY): list them to report"
        )
    else:
        typer.echo(f"public-library rules: {s.rule_packs_dir} -> {origin(s.rule_packs_dir)}")


@rules_app.command("check")
def rules_check(
    rule_file: str = typer.Argument(..., help="The rule's YAML, inside its pack's rules/ folder"),
    pattern: list[str] = typer.Option(..., "--pattern", "-p", help="Fingerprints it explains"),
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON"),
) -> None:
    """Run a rule over every file its patterns occur in, read from the git host at the ingested
    commit: recall, whether it states a surface fact, and the application names it would
    create (rule-curation §6). Exits 1 when the rule must not be proposed."""
    import dataclasses
    import json
    from pathlib import Path

    import yaml
    from sqlalchemy import select

    from steno.db.models import CoveragePattern
    from steno.db.session import session_scope
    from steno.rule_packs.check import check_rule, git_host_reader
    from steno.rule_packs.packs import load_all_packs

    path = Path(rule_file)
    rule = path.read_text()
    pack_yaml = path.parent.parent / "pack.yaml"
    pack = yaml.safe_load(pack_yaml.read_text()) if pack_yaml.exists() else None
    rule_id = (yaml.safe_load(rule) or {}).get("id")
    # The rule itself is left out of its loaded pack, so it isn't counted twice; the pack's
    # other rules stay (a model rule gives the table a session rule needs)
    others = []
    for p in load_all_packs():
        if pack and p.name == pack.get("name"):
            p = dataclasses.replace(p, rules=[r for r in p.rules if r.id != rule_id])
        others.append(p)
    results = {}
    blocking = False
    with session_scope() as session:
        read = git_host_reader(session)
        for fp in pattern:
            p = session.scalar(select(CoveragePattern).where(CoveragePattern.fingerprint == fp))
            if p is None:
                raise typer.BadParameter(f"no pattern {fp!r}: see `steno coverage patterns`")
            result = check_rule(session, p, rule, pack, read, others)
            results[fp] = {**result.to_json(), "problems": result.problems()}
            blocking = blocking or result.blocking()
    if as_json:
        typer.echo(json.dumps(results, indent=2))
    else:
        for fp, r in results.items():
            typer.echo(f"{fp}")
            typer.echo(f"  surface fact: {'yes' if r['surface'] else 'no'}")
            typer.echo(f"  recall: explains {len(r['explained'])} of {r['files']} files")
            for f in r["incomplete"][:10]:
                typer.echo(f"    matched, but needs files not loaded here: {f}")
            for f in r["missed"][:10]:
                typer.echo(f"    missed {f}")
            if r["names"]:
                typer.echo(f"  application names: {', '.join(r['names'])}")
            for e in r["errors"]:
                typer.echo(f"  error: {e}")
            for problem in r["problems"]:
                typer.echo(typer.style(f"  problem: {problem}", fg="red"))
    if blocking:
        raise typer.Exit(1)


@coverage_app.command("patterns")
def coverage_patterns(
    state: str = typer.Option("open", help="open, ignored, expected, proposed, covered"),
    limit: int = typer.Option(20, help="How many frameworks or lone patterns"),
    quiet: bool = typer.Option(False, "--quiet", help="Include unread files and rule health"),
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON"),
) -> None:
    """Patterns in a state, as frameworks ranked by score: the report's order (D146)."""
    import json

    from steno.api.routes.patterns import patterns
    from steno.db.models import PatternState
    from steno.db.session import session_scope

    with session_scope() as session:
        out = patterns(session, PatternState(state), limit, quiet)
    if as_json:
        typer.echo(json.dumps(out, indent=2))
        return
    for g in out["frameworks"]:
        owner = g["owner"] or "(the application's own code)"
        typer.echo(f"{g['score']:10.1f}  {owner}")
        for p in g["patterns"]:
            reach = (
                f"{p['repositories']} repos, {p['applications']} apps, {p['occurrences']} places"
            )
            typer.echo(f"            {p['fingerprint']}\n              {p['label']} · {reach}")
    if out["more"]:
        typer.echo(f"\n{out['more']} more; raise --limit")


@coverage_app.command("show")
def coverage_show(
    fingerprint: str = typer.Argument(..., help="A pattern's fingerprint, `signal:target`"),
    limit: int = typer.Option(20, help="How many places"),
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON"),
) -> None:
    """Where a pattern occurs, with links to the code at the ingested commit, and samples."""
    import json

    from steno.coverage.patterns import occurrences
    from steno.db.session import session_scope

    with session_scope() as session:
        try:
            out = occurrences(session, fingerprint, limit)
        except LookupError as exc:
            raise typer.BadParameter(str(exc)) from exc
    if as_json:
        typer.echo(json.dumps(out, indent=2))
        return
    typer.echo(f"{out['label']}  [{out['state']}]  owner: {out['owner'] or '-'}")
    for place in out["places"]:
        typer.echo(f"  {place['repository']}/{place['file']}  {place['symbol']}")
        if place["url"]:
            typer.echo(f"    {place['url']}")
    for smp in out["samples"][:10]:
        line = f":{smp['line']}" if smp.get("line") else ""
        typer.echo(
            f"  sample {smp['repository']}/{smp.get('file')}{line}  {smp.get('detail') or ''}"
        )
    if out["more"]:
        typer.echo(f"  {out['more']} more places; raise --limit")


@coverage_app.command("reset")
def coverage_reset(
    yes: bool = typer.Option(False, "--yes", help="Don't ask; for scripts"),
) -> None:
    """Forget the coverage report: every item, pattern, occurrence, and triage decision
    (ignored, expected, proposed). The next ingestion of each repository builds it again.
    The graph, jobs, and everything else in Postgres are kept."""
    from sqlalchemy import delete, func, select

    from steno.db.models import CoverageItem, CoverageOccurrence, CoveragePattern, IgnoredFile
    from steno.db.session import session_scope

    tables = (CoverageOccurrence, CoverageItem, CoveragePattern, IgnoredFile)
    with session_scope() as session:
        counts = {
            t.__tablename__: session.scalar(select(func.count()).select_from(t)) for t in tables
        }
        decided = session.scalar(
            select(func.count()).select_from(CoveragePattern).where(CoveragePattern.state != "open")
        )
        typer.echo("  " + ", ".join(f"{n} {t}" for t, n in counts.items()))
        typer.echo(f"  {decided} patterns have a triage decision, which is lost")
        if not yes and not typer.confirm("Forget the coverage report?"):
            raise typer.Exit(1)
        for t in tables:
            session.execute(delete(t))
    typer.echo("coverage reset: re-ingest repositories to build it again")


@coverage_app.command("mark")
def coverage_mark(
    fingerprints: list[str] = typer.Argument(..., help="Patterns' fingerprints"),
    state: str = typer.Option(..., help="ignored, expected, proposed, or open"),
    note: str = typer.Option(..., help="Why: harmless because…, outside the organization…, a PR"),
) -> None:
    """Set patterns' state, everywhere they occur and in later runs (D147)."""
    from steno.coverage.patterns import TRIAGE, triage
    from steno.db.models import PatternState
    from steno.db.session import session_scope

    wanted = PatternState(state)
    if wanted not in TRIAGE:
        raise typer.BadParameter(f"state is one of {sorted(s.value for s in TRIAGE)}")
    with session_scope() as session:
        changed = triage(session, fingerprints, wanted, note)
    typer.echo(f"{changed} of {len(set(fingerprints))} patterns marked {state}")
    if changed < len(set(fingerprints)):
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
