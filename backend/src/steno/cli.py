"""`steno` command line: run the processes and manage local state."""

import logging

import typer

from steno.config import get_settings
from steno.db.models import JobMode

app = typer.Typer(no_args_is_help=True, help="Steno: org-wide context engine for AI agents.")
db_app = typer.Typer(no_args_is_help=True, help="Postgres schema.")
graph_app = typer.Typer(no_args_is_help=True, help="Neo4j schema and projections.")
job_app = typer.Typer(no_args_is_help=True, help="Ingestion jobs.")
rules_app = typer.Typer(no_args_is_help=True, help="Extractor rule packs.")
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
def worker(once: bool = typer.Option(False, help="Run at most one job, then exit.")) -> None:
    """Claim and run ingestion jobs from the Postgres queue."""
    from steno.ingestion.worker import run_forever, run_once

    if once:
        if not run_once():
            typer.echo("queue empty")
    else:
        run_forever()


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
    from steno.ingestion.queue import enqueue

    with session_scope() as session:
        repository = session.scalar(select(Repository).where(Repository.name == repo))
        if repository is None:
            raise typer.BadParameter(f"unknown repository {repo!r}")
        job = enqueue(session, repository.id, mode)
        typer.echo(f"queued job {job.id} ({mode}) for {repo}")


@rules_app.command("test")
def rules_test(
    packs: list[str] = typer.Argument(None, help="Pack names; all packs if omitted"),
) -> None:
    """Run rule packs' test cases and compare with expected.yaml."""
    from steno.extractors.packs import load_packs
    from steno.extractors.testing import run_pack_tests

    all_packs = load_packs(get_settings().rule_packs_dir)
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
    """Run the enabled rule packs over a local folder and summarize the facts (no database)."""
    import json
    from collections import Counter
    from pathlib import Path

    from steno.extractors.engine import extract as run_extract
    from steno.extractors.packs import is_enabled, load_packs
    from steno.extractors.report import to_json

    repo = Path(path).resolve()
    packs = [p for p in load_packs(get_settings().rule_packs_dir) if is_enabled(p, repo)]
    typer.echo(f"packs: {', '.join(p.name for p in packs) or '(none enabled)'}")
    x = run_extract(repo, packs)
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
