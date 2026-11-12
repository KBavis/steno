"""The routing block with the stand-in: which spaces, applications, and flows, from
search's signals; uncalibrated, so no bands; a shortlist when the flows can't be told apart."""

from steno.decisions.base import (
    Answer,
    Choice,
    DecisionUnavailable,
    DisabledBackend,
    Noul,
)
from steno.decisions.standin import StandInBackend
from steno.routing.route import _keep, _scope_of, band, route


class _Driver:
    """Answers routing's graph reads from fixed rows."""

    def __init__(self, rows):
        self.rows = rows

    def session(self, database=None):
        driver = self

        class S:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def run(self, cypher, **params):
                key = next(k for k in driver.rows if k in cypher)
                rows = driver.rows[key]

                class R:
                    def data(self):
                        return rows(params) if callable(rows) else rows

                return R()

        return S()


ROWS = {
    "MATCH (f:Flow)-[:BELONGS_TO]->(a:Application) OPTIONAL": [
        {"id": "flow:o:DELETE /orders/{id}", "name": "DELETE /orders/{id}", "application": "orders",
         "application_id": "app:orders", "space": "Shop", "aliases": [],
         "digest": "DELETE /orders/{id} · orders\nDOC       Cancels an order."},
        {"id": "flow:o:GET /orders", "name": "GET /orders", "application": "orders",
         "application_id": "app:orders", "space": "Shop", "aliases": [],
         "digest": "GET /orders · orders\nDOC       Lists orders."},
        {"id": "flow:b:POST /charges", "name": "POST /charges", "application": "billing",
         "application_id": "app:billing", "space": "Shop", "aliases": [],
         "digest": "POST /charges · billing\nDOC       Charges a card."},
    ],
    "WHERE n:Table": [
        {"id": "table:orders", "name": "orders", "labels": ["Table"], "writers": ["POST /orders"],
         "readers": ["GET /orders"]},
        {"id": "topic:order-events", "name": "order-events", "labels": ["Interface", "KafkaTopic"],
         "writers": ["POST /orders"], "readers": []},
    ],
    "MATCH (s:Space)": [{"id": "space:1", "name": "Shop", "line": "Selling things."},
                        {"id": "space:2", "name": "People", "line": "Staff."}],
    "MATCH (a:Application) RETURN": [
        {"id": "app:orders", "name": "orders", "line": "Takes orders."},
        {"id": "app:billing", "name": "billing", "line": "Charges cards."},
    ],
    "MATCH (a:Application)-[:BELONGS_TO]->(s:Space)": [
        {"id": "app:orders", "name": "orders", "space": "Shop"},
        {"id": "app:billing", "name": "billing", "space": "Shop"},
    ],
    "WHERE f.id IN $ids": [],
    "RETURN a.name AS app": [
        {"app": "orders", "id": "flow:o:GET /orders", "name": "GET /orders",
         "digest": "GET /orders · orders\nDOC       Lists orders."},
        {"app": "orders", "id": "flow:o:DELETE /orders/{id}", "name": "DELETE /orders/{id}",
         "digest": "DELETE /orders/{id} · orders\nDOC       Cancels an order."},
    ],
}  # fmt: skip
HITS = [
    {"id": "flow:o:DELETE /orders/{id}", "kind": "flow", "name": "DELETE /orders/{id}",
     "application": "orders", "space": "Shop"},
    {"id": "flow:o:GET /orders", "kind": "flow", "name": "GET /orders", "application": "orders",
     "space": "Shop"},
    {"id": "flow:b:POST /charges", "kind": "flow", "name": "POST /charges",
     "application": "billing", "space": "Shop"},
]  # fmt: skip


def _route(hits=HITS, dropped=(), decider=None):
    return route(_Driver(ROWS), "neo4j", "how do I cancel an order", ["cancel", "order"],
                 list(hits), list(dropped), decider=decider or StandInBackend())  # fmt: skip


