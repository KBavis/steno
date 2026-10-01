"""The ingestion pipeline for one repository (docs/ingestion.md §2).

Each stage is timed and recorded in `ingestion_stage`; together those rows are the
dry-run report (time per stage, graph size, projected LLM cost).

Every stage below is a stub that records itself and returns no metrics. They get
filled in one at a time.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from steno.db.models import (
    IngestionJob,
    IngestionStage,
    JobMode,
    Repository,
    StageName,
    StageStatus,
)

log = logging.getLogger(__name__)


@dataclass
class JobContext:
    job: IngestionJob
    repository: Repository
    workspace: Path | None = None
    # Facts handed from one stage to the next (raw facts, call graph, flows, ...)
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def dry_run(self) -> bool:
        return self.job.mode == JobMode.DRY_RUN


StageFn = Callable[[JobContext], dict[str, Any]]


def clone(ctx: JobContext) -> dict[str, Any]:
    """Clone the repository at `to_sha` into a temporary workspace."""
    return {}


def deps(ctx: JobContext) -> dict[str, Any]:
    """Fetch dependency JARs (e.g. `mvn dependency:copy-dependencies`) for symbol resolution."""
    return {}


def parse(ctx: JobContext) -> dict[str, Any]:
    """Pass 1, per file: run enabled rule packs to emit raw structural facts."""
    return {}


def resolve(ctx: JobContext) -> dict[str, Any]:
    """Pass 2–3: symbol + DI resolution (JVM helper), config placeholders, stubs."""
    return {}


def flows(ctx: JobContext) -> dict[str, Any]:
    """Build the call graph and derive every flow from every entry point."""
    return {}


def write(ctx: JobContext) -> dict[str, Any]:
    """MERGE facts into Neo4j by stable ID, remove stale ones, record the delta."""
    return {}


def cards(ctx: JobContext) -> dict[str, Any]:
    """Generate LLM purposes and narratives. In a dry run: count tokens and project cost only."""
    return {}


STAGES: list[tuple[StageName, StageFn]] = [
    (StageName.CLONE, clone),
    (StageName.DEPS, deps),
    (StageName.PARSE, parse),
    (StageName.RESOLVE, resolve),
    (StageName.FLOWS, flows),
    (StageName.WRITE, write),
    (StageName.CARDS, cards),
]


def run_job(session: Session, job: IngestionJob) -> None:
    """Run every stage in order, committing each stage's row so progress is visible."""
    repository = session.get_one(Repository, job.repository_id)
    ctx = JobContext(job=job, repository=repository)

    for name, fn in STAGES:
        stage = IngestionStage(
            job_id=job.id, stage=name, status=StageStatus.RUNNING, started_at=_now()
        )
        session.add(stage)
        session.commit()

        started = time.perf_counter()
        try:
            stage.metrics = {**fn(ctx), "seconds": round(time.perf_counter() - started, 3)}
            stage.status = StageStatus.SUCCEEDED
        except Exception:
            stage.status = StageStatus.FAILED
            raise
        finally:
            stage.finished_at = _now()
            session.commit()
        log.info("job %s: %s %s", job.id, name, stage.status)


def _now() -> datetime:
    return datetime.now(UTC)
