"""Generated summaries (D143): written once per input, cached and logged, parsed into a line
and a summary, and never erased by the write stage."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from steno.db.models import LlmCall, LlmOutput
from steno.db.session import _session_factory
from steno.graph.delta import nulls_for
from steno.llm import generate
from steno.llm.providers import Fake
from steno.summaries.prompts import parse


@pytest.fixture
def session():
    """The dev Postgres, inside a transaction that's rolled back; skipped without it."""
    s = _session_factory()()
    try:
        s.execute(text("select 1"))
    except OperationalError:
        pytest.skip("Postgres isn't running (make up)")
    s.commit = s.flush  # type: ignore[method-assign]  # keep it all in the rolled-back transaction
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _gen(session, provider, prompt="Application: orders\nWays in: POST /orders", **kw):
    return generate(
        session, provider, node_id="app:test-summ", kind="summary:application", system="S",
        prompt=prompt, **kw,
    )  # fmt: skip


def test_unchanged_inputs_are_never_written_twice(session):
    llm = Fake()
    first = _gen(session, llm)
    again = _gen(session, llm)
    assert first.text == again.text and not first.cached and again.cached
    assert len(llm.calls) == 1
    calls = session.scalars(select(LlmCall).where(LlmCall.node_id == "app:test-summ")).all()
    assert [c.purpose for c in calls] == ["summary:application"]


def test_changed_inputs_write_again_and_only_the_newest_is_current(session):
    llm = Fake()
    _gen(session, llm)
    _gen(session, llm, prompt="Application: orders\nWays in: POST /orders, GET /orders")
    rows = session.scalars(
        select(LlmOutput).where(LlmOutput.node_id == "app:test-summ").order_by(LlmOutput.id)
    ).all()
    assert len(llm.calls) == 2
    assert [r.is_current for r in rows] == [False, True]
    # Back to the first inputs: its text comes back from the cache, current again
    back = _gen(session, llm)
    assert back.cached and len(llm.calls) == 2
    session.expire_all()
    assert [r.is_current for r in rows] == [True, False]


def test_a_dry_run_counts_and_writes_nothing(session):
    llm = Fake()
    done = _gen(session, llm, dry_run=True)
    assert done.text is None and done.input_tokens > 0 and not llm.calls


def test_the_answer_splits_into_a_line_and_a_summary():
    assert parse("LINE: Takes orders.\nSUMMARY: Takes and tracks orders.\nShips them.") == (
        "Takes orders.",
        "Takes and tracks orders. Ships them.",
    )
    # An answer that ignored the form keeps its text
    assert parse("Takes orders. Ships them.") == ("Takes orders.", "Takes orders. Ships them.")


def test_a_rewrite_of_a_node_keeps_its_summary():
    before = {"id": "app:1", "name": "orders", "summary": "…", "summary_line": "…", "old": 1}
    assert nulls_for(before, {"id": "app:1", "name": "orders"}) == {"old": None}