def test_the_stand_in_routes_by_where_the_matches_are():
    r = _route()
    assert r["backend"] == "stand_in" and r["confidence"] == "uncalibrated"
    assert [s["name"] for s in r["spaces"]] == ["Shop"]
    assert r["applications"][0]["name"] == "orders"
    assert r["flows"][0]["name"] == "DELETE /orders/{id}"
    # Uncalibrated: no bands anywhere
    assert all("band" not in x for k in ("spaces", "applications", "flows") for x in r[k])


def test_a_shortlist_when_words_were_dropped_ranked_by_the_question_words():
    r = _route(dropped=["cancel"])
    names = [f["name"] for f in r["shortlist"]["flows"]]
    assert r["shortlist"]["application"] == "orders"
    assert names[0] == "DELETE /orders/{id}"  # "Cancels an order": more of the words
    assert r["shortlist"]["flows"][0]["does"] == "Cancels an order."
    assert "shortlist" not in _route()


def test_switched_off_nothing_is_chosen():
    r = _route(decider=DisabledBackend())
    assert r["spaces"] == [] and r["applications"] == [] and r["flows"] == []


def test_close_calls_keep_both_branches():
    scored = [{"id": "a", "confidence": 0.45}, {"id": "b", "confidence": 0.4},
              {"id": "c", "confidence": 0.1}]  # fmt: skip
    assert [s["id"] for s in _keep(scored, threshold=0.5)] == ["a", "b"]
    assert band(0.95, calibrated=True) == "high" and band(0.95, calibrated=False) is None
    assert band(0.6, True) == "medium" and band(0.2, True) == "low"


class _Jev:
    """Scores flows by their digests' words, as a backend that reads would."""

    name = "jev"
    calibrated = False

    def __init__(self, scores):
        self.scores = scores

    def decide(self, decision, state, questions):
        if decision == "E1":
            return [Answer(True, self.scores.get(q.key.removeprefix("entity "), 0.1))
                    for q in questions]  # fmt: skip
        if decision == "Q4":
            return [Answer(True, self.scores.get(q.key.split(" do what")[0][5:], 0.1))
                    for q in questions]  # fmt: skip
        return [Answer(q.options[0], 1.0, {q.options[0]: 1.0}) for q in questions]


def test_jev_reads_every_flow_and_the_flows_decide_the_applications():
    r = _route(hits=[], decider=_Jev({"POST /charges": 0.8, "GET /orders": 0.6}))
    assert [f["name"] for f in r["flows"]] == ["POST /charges", "GET /orders"]
    assert [a["name"] for a in r["applications"]] == ["billing", "orders"]
    assert r["applications"][1]["confidence"] == 0.6
    assert [s["name"] for s in r["spaces"]] == ["Shop"]


def test_when_nothing_fits_the_answer_says_so():
    r = _route(hits=[], decider=_Jev({"GET /orders": 0.3}))
    assert r["flows"] == [] and r["applications"] == []
    assert r["nothing_fits"]["closest"][0]["name"] == "GET /orders"
    assert "rephrase" in r["nothing_fits"]["suggest"]


def test_several_close_flows_get_a_shortlist_with_what_each_does():
    r = _route(hits=[], decider=_Jev({"DELETE /orders/{id}": 0.7, "GET /orders": 0.6}))
    assert r["shortlist"]["flows"][0]["does"] == "Cancels an order."
    sure = _route(hits=[], decider=_Jev({"DELETE /orders/{id}": 0.95}))
    assert "shortlist" not in sure


def test_a_flow_named_in_the_question_is_a_fact():
    r = route(_Driver(ROWS), "neo4j", "what does GET /orders return", ["orders"], [], [],
              decider=_Jev({"GET /orders": 0.2}))  # fmt: skip
    assert r["flows"][0]["name"] == "GET /orders" and r["flows"][0]["confidence"] == 1.0


