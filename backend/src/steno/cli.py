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
    """Create Neo4j constraints and indexes (idempotent)."""
    from steno.graph.driver import database, get_driver
    from steno.graph.schema import apply_schema

    stmts = apply_schema(get_driver(), database())
    typer.echo(f"applied {len(stmts)} constraints/indexes")


@app.command("load-config")
def load_config(path: str = typer.Option(None, help="Defaults to STENO_POC_CONFIG_PATH.")) -> None:
    """Load the POC declarations file into Postgres and project spaces into Neo4j."""
    from pathlib import Path

    from steno.db.session import session_scope
    from steno.graph.driver import database, get_driver
    from steno.graph.projection import project_spaces
    from steno.poc.config_loader import load_config as load
    from steno.poc.config_loader import read_config

    config = read_config(Path(path) if path else get_settings().poc_config_path)
    with session_scope() as session:
        counts = load(session, config)
        projected = project_spaces(session, get_driver(), database(), config.organization)
    typer.echo(f"loaded {counts}; projected {projected} spaces into Neo4j")


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
            raise typer.BadParameter(f"unknown repository {repo!r}; run `steno load-config`?")
        job = enqueue(session, repository.id, mode)
        typer.echo(f"queued job {job.id} ({mode}) for {repo}")
