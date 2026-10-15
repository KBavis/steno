"""Only differences are written (D59), and they become the change history (D18)."""

import copy
import json

from steno.graph.build import GraphPlan
from steno.graph.delta import Existing, changes, diff, nulls_for

FLOW = "flow:demo:POST /x"
STEP = f"step:{FLOW}:svc.run"


def _plan(commit: str = "c1", line: int = 10, body: str = "aaa") -> GraphPlan:
    prov = {"repo": "demo", "ingestion_job": 1, "commit": commit, "last_seen": "t"}
    trace = [
        {"path": "1", "symbol": "api.x", "file": "api.py", "start_line": 1, "body_hash": "e"},
        {"path": "1.1", "symbol": "svc.run", "file": "svc.py", "start_line": line,
         "call_line": 3, "body_hash": body, "step": 1},
    ]  # fmt: skip
    p = GraphPlan()
    p.node(FLOW, ["Flow", "Searchable"], name="POST /x", trace=json.dumps(trace), **prov)
    p.node(STEP, ["Step"], name="run", file="svc.py", start_line=line, **prov)
    p.node("table:s::t", ["Table", "Searchable"], name="t", shared=True, **prov)
    p.edge("WRITES_TO", STEP, "table:s::t", operation="insert", **prov)
    return p


def _graph_holding(plan: GraphPlan) -> Existing:
    """The graph as it is after writing `plan`: Neo4j returns each node's `id` and
    `first_seen` among its properties."""
    e = Existing()
    for n in plan.nodes.values():
        stored = {**copy.deepcopy(n.props), "id": n.id, "first_seen": "t0"}
        e.nodes[n.id] = (n.labels, stored)
        e.repo_nodes.add(n.id)
    for key, edge in plan.edges.items():
        e.edges[key] = copy.deepcopy(edge.props)
    return e


def test_a_rewrite_never_removes_the_id():
    """A changed node is written with the properties that disappeared set to null; its id
    and first_seen must never be among them."""
    delta = diff(_plan(body="bbb"), _graph_holding(_plan()))
    ((_, before, after),) = delta.nodes
    assert nulls_for(before, after) == {}


def test_rewriting_the_same_code_changes_nothing():
    delta = diff(_plan(), _graph_holding(_plan()))
    assert delta.nodes == [] and delta.edges == []
    assert changes(delta) == []


def test_a_new_commit_with_moved_lines_changes_nothing():
    """Unchanged facts keep the commit their lines were read at, so citations stay exact."""
    delta = diff(_plan(commit="c2", line=40), _graph_holding(_plan()))
    assert delta.nodes == [] and delta.edges == []


def test_changed_code_rewrites_the_trace_and_logs_which_function_changed():
    delta = diff(_plan(body="bbb"), _graph_holding(_plan()))
    assert [nid for nid, _, _ in delta.nodes] == [FLOW]
    rows = changes(delta)
    assert rows == [
        {
            "fact_id": FLOW,
            "fact_type": "Trace",
            "change": "modified",
            "before": None,
            "after": {"modified": ["svc.run"]},
        }
    ]


def test_facts_gone_from_the_code_are_removed_but_shared_ones_wait():
    old = _plan()
    new = _plan()
    del new.nodes[STEP]
    del new.edges[("WRITES_TO", STEP, "table:s::t")]
    del new.nodes["table:s::t"]
    delta = diff(new, _graph_holding(old))
    # The step goes (its edge with it); the shared table is left for orphan cleanup
    assert delta.nodes_to_remove() == [STEP]
    assert delta.edges_to_remove() == []
    assert {r["fact_id"] for r in changes(delta)} == {STEP}


def test_a_shared_node_another_repository_wrote_first_stays_with_it():
    """A topic both a producer's and a consumer's repositories name: the first writer keeps
    it, so the two runs don't take turns rewriting it (and the change history stays quiet)."""
    prov = {"ingestion_job": 2, "commit": "c", "last_seen": "t"}
    topic = "kafkatopic:payment.settled"
    first = GraphPlan()
    first.node(
        topic, ["Interface", "KafkaTopic"], name="payment.settled", shared=True,
        repo="payments", extracted_by="kafka-template-send", **prov,
    )  # fmt: skip
    second = GraphPlan()
    second.node(
        topic, ["Interface", "KafkaTopic"], name="payment.settled", shared=True,
        repo="recon", extracted_by="kafka-listener", **prov,
    )  # fmt: skip
    graph = _graph_holding(first)
    graph.repo_nodes.clear()  # read by id: the recon repository doesn't own it
    assert diff(second, graph).nodes == []
    # A real change is still written, keeping its first writer
    second.nodes[topic].props["partitions"] = 6
    ((_, _, after),) = diff(second, graph).nodes
    assert (after["repo"], after["partitions"]) == ("payments", 6)
