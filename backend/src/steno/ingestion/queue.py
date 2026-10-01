"""The work queue is the `ingestion_job` table (D35): no broker.

Workers claim the oldest queued job with `FOR UPDATE SKIP LOCKED`, so several
workers can poll at once without claiming the same job.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steno.db.models import IngestionJob, JobMode, JobStatus, JobTrigger


def enqueue(
    session: Session,
    repository_id: int,
    mode: JobMode,
    trigger: JobTrigger = JobTrigger.MANUAL,
) -> IngestionJob:
    job = IngestionJob(repository_id=repository_id, mode=mode, trigger=trigger)
    session.add(job)
    session.flush()
    session.refresh(job)  # load server defaults (queued_at)
    return job


def claim_next(session: Session) -> IngestionJob | None:
    """Claim one queued job and mark it running. The caller commits."""
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
