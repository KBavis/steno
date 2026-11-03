"""Writing a large graph: the transaction's size is recorded, and running out of Neo4j's
transaction memory fails once with a message that says what to raise (D59)."""

import pytest
from neo4j.exceptions import Neo4jError

from steno.graph import writer
from steno.graph.build import GraphPlan
from steno.graph.delta import Delta
from steno.graph.writer import GraphWriteTooLarge, _transaction_estimate_mb, _write_all


class _Result:
    def __init__(self, row):
        self._row = row

    def single(self):
        return self._row


class _Tx:
    def __init__(self, row=None, fail=None):
        self.row, self.fail, self.queries = row, fail, []

    def run(self, query, **_):
        self.queries.append(query)
        if self.fail:
            raise self.fail
        return _Result(self.row)


def _neo4j_error(code):
    # The driver builds the right subclass (TransientError, ClientError) from the code
    return Neo4jError._hydrate_neo4j(code=code, message="boom")


def test_the_transaction_size_is_read_in_megabytes():
    tx = _Tx({"bytes": 47_185_920})
    assert _transaction_estimate_mb(tx) == 45.0
    assert "steno-transaction-size" in tx.queries[0]  # picks out this query's transaction


def test_a_server_that_wont_say_gives_none_instead_of_failing_the_write():
    assert (
        _transaction_estimate_mb(_Tx(fail=_neo4j_error("Neo.ClientError.Security.Forbidden")))
        is None
    )
    assert _transaction_estimate_mb(_Tx({"bytes": None})) is None


def test_running_out_of_transaction_memory_says_what_to_raise(monkeypatch):
    def too_big(*_):
        raise _neo4j_error(writer.OUT_OF_MEMORY)

    monkeypatch.setattr(writer, "_write_changes", too_big)
    with pytest.raises(GraphWriteTooLarge) as caught:
        _write_all(_Tx(), GraphPlan(), "thingsboard")
    message = str(caught.value)
    assert "thingsboard" in message and "dbms.memory.transaction.total.max" in message
    assert not isinstance(caught.value, Neo4jError)  # the driver retries Neo4j's transient errors


def test_other_transient_errors_still_reach_the_driver_to_be_retried(monkeypatch):
    def deadlock(*_):
        raise _neo4j_error("Neo.TransientError.Transaction.DeadlockDetected")

    monkeypatch.setattr(writer, "_write_changes", deadlock)
    with pytest.raises(Neo4jError):
        _write_all(_Tx(), GraphPlan(), "thingsboard")


def test_a_finished_write_carries_its_transaction_size(monkeypatch):
    monkeypatch.setattr(writer, "_write_changes", lambda *_: Delta())
    delta = _write_all(_Tx({"bytes": 1_048_576}), GraphPlan(), "demo")
    assert delta.transaction_estimate_mb == 1.0
