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


def test_a_pattern_ignored_for_some_file_names_reopens_when_others_appear(session):
    from steno.db.models import IgnoredFile

    a = _repo(session, "pattern-test-scoped")
    fp = "unread_file:.yml@steno-test-scoped/"
    session.add(IgnoredFile(fingerprint=fp, name="docker-compose.local.yml"))
    p = CoveragePattern(fingerprint=fp, kind="config", signal="unread_file", label="x",
                        state=PatternState.IGNORED)  # fmt: skip
    session.add(p)
    session.flush()
    # The ignored name is filtered out before patterns are recorded; what's left is new
    it = Item("config", "unread_file", ".yml@steno-test-scoped/", "x")
    it.add({"file": "steno-test-scoped/deploy.yml", "function": "", "application": "svc"})
    record(session, a.id, 1, [it])
    assert p.state == PatternState.OPEN


def test_an_approved_or_rejected_pattern_a_rule_now_explains_is_covered(session):
    a = _repo(session, "pattern-test-explained")
    record(session, a.id, 1, [_hook(("A.java", "A#on()"))])
    p = _pattern(session)
    for state in (PatternState.APPROVED, PatternState.REJECTED):
        p.state = state
        record(session, a.id, 2, [])  # the approved rule (or a hand-written one) explains it
        assert p.state == PatternState.COVERED
        record(session, a.id, 3, [_hook(("A.java", "A#on()"))])
    p.state = PatternState.IGNORED
    record(session, a.id, 4, [])
    assert p.state == PatternState.IGNORED


def test_expected_and_proposed_are_triage_states_and_expected_stays(session):
    """D146, D147: expected (outside the organization) survives the pattern disappearing;
    proposed (a rule's pull request is open) becomes covered once a rule explains it."""
    from steno.coverage.patterns import triage

    a = _repo(session, "pattern-test-expected")
    record(session, a.id, 1, [_hook(("A.java", "A#on()"))])
    fp = "unknown_hook:com.acme.test.AcmeHandler"
    triage(session, [fp], PatternState.EXPECTED, "a vendor calls it")
    record(session, a.id, 2, [])
    assert _pattern(session).state == PatternState.EXPECTED
    record(session, a.id, 3, [_hook(("A.java", "A#on()"))])  # back: still expected
    assert _pattern(session).state == PatternState.EXPECTED
    triage(session, [fp], PatternState.PROPOSED, "https://github.com/x/pull/1")
    record(session, a.id, 4, [])  # the rule merged and explains it
    assert _pattern(session).state == PatternState.COVERED


def test_a_one_sided_join_is_triaged_by_creating_its_pattern(session):
    from steno.coverage.patterns import triage

    fp = "one_sided:interface:space:1:kafka:test-orders.created"
    assert triage(session, [fp], PatternState.EXPECTED, "a vendor consumes it") == 1
    p = session.scalar(select(CoveragePattern).where(CoveragePattern.fingerprint == fp))
    assert (p.signal, p.state) == ("one_sided", PatternState.EXPECTED)


def test_a_quiet_item_makes_a_quiet_pattern(session):
    a = _repo(session, "pattern-test-quiet")
    it = Item("config", "unread_file", ".xyz@flows/", "Unread files · *.xyz under flows/")
    it.add({"file": "flows/a.xyz", "function": "", "application": "svc"})
    it.quiet = True
    record(session, a.id, 1, [it])
    p = session.scalar(
        select(CoveragePattern).where(CoveragePattern.fingerprint == "unread_file:.xyz@flows/")
    )
    assert p.quiet is True
