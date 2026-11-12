"""Each flow's digest: deterministic, bounded however large the flow is, and written on
every flow by the build."""

from test_graph_build import FLOW, _app, _plan

from steno.graph import trace as trace_codec
from steno.graph.build import GraphPlan
from steno.graph.digest import FUNCTIONS, LIMIT, _Index, flow_digest

F = "flow:shop:POST /orders"


def _shop(tables: int = 1, functions: int = 3, **flow: object) -> GraphPlan:
    """A flow in `shop` that writes `tables` tables and runs `functions` functions, the first
    one a step."""
    plan = GraphPlan()
    plan.node("app:shop", ["Application"], name="shop")
    plan.node("app:billing", ["Application"], name="billing")
    trace = [
        {
            "path": str(i + 1),
            "symbol": f"shop.OrderService.f{i}",
            "container": "shop.OrderService",
            "name": f"f{i}",
            "file": "OrderService.java",
            "start_line": i,
            "end_line": i,
            **({"step": 1} if i == 0 else {}),
        }
        for i in range(functions)
    ]
    plan.node(
        F,
        ["Flow", "Searchable"],
        name="POST /orders",
        trigger="POST /orders",
        trigger_kind="http",
        trace=trace_codec.encode(trace),
        **flow,
    )
    plan.edge("BELONGS_TO", F, "app:shop")
    plan.node("endpoint:shop:POST:/orders", ["Interface", "HttpEndpoint"], name="POST /orders")
    plan.edge("STARTS", "endpoint:shop:POST:/orders", F)
    plan.node("entity:shop:CreateOrder", ["Entity"], name="shop.api.CreateOrder")
    plan.edge(
        "USES_ENTITY", "endpoint:shop:POST:/orders", "entity:shop:CreateOrder", role="request"
    )
    for i in range(tables):
        plan.node(f"table:t{i:03}", ["Table"], name=f"t{i:03}")
        plan.edge("WRITES_TO", F, f"table:t{i:03}", rollup=True, operation="insert")
    return plan


def test_every_flow_the_build_writes_has_a_digest(tmp_path):
    plan = _plan(_app(tmp_path))
    digest = plan.nodes[FLOW].props["digest"]
    lines = digest.splitlines()
    assert lines[0] == "POST /projects/ · demo"
    assert lines[1].split() == ["TRIGGER", "http", "POST", "/projects/"]
    assert any(x.startswith("WRITES") and "project (insert)" in x for x in lines)
    # Nearest the entry first: the entry, then what it calls directly, in call order
    functions = next(x for x in lines if x.startswith("FUNCTIONS")).split(None, 1)[1]
    assert functions.startswith("create_project, ProjectService.describe, ProjectService.create")
    assert "fmt" in functions
    assert all("digest" in n.props for n in plan.nodes.values() if "Flow" in n.labels)


def test_a_huge_flow_still_has_a_short_digest():
    small = flow_digest(_shop(tables=2, functions=5), F)
    huge = flow_digest(_shop(tables=300, functions=2000), F)
    writes = next(x for x in huge.splitlines() if x.startswith("WRITES"))
    functions = next(x for x in huge.splitlines() if x.startswith("FUNCTIONS"))
    assert writes.count("(insert)") == LIMIT and writes.endswith(f"(+{300 - LIMIT} more)")
    assert functions.count(",") == FUNCTIONS - 1
    assert functions.endswith(f"(+{2000 - FUNCTIONS} more)")
    # Bounded by construction: the line count never grows, and the size stays near the small one
    assert len(huge.splitlines()) == len(small.splitlines())
    assert len(huge) < 4 * len(small)


def test_calls_name_the_application_that_serves_them_first():
    plan = _shop()
    plan.node("endpoint:billing:POST:/charges", ["Interface", "HttpEndpoint"], name="POST /charges")
    plan.edge("BELONGS_TO", "endpoint:billing:POST:/charges", "app:billing")
    plan.node("ext:stripe", ["ExternalSystem"], name="Stripe")
    plan.node("ext:unknown", ["ExternalSystem"], name="Unknown host · Client", stub=True)
    for target in ("ext:unknown", "ext:stripe", "endpoint:billing:POST:/charges"):
        plan.edge("CALLS", F, target, rollup=True)
    calls = next(x for x in flow_digest(plan, F).splitlines() if x.startswith("CALLS"))
    assert calls.split(None, 1)[1] == "POST /charges (billing), Stripe, Unknown host · Client"


