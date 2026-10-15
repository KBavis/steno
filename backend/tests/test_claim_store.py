"""The claim store (claims-and-reconcile.md §5) and name resolution (identity.md §3), against
the dev Postgres inside a transaction that's rolled back; skipped without it."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from steno.claims.store import active, store_claims
from steno.db.models import Connector, ConnectorKind, Repository
from steno.db.session import _session_factory
from steno.extraction.facts import ClaimFact, Origin
from steno.identity.identify import Deployable, identify
from steno.identity.names import Name
from steno.identity.registry import SqlRegistry, resolve


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


def _grant(app: str, topic: str, line: int) -> ClaimFact:
    return ClaimFact(
        "may_consume",
        {"application": app, "channel": {"KafkaTopic": topic}},
        Origin("acl", "acme-xld", "topics/acl.yaml", line),
        "PLATFORM",
        "acme-xld",
    )


def test_each_run_replaces_its_claims(session):
    repo = _repo(session, "claims-test-xld")
    first = store_claims(
        session, repo.id, 1, "c1", [_grant("recon-svc", "t1", 3), _grant("ledger-svc", "t1", 4)]
    )
    assert (first.added, first.removed) == (2, 0)
    assert "channel:kafkatopic:t1" in first.touched

    # Same claims, moved down a line: kept, not re-added
    again = store_claims(
        session, repo.id, 2, "c2", [_grant("recon-svc", "t1", 5), _grant("ledger-svc", "t1", 6)]
    )
    assert (again.added, again.removed, again.kept) == (0, 0, 2) and not again.touched

    # The ledger grant is deleted: removed (kept for history), and its subjects are touched
    gone = store_claims(session, repo.id, 3, "c3", [_grant("recon-svc", "t1", 5)])
    assert (gone.added, gone.removed) == (0, 1)
    assert "application:ledger-svc" in gone.touched
    assert [c.fields["application"] for c in active(session, {"channel:kafkatopic:t1"})] == [
        "recon-svc"
    ]

    # And back again: the same row comes back
    back = store_claims(
        session, repo.id, 4, "c4", [_grant("recon-svc", "t1", 5), _grant("ledger-svc", "t1", 6)]
    )
    assert (back.added, back.removed) == (1, 0)


def test_code_claims_are_not_stored(session):
    repo = _repo(session, "claims-test-code")
    name = ClaimFact(
        "name",
        {"application": "users", "name": "users", "slot": "runtime"},
        Origin("spring-application-name", "steno-pack-spring-boot", "application.yml", 3),
        "CODE",
        "steno-pack-spring-boot",
    )
    assert store_claims(session, repo.id, 1, None, [name]).added == 0


def test_names_resolve_by_canonical_name_or_exact_alias(session):
    repo = _repo(session, "claims-test-app")
    names = [
        Name("claims-test-app", "path", "CODE"),
        Name("ledger-service-zz", "build_artifact", "CODE"),
        Name("ledger-zz", "runtime", "CODE"),
    ]
    ids, _ = identify(SqlRegistry(session), repo.id, [Deployable(".", names)], 1)
    steno_id = ids["."].steno_id
    assert resolve(session, "ledger-service-zz") == steno_id  # canonical
    assert resolve(session, "ledger-zz") == steno_id  # an exact alias
    assert resolve(session, "ledger") is None  # never a partial match
