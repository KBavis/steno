"""The routing block (D142) with the stand-in: which spaces, applications, and flows, from
search's signals; uncalibrated, so no bands; a shortlist when the flows can't be told apart."""

from steno.decisions.base import Choice, DecisionUnavailable, DisabledBackend, Noul
from steno.decisions.standin import StandInBackend
from steno.routing.route import _keep, band, route


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
    "MATCH (s:Space)": [{"id": "space:1", "name": "Shop", "line": "Selling things."},
                        {"id": "space:2", "name": "People", "line": "Staff."}],
    "MATCH (a:Application)-[:BELONGS_TO]->(s:Space)": [
        {"id": "app:orders", "name": "orders", "line": "Takes orders.", "space": "Shop"},
        {"id": "app:billing", "name": "billing", "line": "Charges cards.", "space": "Shop"},
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
    assert r["alternatives"]["spaces"][0]["name"] == "People"


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
