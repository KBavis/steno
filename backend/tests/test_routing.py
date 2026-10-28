"""Routing a request through a gateway's routes (D132), at query time."""

from steno.graph.routing import inside_path, matches, outside_path, serves, takes

VETS = {"path": "/api/vet/**", "strip_prefix": 2}


def test_spring_path_patterns():
    assert matches("/api/vet/**", "/api/vet/vets") and matches("/api/vet/**", "/api/vet")
    assert not matches("/api/vet/**", "/api/visit/x")
    assert matches("/owners/{id}/pets/*", "/owners/7/pets/3")
    assert not matches("/owners/{id}", "/owners/7/pets")


def test_strip_prefix_both_ways():
    assert inside_path(VETS, "/api/vet/vets") == "/vets"
    assert inside_path(VETS, "/api/visit/x") is None
    assert outside_path(VETS, "/vets") == "/api/vet/vets"
    assert outside_path(VETS, "/owners/{id}") == "/api/vet/owners/{id}"


def test_rewrite_and_prefix():
    route = {"path": "/shop/**", "rewrite": "/shop/(?<segment>.*), /${segment}", "prefix": "/v1"}
    assert inside_path(route, "/shop/orders/7") == "/v1/orders/7"
    assert outside_path(route, "/v1/orders") is None  # a regex can't be reversed
    assert outside_path({"path": "/svc/**", "prefix": "/v1"}, "/v1/svc/a") == "/svc/a"


def test_methods_and_endpoint_templates():
    assert takes({"methods": "GET,POST"}, "get") and not takes({"methods": "GET"}, "DELETE")
    assert takes({}, "DELETE")
    assert serves("/owners/{ownerId}", "/owners/7") and not serves("/owners", "/owners/7")
