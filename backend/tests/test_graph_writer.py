"""Writing a graph to Neo4j in bounded transactions (D135): the order, the size of a batch,
halving a batch Neo4j can't hold, and what a failure leaves behind. A fake session stands in
for Neo4j, so none of this touches a real graph."""

import pytest
from neo4j.exceptions import Neo4jError

from steno.graph import writer
from steno.graph.build import GraphPlan
from steno.graph.delta import Delta, Existing, diff
from steno.graph.writer import (
    GraphWriteIncomplete,
    _orphan_candidates,
    _Run,
    apply_delta,
    plan_batches,
    plan_removals,
)


def _plan(flows: int = 3, steps: int = 4, trace: int = 1000) -> GraphPlan:
    plan = GraphPlan()
    plan.node("app:a", ["Application"], name="shop", repo="r")
    plan.node("table:t", ["Table", "Searchable"], name="t", repo="r")  # shared
    for f in range(flows):
        fid = f"flow:a:GET /{f}"
        plan.node(fid, ["Flow"], name=f"GET /{f}", trace=b"x" * trace, repo="r", last_seen="n")
        plan.edge("BELONGS_TO", fid, "app:a")
        previous = None
        for s in range(steps):
            sid = f"step:{fid}:fn{s}"
            plan.node(sid, ["Step"], symbol=f"fn{s}", repo="r", last_seen="n")
            plan.edge("BELONGS_TO", sid, fid)
            if previous:
                plan.edge("NEXT", previous, sid)
            previous = sid
        plan.edge("WRITES_TO", f"step:{fid}:fn0", "table:t")
    return plan


def _delta(plan: GraphPlan, existing: Existing | None = None) -> tuple[Delta, Existing]:
    existing = existing or Existing()
    return diff(plan, existing), existing


def _error(code: str) -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code=code, message="boom")


class _Result:
    def __init__(self, count: int):
        self.count = count

    def single(self):
        return {"c": self.count}

    def consume(self):
        return None

    def data(self):
        return [{"src": "a", "dst": "b"}]


class _Session:
    """Answers like Neo4j: each batch writes all its rows, unless told to run out of memory
    (`oom_over` rows), fail (`fail`), or write fewer edges than sent (`drop_edges`)."""

    def __init__(self, oom_over=None, fail=None, drop_edges=0):
        self.oom_over, self.fail, self.drop_edges = oom_over, fail, drop_edges
        self.sent: list[tuple[str, list]] = []  # the statements that committed
        self.markers: list[dict] = []
        self.attempts = 0

    def run(self, query, **params):
        rows = params.get("rows")
        if "GraphWrite" in query:
            self.markers.append(params["props"])
            return _Result(0)
        self.attempts += 1
        if self.oom_over and rows and len(rows) > self.oom_over and "MERGE (n:" in query:
            raise _error(writer.OUT_OF_MEMORY)
        if self.fail:
            err = self.fail(query, rows)
            if err:
                raise err
        self.sent.append((query, rows))
        count = len(rows or [])
        if "MERGE (a)-" in query:
            count = max(count - self.drop_edges, 0)
        return _Result(count)


def _kinds(session: _Session) -> list[str]:
    def kind(q: str) -> str:
        if "MERGE (n:" in q:
            return "nodes"
        if "MERGE (a)-" in q:
            return "edges"
        if "DETACH DELETE" in q:
            return "delete"
        if "DELETE r" in q:
            return "unlink"
        if "REMOVE n:" in q:
            return "unlabel"
        return "orphans"

    return [kind(q) for q, _ in session.sent]


def test_both_ends_of_an_edge_exist_when_it_is_written():
    plan = _plan(flows=6, steps=5)
    delta, _ = _delta(plan)
    batches = plan_batches(plan, delta, chunk_bytes=3000, node_rows=50, edge_rows=40)
    assert len({b.label for b in batches}) >= 1 and len(batches) > 6
    written: set[str] = set()
    for b in batches:
        if b.kind == "nodes":
            written.update(b.ids)
        else:
            for _, src, dst in b.ids:
                assert src in written and dst in written, (b.kind, src, dst)
    assert written == delta.nodes_to_write()
    sent_edges = {k for b in batches if b.kind == "edges" for k in b.ids}
    assert sent_edges == delta.edges_to_write()


