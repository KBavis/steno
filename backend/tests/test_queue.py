"""The job queue (D35): one job per repository at a time, and a limit on jobs running at once.
Runs against the dev Postgres inside a transaction that's rolled back; skipped without it."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from steno.config import get_settings
from steno.db.models import Connector, ConnectorKind, IngestionJob, JobMode, JobStatus, Repository
from steno.db.session import _session_factory
from steno.ingestion.queue import RepositoryBusy, claim_next, enqueue, recover_interrupted


@pytest.fixture
def session():
    s = _session_factory()()
    try:
        s.execute(text("select 1"))
    except OperationalError:
        pytest.skip("Postgres isn't running (make up)")
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _repo(session, name: str) -> Repository:
    connector = Connector(name=f"test-{name}", kind=ConnectorKind.GITHUB, base_url="https://x")
    session.add(connector)
    session.flush()
    repo = Repository(connector_id=connector.id, name=name, clone_url=f"https://x/{name}.git")
    session.add(repo)
    session.flush()
    return repo


def test_one_job_per_repository_at_a_time(session):
    repo = _repo(session, "queue-test-a")
    first = enqueue(session, repo.id, JobMode.DRY_RUN)
    with pytest.raises(RepositoryBusy) as busy:
        enqueue(session, repo.id, JobMode.FULL)
    assert busy.value.job.id == first.id
    first.status = JobStatus.RUNNING  # still busy while it runs
    with pytest.raises(RepositoryBusy):
        enqueue(session, repo.id, JobMode.DRY_RUN)
    first.status = JobStatus.SUCCEEDED  # done: a new one may be queued
    session.flush()
    assert enqueue(session, repo.id, JobMode.DRY_RUN).id != first.id


def test_the_index_holds_even_when_the_check_is_skipped(session):
    repo = _repo(session, "queue-test-b")
    session.add(IngestionJob(repository_id=repo.id, mode=JobMode.DRY_RUN, trigger="manual"))
    session.flush()
    with pytest.raises(Exception):  # noqa: B017 - the unique index itself refuses it
        with session.begin_nested():
            session.add(IngestionJob(repository_id=repo.id, mode=JobMode.FULL, trigger="manual"))
            session.flush()


def test_no_claim_beyond_the_parallel_limit(session, monkeypatch):
    running = session.scalar(text("select count(*) from ingestion_job where status = 'running'"))
    monkeypatch.setattr(get_settings(), "max_parallel_jobs", running)
    repo = _repo(session, "queue-test-c")
    enqueue(session, repo.id, JobMode.DRY_RUN)
    assert claim_next(session) is None  # the limit is reached: the job waits


def test_interrupted_jobs_unlock_their_repository(session):
    repo = _repo(session, "queue-test-d")
    job = enqueue(session, repo.id, JobMode.DRY_RUN)
    job.status = JobStatus.RUNNING  # its worker was stopped mid-job
    session.flush()
    assert job.id in recover_interrupted(session)
    assert job.status == JobStatus.FAILED and job.error.startswith("interrupted")
    assert enqueue(session, repo.id, JobMode.DRY_RUN).id != job.id  # unlocked


def test_a_job_waits_until_its_memory_fits_this_machine(session):
    """D128: with a job already running here, the next waits when its reservation doesn't fit;
    alone on the machine, it starts."""
    from steno.ingestion.resources import Machine

    busy = _repo(session, "queue-test-e")
    running = enqueue(session, busy.id, JobMode.DRY_RUN)
    running.status = JobStatus.RUNNING
    running.host = "queue-test-host"
    running.memory_reserved_mb = 3000
    repo = _repo(session, "queue-test-f")
    enqueue(session, repo.id, JobMode.DRY_RUN)
    session.flush()
    small = Machine(memory_mb=5000, available_mb=4000, cpus=8)  # budget 4000: 3000 + 3072 won't fit
    for other in session.query(IngestionJob).filter(IngestionJob.status == JobStatus.QUEUED):
        if other.repository_id != repo.id:
            other.queued_at = other.queued_at.replace(year=2100)  # ours is the oldest
    session.flush()
    assert claim_next(session, "queue-test-host", small) is None
    running.status = JobStatus.SUCCEEDED
    session.flush()
    job = claim_next(session, "queue-test-host", small)
    assert job is not None and job.repository_id == repo.id
    assert job.memory_reserved_mb == get_settings().job_memory_default_mb
    assert job.stats["resources"]["estimated_from"].startswith("default")


def test_after_running_out_of_jvm_memory_the_next_run_gets_more_and_batches(session):
    """A repository too big for the defaults recovers instead of failing every night (D128)."""
    from steno.ingestion.resources import Machine, estimate

    repo = _repo(session, "queue-test-g")
    failed = enqueue(session, repo.id, JobMode.DRY_RUN)
    failed.status = JobStatus.FAILED
    failed.error = (
        "RuntimeError: symbol resolution failed: Java resolver failed: Exception in thread "
        '"main" java.lang.OutOfMemoryError: Java heap space'
    )
    failed.stats = {"resources": {"jvm_heap_mb": 2048}}
    session.flush()
    plan = estimate(session, repo.id, Machine(memory_mb=16000, available_mb=12000, cpus=8))
    assert plan.batched and plan.jvm_heap_mb == 4096
    assert plan.reserve_mb == 4096 + get_settings().job_memory_default_mb
    small = estimate(session, repo.id, Machine(memory_mb=7000, available_mb=5000, cpus=8))
    assert small.jvm_heap_mb == 5600 - get_settings().job_memory_default_mb  # within the budget
