"""The nightly run (D59; docs/ingestion.md §7, Nightly updates).

Once a night, queue a full run for every included repository whose default branch moved
past `last_ingested_sha`. Repositories that didn't change are skipped. Workers check whether
it's due on each poll; a `scheduled_run` row, locked with SKIP LOCKED, makes sure only one
worker runs it per night.
"""

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.connectors.git import CloneError, remote_head
from steno.db.models import (
    Connector,
    IngestionJob,
    JobMode,
    JobStatus,
    JobTrigger,
    Repository,
    RepositorySelection,
    ScheduledRun,
)
from steno.ingestion.queue import enqueue

log = logging.getLogger(__name__)
NAME = "nightly"


def last_slot(now: datetime, at: str) -> datetime:
    """The most recent nightly time at or before `now` (UTC)."""
    hour, minute = (int(x) for x in at.split(":"))
    slot = datetime.combine(now.date(), time(hour, minute), tzinfo=UTC)
    return slot if slot <= now else slot - timedelta(days=1)


def is_due(now: datetime, last_run: datetime | None, at: str) -> bool:
    return last_run is not None and last_run < last_slot(now, at)


def maybe_run(session: Session, now: datetime | None = None) -> dict[str, Any] | None:
    """Run the nightly enqueue if it's due and no other worker holds it. The caller commits.

    On the very first check there's no previous run, so it only records the time: a new
    deployment doesn't re-ingest everything the moment a worker starts.
    """
    settings = get_settings()
    if not settings.nightly_enabled:
        return None
    now = now or datetime.now(UTC)
    row = session.scalar(
        select(ScheduledRun).where(ScheduledRun.name == NAME).with_for_update(skip_locked=True)
    )
    if row is None:
        if session.get(ScheduledRun, NAME) is not None:
            return None  # another worker holds it
        session.add(ScheduledRun(name=NAME, last_run_at=now))
        return None
    if not is_due(now, row.last_run_at, settings.nightly_at):
        return None
    result = run(session)
    row.last_run_at = now
    return result


def run(session: Session) -> dict[str, Any]:
    """Queue a full run for every included repository whose default branch moved."""
    queued, unchanged, busy, failed = [], [], [], []
    repos = session.scalars(
        select(Repository).where(Repository.selection == RepositorySelection.INCLUDED)
    ).all()
    for repo in repos:
        if session.scalar(
            select(IngestionJob.id)
            .where(IngestionJob.repository_id == repo.id)
            .where(IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            .limit(1)
        ):
            busy.append(repo.name)
            continue
        connector = session.get_one(Connector, repo.connector_id)
        try:
            head = remote_head(
                repo.clone_url, repo.default_branch, connector.kind, connector.credentials_ref
            )
        except CloneError as exc:
            log.warning("nightly: can't read %s: %s", repo.name, exc)
            failed.append(repo.name)
            continue
        if head is not None and head == repo.last_ingested_sha:
            unchanged.append(repo.name)
            continue
        enqueue(session, repo.id, JobMode.FULL, JobTrigger.NIGHTLY)
        queued.append(repo.name)
    log.info(
        "nightly: queued %s, unchanged %s, busy %s, failed %s",
        len(queued),
        len(unchanged),
        len(busy),
        len(failed),
    )
    return {"queued": queued, "unchanged": unchanged, "busy": busy, "failed": failed}
