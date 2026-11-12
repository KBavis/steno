"""Write the summaries for some applications, then their spaces, then the organization."""

import logging
from dataclasses import dataclass, field
from typing import Any

from neo4j import Driver
from sqlalchemy.orm import Session

from steno.llm import Generated, Provider, generate
from steno.summaries import inputs as read_inputs
from steno.summaries.inputs import Inputs, ReadFile
from steno.summaries.prompts import SYSTEM, parse

log = logging.getLogger(__name__)
LABEL = {"application": "Application", "space": "Space", "organization": "Organization"}


@dataclass
class Report:
    """What a run wrote, reused, and spent; in a dry run, what it would send."""

    dry_run: bool = False
    model: str | None = None
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    would_write: list[str] = field(default_factory=list)  # dry run
    input_tokens: int = 0  # estimated in a dry run
    output_tokens: int = 0
    cost_usd: float = 0.0
    largest: dict[str, int] = field(default_factory=dict)  # kind → largest input (tokens)

    def add(self, item: Inputs, done: Generated) -> None:
        name = f"{item.kind} {item.name}"
        if done.cached:
            self.unchanged.append(name)
        elif done.text is None:
            self.would_write.append(name)
        else:
            self.written.append(name)
        self.input_tokens += done.input_tokens
        self.output_tokens += done.output_tokens
        self.cost_usd += done.cost_usd
        if done.input_tokens > self.largest.get(item.kind, 0):
            self.largest[item.kind] = done.input_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "model": self.model,
            "written": len(self.written),
            "unchanged": len(self.unchanged),
            **({"would_write": len(self.would_write)} if self.dry_run else {}),
            ("estimated_input_tokens" if self.dry_run else "input_tokens"): self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "largest_input_tokens": self.largest,
        }


def summarize(
    driver: Driver,
    database: str,
    session: Session,
    provider: Provider | None,
    applications: list[str] | None = None,
    read: ReadFile | None = None,
    job_id: int | None = None,
    dry_run: bool = False,
) -> Report:
    """Summaries for `applications` (every application when None), then for the spaces they
    belong to, then for the organization. A summary whose inputs haven't changed is reused."""
    report = Report(dry_run=dry_run, model=provider.model if provider else None)
    if applications is None:
        with driver.session(database=database) as s:
            applications = s.run("MATCH (a:Application) RETURN a.id AS id ORDER BY a.id").value(
                "id"
            )
    spaces: set[str] = set()
    for app_id in applications:
        item = read_inputs.application(driver, database, app_id, read)
        _one(driver, database, session, provider, item, report, job_id, dry_run)
        spaces.update(item.parents)
    for space_id in sorted(spaces):
        item = read_inputs.space(driver, database, space_id)
        _one(driver, database, session, provider, item, report, job_id, dry_run)
    org = read_inputs.organization(driver, database)
    if org is not None:
        _one(driver, database, session, provider, org, report, job_id, dry_run)
    return report


def _one(
    driver: Driver,
    database: str,
    session: Session,
    provider: Provider | None,
    item: Inputs,
    report: Report,
    job_id: int | None,
    dry_run: bool,
) -> None:
    done = generate(
        session,
        provider,
        node_id=item.node_id,
        kind=f"summary:{item.kind}",
        system=SYSTEM[item.kind],
        prompt=item.prompt,
        job_id=job_id,
        dry_run=dry_run,
    )
    report.add(item, done)
    if done.text is None:
        return
    line, summary = parse(done.text)
    with driver.session(database=database) as s:
        s.run(
            f"MATCH (n:{LABEL[item.kind]} {{id: $id}}) "
            "SET n.summary = $summary, n.summary_line = $line, n.summary_hash = $hash, "
            "n.summary_model = coalesce($model, n.summary_model)",
            id=item.node_id,
            summary=summary,
            line=line,
            hash=done.input_hash,
            model=done.model,
        ).consume()
    log.info("summary %s %s", "reused for" if done.cached else "written for", item.node_id)


def host_reader(session: Session) -> ReadFile:
    """Read a README from the git host at the ingested commit (never stored), for runs
    outside a job, where there's no clone."""
    from sqlalchemy import select

    from steno.connectors.source import SourceError, read_file
    from steno.db.models import Connector, Repository

    def read(repo: str, commit: str | None, path: str) -> str | None:
        row = session.execute(
            select(Repository, Connector)
            .join(Connector, Repository.connector_id == Connector.id)
            .where(Repository.name == repo)
        ).first()
        if row is None or not commit:
            return None
        repository, connector = row
        try:
            return read_file(
                repository.clone_url, connector.kind, connector.credentials_ref, commit, path
            )
        except SourceError:
            return None

    return read