def test_a_flow_and_its_steps_travel_together_and_batches_stay_in_budget():
    plan = _plan(flows=10, steps=3, trace=4000)  # a flow with its steps weighs about 4.1 KB
    delta, _ = _delta(plan)
    batches = plan_batches(plan, delta, chunk_bytes=10_000, node_rows=100, edge_rows=100)
    node_batches = [b for b in batches if b.kind == "nodes" and b.label != "skeleton"]
    for b in node_batches:
        if b.shape == ("Flow",):
            assert sum(len(plan.nodes[n].props["trace"]) for n in b.ids) <= 10_000
    flows_in = {b.shape: b for b in node_batches}
    assert ("Flow",) in flows_in and ("Step",) in flows_in
    # the skeleton (application, table) comes before any flow
    assert [b.label for b in batches][:1] == ["skeleton"]
    assert all(b.label == "shop" for b in node_batches)  # reported by application


def test_removals_are_planned_apart_from_the_writes_and_come_last():
    plan = _plan(flows=1)
    existing = Existing()
    existing.nodes["step:gone"] = (["Step"], {"repo": "r"})
    existing.repo_nodes.add("step:gone")
    # an edge between two nodes that stay, which the plan no longer has
    existing.edges[("READS_FROM", "step:flow:a:GET /0:fn1", "table:t")] = {}
    delta, _ = _delta(plan, existing)
    writes = plan_batches(plan, delta, chunk_bytes=10**6, node_rows=100, edge_rows=100)
    removals = plan_removals(delta, node_rows=100, edge_rows=100)
    assert {b.kind for b in writes} == {"nodes", "edges"}
    assert {b.kind for b in removals} == {"unlink", "delete"}

    session = _Session()
    apply_delta(session, plan, delta, existing, "r")
    kinds = _kinds(session)
    assert kinds.index("unlink") > max(i for i, k in enumerate(kinds) if k in ("nodes", "edges"))
    assert kinds[-1] == "orphans"
    assert [m["state"] for m in session.markers if "state" in m] == ["writing", "complete"]


def test_a_batch_neo4j_cant_hold_is_halved_until_it_fits():
    plan = _plan(flows=4, steps=4)
    delta, existing = _delta(plan)
    session = _Session(oom_over=3)
    report = apply_delta(session, plan, delta, existing, "r")
    assert report.splits > 0 and not report.failed
    sent = [r["id"] for q, rows in session.sent if "MERGE (n:" in q for r in rows]
    assert sorted(sent) == sorted(delta.nodes_to_write())  # every node exactly once
    assert max(len(rows) for q, rows in session.sent if "MERGE (n:" in q) <= 3


def test_one_row_too_big_for_neo4j_fails_that_batch_not_the_run():
    plan = _plan(flows=2, steps=2)
    delta, existing = _delta(plan)
    session = _Session(oom_over=0)  # nothing fits, not even one row
    session.oom_over = 0
    session.run = _only_flows_too_big(session)
    with pytest.raises(GraphWriteIncomplete) as caught:
        apply_delta(session, plan, delta, existing, "r")
    failed = caught.value.report.failed
    assert failed and "GraphWriteTooLarge" in failed[0]["error"]
    assert "raise server.memory.heap.max_size" in failed[0]["error"]


def _only_flows_too_big(session):
    original = session.run

    def run(query, **params):
        rows = params.get("rows") or []
        if "MERGE (n:Flow" in query and rows:
            raise _error(writer.OUT_OF_MEMORY)
        return original(query, **params)

    return run


def test_a_failed_batch_leaves_the_rest_written_nothing_removed_and_the_repository_marked():
    plan = _plan(flows=4, steps=2)
    existing = Existing()
    existing.nodes["step:gone"] = (["Step"], {"repo": "r"})
    existing.repo_nodes.add("step:gone")
    delta, _ = _delta(plan, existing)
    bad = "flow:a:GET /2"

    def fail(query, rows):
        if rows and any(r.get("id") == bad for r in rows):
            return _error("Neo.ClientError.Statement.ArgumentError")

    session = _Session(fail=fail)
    done: list[Delta] = []
    with pytest.raises(GraphWriteIncomplete) as caught:
        apply_delta(session, plan, delta, existing, "r", on_written=done.append)

    assert "only partly written" in str(caught.value) and "next run" in str(caught.value)
    assert "delete" not in _kinds(session)  # a hole is worse than a stale fact
    assert caught.value.report.removals_skipped
    sent_edges = [k for q, rows in session.sent if "MERGE (a)-" in q for k in rows]
    assert all(bad not in (e["src"], e["dst"]) for e in sent_edges)  # nor edges to it
    written = {n for d in done for n, _, _ in d.nodes}
    assert bad not in written and written  # history only for what committed
    last = [m for m in session.markers if "state" in m][-1]
    assert last["state"] == "incomplete" and last["failed"]