def test_entities_come_from_its_payloads_and_its_tables():
    plan = _shop()
    plan.node("entity:shop:Order", ["Entity"], name="shop.model.Order")
    plan.edge("MAPS_TO", "entity:shop:Order", "table:t000")
    entities = next(x for x in flow_digest(plan, F).splitlines() if x.startswith("ENTITIES"))
    assert entities.split(None, 1)[1] == "CreateOrder (request), Order"


def test_what_steno_couldnt_see_is_said():
    plain = flow_digest(_shop(), F)
    assert "NOTE" not in plain
    gaps = flow_digest(_shop(unresolved_calls=3, incomplete=True), F)
    assert "NOTE      3 calls Steno couldn't follow: the flow may do more" in gaps
    inferred = flow_digest(_shop(inferred=True, framework="Filter"), F)
    assert "TRIGGER   unknown (inferred entry point, called by Filter)" in inferred


def test_the_same_flow_always_gets_the_same_digest():
    assert flow_digest(_shop(tables=20, functions=40), F) == flow_digest(
        _shop(tables=20, functions=40), F
    )


def _line(digest: str, word: str) -> str | None:
    return next((x.split(None, 1)[1] for x in digest.splitlines() if x.startswith(word)), None)


def test_doc_is_what_the_entry_and_its_nearest_steps_say():
    plan = _shop(functions=3)
    docs = {
        "shop.OrderService.f0": "Places an order.",
        "shop.OrderService.f1": "Opens a session.",  # depth 1 too here: the trace is flat
    }
    digest = flow_digest(plan, F, _Index(plan, docs))
    assert _line(digest, "DOC") == "Places an order. · Opens a session."
    assert _line(flow_digest(plan, F), "DOC") is None  # no docs: no line


def test_unique_tells_apart_flows_that_share_most_of_their_code():
    plan = _shop(tables=2, functions=3)
    other = "flow:shop:POST /orders/bulk"
    plan.node(
        other,
        ["Flow"],
        name="POST /orders/bulk",
        trace=trace_codec.encode(
            [{"path": "1", "symbol": "shop.OrderService.f1", "name": "f1", "file": "x"}]
        ),
    )
    plan.edge("BELONGS_TO", other, "app:shop")
    plan.edge("WRITES_TO", other, "table:t000", rollup=True)
    # t000 and f1 are shared; t001, f0, and f2 are this flow's own
    assert _line(flow_digest(plan, F), "UNIQUE") == "t001, OrderService.f0, OrderService.f2"
    # The application's only flow: nothing to tell apart
    assert _line(flow_digest(_shop(tables=2), F), "UNIQUE") is None


def test_interceptors_are_left_out_of_what_the_flow_does():
    plan = _shop(functions=0)
    trace = [
        {"path": "1", "symbol": "sec.JwtFilter.doFilter", "container": "sec.JwtFilter",
         "name": "doFilter", "file": "F.java", "interceptor": "filter"},
        {"path": "1.1", "symbol": "sec.Jwt.validate", "container": "sec.Jwt", "name": "validate",
         "file": "F.java"},
        {"path": "2", "symbol": "shop.OrderService.place", "container": "shop.OrderService",
         "name": "place", "file": "O.java", "step": 1},
    ]  # fmt: skip
    plan.nodes[F].props["trace"] = trace_codec.encode(trace)
    docs = {
        "sec.JwtFilter.doFilter": "Filter incoming requests.",
        "shop.OrderService.place": "Places an order.",
    }
    digest = flow_digest(plan, F, _Index(plan, docs))
    assert _line(digest, "DOC") == "Places an order."
    assert _line(digest, "FUNCTIONS") == "OrderService.place"
