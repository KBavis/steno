"""The job queue (D35): one job per repository at a time, and a limit on jobs running at once.
Runs against the dev Postgres inside a transaction that's rolled back; skipped without it."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from steno.config import get_settings
from steno.db.models import Connector, ConnectorKind, IngestionJob, JobMode, JobStatus, Repository
from steno.db.session import _session_factory
from steno.ingestion.queue import RepositoryBusy, claim_next, enqueue


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