def test_the_history_callback_sees_every_change_exactly_once():
    plan = _plan(flows=3, steps=3)
    delta, existing = _delta(plan)
    done: list[Delta] = []
    apply_delta(_Session(oom_over=5), plan, delta, existing, "r", on_written=done.append)
    nodes = [n for d in done for n, _, _ in d.nodes]
    edges = [e for d in done for e, _, _ in d.edges]
    assert sorted(nodes) == sorted(delta.nodes_to_write())
    assert sorted(edges) == sorted(delta.edges_to_write())
    assert all(nid in d.labels for d in done for nid, _, _ in d.nodes)  # changes() needs them


def test_edges_that_wrote_nothing_are_reported_not_silent():
    plan = _plan(flows=1, steps=2)
    delta, existing = _delta(plan)
    session = _Session(drop_edges=1)
    report = apply_delta(session, plan, delta, existing, "r")
    assert report.edges_missing > 0 and report.missing_samples
    assert report.as_dict()["edges_missing"] == report.edges_missing
    complete = [m for m in session.markers if m.get("state") == "complete"][-1]
    assert complete["edges_missing"] == report.edges_missing


def test_a_broken_database_stops_the_write_at_once():
    plan = _plan(flows=4, steps=2)
    delta, existing = _delta(plan)

    def fail(query, rows):
        return _error("Neo.DatabaseError.General.UnknownError")

    session = _Session(fail=fail)
    with pytest.raises(GraphWriteIncomplete) as caught:
        apply_delta(session, plan, delta, existing, "r")
    assert len(caught.value.report.failed) == 1  # it did not try the other batches


def test_a_deadlock_is_tried_again_and_a_shared_node_keeps_its_first_owner():
    plan = _plan(flows=1, steps=1)
    delta, existing = _delta(plan)
    calls = {"n": 0}

    def fail(query, rows):
        calls["n"] += 1
        if calls["n"] == 1:
            return _error("Neo.TransientError.Transaction.DeadlockDetected")

    session = _Session(fail=fail)
    monkey = writer.time.sleep
    writer.time.sleep = lambda _: None
    try:
        report = apply_delta(session, plan, delta, existing, "r")
    finally:
        writer.time.sleep = monkey
    assert report.retries == 1 and not report.failed

    run = _Run(session, plan, delta, existing, "r", None)
    row = run._node_row("table:t")
    assert row["owner"] == {"repo": "r"} and "repo" not in row["props"]  # decided in the database
    assert run._node_row("app:a")["owner"] == {}  # not shared: its repo is an ordinary property
    query = next(q for q, _ in session.sent if "MERGE (n:Table" in q)
    assert "n.repo IS NULL OR n.repo IN [row.owner.repo, $reconcile]" in query


def test_only_shared_nodes_this_write_stopped_referencing_are_candidates_for_cleanup():
    plan = _plan(flows=1, steps=1)
    existing = Existing()
    for nid, labels, props in (
        ("table:old", ["Table"], {"repo": "r"}),  # ours, no longer in the plan: a candidate
        ("table:other", ["Table"], {"repo": "someone-else"}),  # another repository's: not ours
        ("step:old", ["Step"], {"repo": "r"}),  # not shared: removed as a node, not cleaned up
    ):
        existing.nodes[nid] = (labels, props)
    existing.nodes["table:kept"] = (["Table"], {"repo": "someone-else"})
    existing.repo_nodes.update({"table:old", "step:old"})
    # step:old is removed, so this edge goes with it: table:kept may now be unreferenced
    existing.edges[("READS_FROM", "step:old", "table:kept")] = {}
    delta, _ = _delta(plan, existing)
    delta.labels["table:kept"] = ["Table"]
    assert _orphan_candidates(delta, existing) == ["table:kept", "table:old"]


def test_an_abort_is_never_reported_as_a_finished_write():
    plan = _plan(flows=2, steps=2)
    delta, existing = _delta(plan)
    original = _Run.go
    seen = {"n": 0}

    def stop_early(self, batch):
        seen["n"] += 1
        if seen["n"] > 2:
            raise writer._Abort
        return original(self, batch)

    session = _Session()
    _Run.go = stop_early
    try:
        with pytest.raises(GraphWriteIncomplete):
            apply_delta(session, plan, delta, existing, "r")
    finally:
        _Run.go = original
    assert [m["state"] for m in session.markers if "state" in m][-1] == "incomplete"
    assert "delete" not in _kinds(session)
