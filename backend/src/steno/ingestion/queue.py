"""The work queue is the `ingestion_job` table (D35): no broker.

Workers claim the oldest queued job with `FOR UPDATE SKIP LOCKED`, so several workers can
poll at once without claiming the same job.

- **One job per repository at a time.** A unique index allows one queued-or-running job per
  repository, so a second run can't be queued while one is waiting or running: two runs would
  race on the repository's graph and change history.
- **A limit on jobs running at once**, across every worker (`max_parallel_jobs`). Each job
  holds a clone and, for Java, a JVM, so the limit is what bounds memory and disk.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.db.models import IngestionJob, JobMode, JobStatus, JobTrigger

# Postgres advisory lock that serializes claims, so the running-jobs limit can't be overshot
CLAIM_LOCK = 0x5734_0001


class RepositoryBusy(Exception):
    """The repository already has a job queued or running."""

    def __init__(self, job: IngestionJob):
        self.job = job
        super().__init__(f"job {job.id} is already {job.status.value} for this repository")


def active_job(session: Session, repository_id: int) -> IngestionJob | None:
    return session.scalar(
        select(IngestionJob).where(
            IngestionJob.repository_id == repository_id,
            IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    )


def enqueue(
    session: Session,
    repository_id: int,
    mode: JobMode,
    trigger: JobTrigger = JobTrigger.MANUAL,
) -> IngestionJob:
    """Queue a job, unless the repository already has one queued or running (RepositoryBusy)."""
    existing = active_job(session, repository_id)
    if existing is not None:
        raise RepositoryBusy(existing)
    job = IngestionJob(repository_id=repository_id, mode=mode, trigger=trigger)
    try:
        with session.begin_nested():  # the index decides when two requests race
            session.add(job)
            session.flush()
    except IntegrityError:
        existing = active_job(session, repository_id)
        if existing is None:
            raise
        raise RepositoryBusy(existing) from None
    session.refresh(job)  # load server defaults (queued_at)
    return job


def claim_next(session: Session) -> IngestionJob | None:
    """Claim one queued job and mark it running, unless `max_parallel_jobs` are already
    running. The caller commits."""
    session.execute(select(func.pg_advisory_xact_lock(CLAIM_LOCK)))
    running = session.scalar(
        select(func.count())
        .select_from(IngestionJob)
        .where(IngestionJob.status == JobStatus.RUNNING)
    )
    if (running or 0) >= get_settings().max_parallel_jobs:
        return None
    job = session.scalar(
        select(IngestionJob)
        .where(IngestionJob.status == JobStatus.QUEUED)
        .order_by(IngestionJob.queued_at, IngestionJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is not None:
        job.status = JobStatus.RUNNING
        job.started_at = func.now()
    return job
