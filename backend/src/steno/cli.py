"""`steno` command line: run the processes and manage local state."""

import logging

import typer

from steno.config import get_settings
from steno.db.models import JobMode

app = typer.Typer(no_args_is_help=True, help="Steno: org-wide context engine for AI agents.")
db_app = typer.Typer(no_args_is_help=True, help="Postgres schema.")
graph_app = typer.Typer(no_args_is_help=True, help="Neo4j schema and projections.")
job_app = typer.Typer(no_args_is_help=True, help="Ingestion jobs.")
app.add_typer(db_app, name="db")
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