def test_the_trace_shows_every_candidate_and_why():
    trace = []
    route(_Driver(ROWS), "neo4j", "cancel", ["cancel"], [], [], decider=_Jev({}), trace=trace)
    assert trace[0]["step"] == "proposed" and trace[0]["candidates"] == 3
    flows = [step for step in trace if "flow" in step]
    assert "read every flow" in flows[0]["why"][0]
    assert any("entity" in step for step in trace)


def test_a_named_application_comes_first_and_the_rest_stay():
    scores = {"POST /charges": 0.9, "GET /orders": 0.6}
    r = route(_Driver(ROWS), "neo4j", "how does orders list things", ["orders"], [], [],
              decider=_Jev(scores))  # fmt: skip
    assert [f["name"] for f in r["flows"]] == ["GET /orders", "POST /charges"]
    assert r["named_in_question"] == ["orders"]
    assert [a["name"] for a in r["applications"]] == ["billing", "orders"]


def test_a_named_scope_with_no_fitting_flow_doesnt_outrank_one_that_fits():
    scores = {"POST /charges": 0.9, "GET /orders": 0.2}
    r = route(_Driver(ROWS), "neo4j", "orders and refunds", ["orders"], [], [],
              decider=_Jev(scores))  # fmt: skip
    assert r["flows"][0]["name"] == "POST /charges"


def test_a_common_word_isnt_a_fact_about_an_entity():
    r = route(_Driver(ROWS), "neo4j", "how are orders stored", ["orders"], [], [],
              decider=_Jev({"GET /orders": 0.8, "table:orders": 0.3}))  # fmt: skip
    assert "entities" not in r


def test_a_name_only_counts_as_whole_words():
    rows = {"application": "orders", "aliases": ["shop-orders"], "space": "Shop"}
    assert _scope_of(rows, "what about shop orders") == "application"
    assert _scope_of(rows, "reorders stock") is None
    assert _scope_of(rows, "something in the shop") == "space"
    assert _scope_of({"application": "api"}, "the api") is None


def test_topics_and_tables_are_found_like_flows():
    scores = {"GET /orders": 0.8, "topic:order-events": 0.7}
    r = _route(hits=[], decider=_Jev(scores))
    assert [(e["name"], e["kind"]) for e in r["entities"]][0][0] == "order-events"
    assert r["entities"][0]["confidence"] == 0.7
    assert all(e["name"] != "orders" for e in r["entities"])


def test_an_entity_named_in_the_question_is_a_fact():
    r = route(_Driver(ROWS), "neo4j", "who writes order-events", ["order"], [], [],
              decider=_Jev({"GET /orders": 0.8}))  # fmt: skip
    assert r["entities"][0]["name"] == "order-events" and r["entities"][0]["confidence"] == 1.0


def test_a_fitting_entity_means_something_fits():
    r = _route(hits=[], decider=_Jev({"table:orders": 0.8}))
    assert r["flows"] == [] and "nothing_fits" not in r
    assert r["entities"][0]["name"] == "orders"


def test_the_stand_in_reads_no_entities():
    assert "entities" not in _route()


def test_the_stand_in_answers_from_signals():
    [yes, pick] = StandInBackend().decide(
        "Q1",
        {"signals": {"Is it about Shop?": 0.8, "Which?": {"a": 3.0, "b": 1.0}}},
        [Noul("Is it about Shop?"), Choice("Which?", ("a", "b"))],
    )
    assert yes.value is True and yes.confidence == 0.8
    assert pick.value == "a" and pick.distribution == {"a": 0.75, "b": 0.25}


def test_when_jev_cant_answer_the_search_still_routes_and_says_why():
    class Down:
        name = "jev"

        def decide(self, decision, state, questions):
            raise DecisionUnavailable("can't reach it")

    r = _route(decider=Down())
    assert r["backend"] == "stand_in" and r["confidence"] == "uncalibrated"
    assert r["unavailable"] == "jev: can't reach it"
    assert r["applications"]
