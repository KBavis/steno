"""A gateway's routes as claims (D132): what a route's target names, and its subjects."""

from steno.claims.types import route_target, subjects, validate


def test_a_route_target_names_an_application_unless_its_an_outside_host():
    assert route_target("lb://vets-service") == "vets-service"
    assert route_target("http://orders:8080/api") == "orders"
    assert route_target("http://orders.shop.svc.cluster.local:80") == "orders"
    assert route_target("https://api.example.com") is None
    assert route_target("http://localhost:8080") is None


def test_a_route_is_about_its_gateway_and_its_target():
    fields = {"gateway": "api-gateway", "path": "/api/vet/**", "target": "lb://vets-service"}
    assert validate("route", fields) == fields
    assert subjects("route", fields) == ["application:api-gateway", "application:vets-service"]
