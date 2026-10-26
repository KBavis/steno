"""The ingestion worker: claims jobs from Postgres and runs them.

Separate from the API/MCP process so heavy work never slows queries (docs/how-it-works/README.md).
"""

import logging
import multiprocessing
import os
import signal
import time
from datetime import UTC, datetime

from steno.config import get_settings
from steno.db.models import IngestionJob, JobStatus
from steno.db.session import session_scope
from steno.ingestion import nightly
from steno.ingestion.pipeline import run_job
from steno.ingestion.queue import claim_next
from steno.ingestion.resources import Machine, budget_mb, host, machine

log = logging.getLogger(__name__)


def run_once() -> bool:
    """Claim and run one ingestion job, or else one queued curation run. Returns False if
    there was nothing to do."""
    with session_scope() as session:
        job = claim_next(session, host(), machine())
        if job is None:
            return run_curation()
        session.commit()
        job_id = job.id

    log.info("job %s: started", job_id)
    with session_scope() as session:
        job = session.get_one(IngestionJob, job_id)
        try:
            run_job(session, job)
            job.status = JobStatus.SUCCEEDED
        except Exception as exc:
            session.rollback()
            job = session.get_one(IngestionJob, job_id)
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            log.exception("job %s: failed", job_id)
        job.finished_at = datetime.now(UTC)
    log.info("job %s: %s", job_id, job.status)
    return True


def run_curation() -> bool:
    """Draft one pattern of a rule curation run (D120), if there's one to claim. Drafting is
    LLM work, kept out of the API process like ingestion; each worker process drafts one
    pattern at a time, so a run's patterns are drafted in parallel across workers."""
    from steno.curation.run import claim, draft_one

    with session_scope() as session:
        work = claim(session)
        if work is None:
            return False
        try:
            draft_one(session, work)
        except Exception:
            log.exception("curation run %s: stopped", work.run_id)
    return True


def check_schedule() -> None:
    """Queue the nightly runs if they're due (D59)."""
    try:
        with session_scope() as session:
            nightly.maybe_run(session)
    except Exception:
        log.exception("nightly: check failed")


def run_forever(schedule: bool = True) -> None:
    """Claim and run jobs until stopped. `schedule`: also queue the nightly runs when due
    (one process in a pool does it)."""
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True
        log.info("worker: stopping after the current job")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    poll = get_settings().worker_poll_seconds
    log.info("worker: polling every %ss", poll)
    last_check = 0.0
    while not stopping:
        if schedule and time.monotonic() - last_check >= 60:
            check_schedule()
            last_check = time.monotonic()
        if not run_once():
            time.sleep(poll)


def processes_for(m: Machine) -> int:
    """Worker processes when none are configured: one per CPU, up to 8. Idle ones only poll;
    what runs at once is decided by memory when each claims (D128)."""
    return get_settings().worker_processes or min(m.cpus, 8)


def run_pool(processes: int) -> None:
    """Run `processes` workers side by side, each claiming one job at a time, and only when
    the job fits this machine's memory (D128). `max_parallel_jobs` caps jobs across every
    machine. Ctrl+C (or SIGTERM) stops each worker after its current job."""
    ctx = multiprocessing.get_context("spawn")
    pool = [
        ctx.Process(target=run_forever, args=(i == 0,), name=f"worker-{i + 1}")
        for i in range(processes)
    ]
    for p in pool:
        p.start()
    m = machine()
    log.info(
        "worker: %s processes on %s (%s MB, %s MB of it for jobs, %s CPUs)",
        processes,
        host(),
        m.memory_mb,
        budget_mb(m),
        m.cpus,
    )

    def stop(*_: object) -> None:
        for p in pool:
            if p.is_alive() and p.pid:
                os.kill(p.pid, signal.SIGTERM)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    for p in pool:
        p.join()
