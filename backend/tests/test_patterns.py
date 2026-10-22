"""Coverage items become patterns across the organization (D121): reach is the current state,
new code is what a run saw first, and a pattern no longer occurring is covered."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from steno.coverage.collect import Item
from steno.coverage.patterns import package_root, record, score
from steno.db.models import (
    Connector,
    ConnectorKind,
    CoverageOccurrence,
    CoveragePattern,
    CoverageStatus,
    PatternState,
    Repository,
)
from steno.db.session import _session_factory


def test_package_root_names_the_library_not_the_class():
    assert package_root("com.acme.messaging.handler.AcmeHandler") == "com.acme.messaging"
    assert package_root("com.acme.Bus#publish(java.lang.String)") == "com.acme"
    assert package_root("AcmeHandler") is None


def test_reach_ranks_repositories_before_occurrences():
    assert score("unknown_hook", 2, 2, 1) > score("unknown_hook", 1, 1, 500)
    assert score("unknown_hook", 1, 1, 1) > score("version_unknown", 1, 1, 1)


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


def _repo(session, name):
    c = Connector(name=f"test-{name}", kind=ConnectorKind.GITHUB, base_url="https://x")
    session.add(c)
    session.flush()
    r = Repository(connector_id=c.id, name=name, clone_url=f"https://x/{name}.git")
    session.add(r)
    session.flush()
    return r


def _hook(*places):
    it = Item("unmet_join", "unknown_hook", "com.acme.test.AcmeHandler", "Framework hook · x")
    for file, fn in places:
        it.add({"file": file, "function": fn, "application": "svc", "line": 1})
    return it


def _pattern(session):
    return session.scalar(
        select(CoveragePattern).where(
            CoveragePattern.fingerprint == "unknown_hook:com.acme.test.AcmeHandler"
        )
    )


def test_reach_is_the_current_state_and_new_code_is_a_difference(session):
    a, b = _repo(session, "pattern-test-a"), _repo(session, "pattern-test-b")
    first = record(session, a.id, 1, [_hook(("A.java", "A#on()"), ("B.java", "B#on()"))])
    assert first["new_occurrences"] == 2
    p = _pattern(session)
    assert (p.repositories, p.occurrences, p.owner, p.state) == (1, 2, "com.acme.test", "open")

    # The same code again: nothing new, nothing grows
    again = record(session, a.id, 2, [_hook(("A.java", "A#on()"), ("B.java", "B#on()"))])
    assert again["new_occurrences"] == 0 and _pattern(session).occurrences == 2

    # Another repository, and one place in the first moved away
    record(session, b.id, 3, [_hook(("C.java", "C#on()"))])
    record(session, a.id, 4, [_hook(("A.java", "A#on()"))])
    p = _pattern(session)
    assert (p.repositories, p.occurrences) == (2, 2)
    firsts = sorted(
        o.first_seen_job
        for o in session.scalars(
            select(CoverageOccurrence).where(CoverageOccurrence.pattern_id == p.id)
        )
    )
    assert firsts == [1, 3]


def test_a_pattern_no_longer_seen_is_covered_and_comes_back_open(session):
    a = _repo(session, "pattern-test-c")
    record(session, a.id, 1, [_hook(("A.java", "A#on()"))])
    record(session, a.id, 2, [])  # a rule explains it now
    assert _pattern(session).state == PatternState.COVERED
    record(session, a.id, 3, [_hook(("A.java", "A#on()"))])
    assert _pattern(session).state == PatternState.OPEN


def test_ignoring_an_item_ignores_its_pattern(session):
    a = _repo(session, "pattern-test-d")
    it = _hook(("A.java", "A#on()"))
    record(session, a.id, 1, [it], {it.fingerprint: CoverageStatus.IGNORED})
    assert _pattern(session).state == PatternState.IGNORED


def test_triage_ignores_and_reopens_whole_patterns(session):
    from steno.coverage.patterns import triage

    a = _repo(session, "pattern-test-e")
    record(session, a.id, 1, [_hook(("A.java", "A#on()"))])
    fp = "unknown_hook:com.acme.test.AcmeHandler"
    assert triage(session, [fp], PatternState.IGNORED, "logging helper, not wiring") == 1
    p = _pattern(session)
    assert (p.state, p.note) == (PatternState.IGNORED, "logging helper, not wiring")
    triage(session, [fp], PatternState.OPEN, "it is wiring after all")
    assert _pattern(session).state == PatternState.OPEN
    with pytest.raises(ValueError):
        triage(session, [fp], PatternState.APPROVED)
