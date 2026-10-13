"""Search splits a query the way the full-text index splits names: a wildcard term skips the
index's analyzer, so `budgeting-app` kept whole would never match `budgeting-app-backend`."""

import pytest

from steno.graph.views import search_words


@pytest.mark.parametrize(
    ("query", "words"),
    [
        ("budgeting-app", ["budgeting", "app"]),
        ("POST /auth/register", ["post", "auth", "register"]),
        ("create_project", ["create_project"]),
        ("  JwtAuthenticationFilter ", ["jwtauthenticationfilter"]),
        ("-- / --", []),
    ],
)
def test_search_words(query: str, words: list[str]) -> None:
    assert search_words(query) == words
