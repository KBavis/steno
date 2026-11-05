"""Calls between repositories, by the service a host names: never by a config key or a path."""

from steno.claims.types import route_target
from steno.reconcile.links import Call, Endpoint, application_of, endpoint_for, plan

NAMES = {
    "shop-int-orders": {"s1"},
    "orders": {"s1"},
    "shop-ext-orders": {"s2"},
    "billing": {"s3"},
    "twice": {"s4", "s5"},
}


def _host(name: str) -> str:
    return f"{name}.${{ENV}}-shop.svc.cluster.local:8080"


def test_an_exact_name_or_alias_is_confirmed():
    assert application_of(_host("billing"), NAMES) == ("s3", "confirmed")
    assert application_of(_host("orders"), NAMES) == ("s1", "confirmed")
    assert application_of("billing", NAMES) == ("s3", "confirmed")


def test_a_name_with_a_runtime_part_is_inferred_only_when_one_application_fits():
    assert application_of(_host("${LOB}-int-orders"), NAMES) == ("s1", "inferred")
    assert application_of(_host("shop-${ROLE}-orders"), NAMES) is None  # s1 and s2 both fit
    assert application_of(_host("${a}-${b}"), NAMES) is None  # no fixed text to go on


def test_a_name_never_guesses():
    assert application_of(_host("twice"), NAMES) is None
    assert application_of(_host("nothing"), NAMES) is None
    assert application_of("api.example.com", NAMES) is None


def test_a_placeholder_with_a_dot_stays_part_of_the_name():
    assert route_target("shop-${kafka.env}-orders.ns.svc") == "shop-${kafka.env}-orders"


def test_an_endpoint_is_the_exact_one_else_the_only_template_that_fits():
    a = Endpoint("a", "s1", "GET", "/items")
    b = Endpoint("b", "s1", "GET", "/items/{itemId}")
    c = Endpoint("c", "s1", "POST", "/items")
    assert endpoint_for("GET", "/items?x=1", [a, b, c]) == a
    assert endpoint_for("post", "/items/", [a, b, c]) == c
    assert endpoint_for("GET", "/items/{id}", [a, b, c]) == b
    assert endpoint_for("GET", "/items/42", [a, b, c]) == b
    assert endpoint_for("DELETE", "/items", [a, b, c]) is None
    two = [b, Endpoint("d", "s1", "GET", "/items/{other}")]
    assert endpoint_for("GET", "/items/42", two) is None


def test_a_path_alone_never_links_a_call_whose_host_is_unknown_or_outside():
    endpoints = [Endpoint("e", "s3", "POST", "/transactions/sync")]
    calls = [
        Call("f1", "x", "POST", "${plaid.url}/transactions/sync"),
        Call("f2", "x", "POST", "https://api.plaid.com/transactions/sync"),
    ]
    assert plan(calls, NAMES, endpoints) == ([], {})


def test_a_call_links_to_the_named_services_endpoint_and_a_miss_keeps_the_service():
    endpoints = [Endpoint("e", "s3", "GET", "/invoices")]
    url = f"http://{_host('billing')}"
    links, systems = plan(
        [
            Call("f1", "sys", "GET", f"{url}/invoices"),
            Call("f2", "sys", "GET", f"{url}/not-there"),
        ],
        NAMES,
        endpoints,
    )
    assert [(link.source, link.endpoint, link.certainty) for link in links] == [
        ("f1", "e", "confirmed")
    ]
    assert systems == {"sys": "s3"}
