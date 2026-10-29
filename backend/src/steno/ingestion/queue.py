"""The work queue is the `ingestion_job` table (D35): no broker.

Workers claim the oldest queued job with `FOR UPDATE SKIP LOCKED`, so several workers can
poll at once without claiming the same job.

- **One job per repository at a time.** A unique index allows one queued-or-running job per
  repository, so a second run can't be queued while one is waiting or running: two runs would
  race on the repository's graph and change history.
- **Only what fits the machine** (D128). A job reserves the memory its repository needed last
  time; a worker claims it only when that fits what its machine has left (`resources.py`).
  `max_parallel_jobs` is a ceiling across every machine.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.db.models import IngestionJob, JobMode, JobStatus, JobTrigger
from steno.ingestion.resources import Machine, estimate, fits, reserved_mb

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


def claim_next(
    session: Session, host: str | None = None, machine: Machine | None = None
) -> IngestionJob | None:
    """Claim the oldest queued job and mark it running, unless `max_parallel_jobs` are already
    running, or (given this worker's `host` and `machine`) its memory doesn't fit what the
    machine has left. A job that doesn't fit waits at the head of the queue rather than let
    smaller ones pass, so a large repository is never starved. The caller commits."""
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
    if job is None:
        return None
    if host is not None and machine is not None:
        plan = estimate(session, job.repository_id, machine)
        reserved, here = reserved_mb(session, host)
        if not fits(plan.reserve_mb, machine, reserved, here):
            return None
        job.host = host
        job.memory_reserved_mb = plan.reserve_mb
        job.stats = {
            **(job.stats or {}),
            "resources": {
                "host": host,
                "reserved_mb": plan.reserve_mb,
                "jvm_heap_mb": plan.jvm_heap_mb,
                "batched": plan.batched,
                "estimated_from": plan.source,
                "machine_memory_mb": machine.memory_mb,
                "machine_available_mb": machine.available_mb,
                "cpus": machine.cpus,
                "jobs_already_running_here": here,
            },
        }
    job.status = JobStatus.RUNNING
    job.started_at = func.now()
    return job


def recover_interrupted(session: Session) -> list[int]:
    """Jobs still `running` with no worker running them (the worker was stopped mid-job): mark
    them failed, which also unlocks their repositories. Callers make sure no worker is running;
    a worker heartbeat would make this safe while workers run (later)."""
    jobs = list(
        session.scalars(select(IngestionJob).where(IngestionJob.status == JobStatus.RUNNING))
    )
    for job in jobs:
        job.status = JobStatus.FAILED
        job.finished_at = func.now()
        job.error = "interrupted: the worker running it stopped"
    return [job.id for job in jobs]
