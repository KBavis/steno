"""The rule author's worklist from the command line: read, show, and mark patterns."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from steno.cli import app
from steno.coverage.collect import Item
from steno.coverage.patterns import record
from steno.db.models import Connector, ConnectorKind, CoveragePattern, PatternState, Repository
from steno.db.session import _session_factory, session_scope

FP = "internal_library:com.acmecli.orders"


@pytest.fixture
def seeded():
    s = _session_factory()()
    try:
        s.execute(text("select 1"))
    except OperationalError:
        pytest.skip("Postgres isn't running (make up)")
    c = Connector(name="test-cli", kind=ConnectorKind.GITHUB, base_url="https://x")
    s.add(c)
    s.flush()
    r = Repository(connector_id=c.id, name="cli-test-repo", clone_url="https://x/cli.git")
    s.add(r)
    s.flush()
    it = Item("library", "internal_library", "com.acmecli.orders", "Internal library · x")
    it.add({"file": "A.java", "function": "A#go()", "application": "svc", "line": 3})
    record(s, r.id, 1, [it])
    s.commit()
    try:
        yield
    finally:
        with session_scope() as cleanup:
            cleanup.execute(text("DELETE FROM coverage_pattern WHERE fingerprint = :f"), {"f": FP})
            cleanup.execute(text("DELETE FROM repository WHERE name = 'cli-test-repo'"))
            cleanup.execute(text("DELETE FROM connector WHERE name = 'test-cli'"))
        s.close()


def test_show_and_mark_a_pattern(seeded):
    runner = CliRunner()
    shown = runner.invoke(app, ["coverage", "show", FP])
    assert shown.exit_code == 0, shown.output
    assert "cli-test-repo/A.java" in shown.output
    marked = runner.invoke(
        app, ["coverage", "mark", FP, "--state", "ignored", "--note", "a logging helper"]
    )
    assert marked.exit_code == 0, marked.output
    with session_scope() as s:
        p = s.scalar(select(CoveragePattern).where(CoveragePattern.fingerprint == FP))
        assert (p.state, p.note) == (PatternState.IGNORED, "a logging helper")


def test_mark_refuses_a_state_only_steno_sets(seeded):
    out = CliRunner().invoke(app, ["coverage", "mark", FP, "--state", "covered", "--note", "x"])
    assert out.exit_code != 0
