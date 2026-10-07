"""The ingestion worker: claims jobs from Postgres and runs them.

Separate from the API/MCP process so heavy work never slows queries (DESIGN_DOC §6).
"""

import logging
import signal
import time
from datetime import UTC, datetime

from steno.config import get_settings
from steno.db.models import IngestionJob, JobStatus
from steno.db.session import session_scope
from steno.ingestion import nightly
from steno.ingestion.pipeline import run_job
from steno.ingestion.queue import claim_next

log = logging.getLogger(__name__)


def run_once() -> bool:
    """Claim and run one job. Returns False if the queue was empty."""
    with session_scope() as session:
        job = claim_next(session)
        if job is None:
            return False
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


def check_schedule() -> None:
    """Queue the nightly runs if they're due (D59)."""
    try:
        with session_scope() as session:
            nightly.maybe_run(session)
    except Exception:
        log.exception("nightly: check failed")


def run_forever() -> None:
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
        if time.monotonic() - last_check >= 60:
            check_schedule()
            last_check = time.monotonic()
        if not run_once():
            time.sleep(poll)
