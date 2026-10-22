"""The MCP tools' pure parts: folding a flow into an outline, and fitting a token budget."""

from steno.coverage.runs import count_by_application
from steno.mcp.tools import _completeness, _fold, _source_groups, _stops, fit


def _step(path: str, does: list[str] | None = None) -> dict:
    return {"path": path, "function": f"f{path}", **({"does": does} if does else {})}


STEPS = [
    _step("1"),
    _step("1.1", ["reads job"]),
    _step("1.2"),
    _step("1.2.1", ["writes job"]),
    _step("1.2.1.3", ["calls GitHub"]),
    _step("1.2.4"),
]


def test_deeper_steps_fold_into_the_shown_step_above():
    shown = {s["path"]: s for s in _fold(STEPS, 2, None)}
    assert list(shown) == ["1", "1.1", "1.2"]
    assert shown["1.2"]["inside"] == 3
    assert shown["1.2"]["inside_does"] == ["writes job", "calls GitHub"]


def test_under_opens_one_branch():
    shown = _fold(STEPS, 2, "1.2")
    assert [s["path"] for s in shown] == ["1.2", "1.2.1", "1.2.4"]
    assert shown[1]["inside"] == 1  # 1.2.1.3, two levels below 1.2


def test_a_long_answer_is_cut_and_says_so():
    result = {"steps": [{"n": i, "pad": "x" * 100} for i in range(50)], "next": ["a"]}
    out = fit(result, max_tokens=500)
    assert len(out["steps"]) < 50
    assert out["truncated"]["steps"].startswith(f"{50 - len(out['steps'])} more")
    assert out["next"] == ["a"]


def test_stops_fold_into_the_shown_step_above():
    """A call Steno couldn't follow stays visible however deep it is (D71)."""
    stop = "web.retrieve() at line 61: library type (org.springframework.web)"
    steps = [*STEPS[:3], {**STEPS[3], "stops": [stop]}, *STEPS[4:]]
    shown = {s["path"]: s for s in _fold(steps, 2, None)}
    assert shown["1.2"]["stops"] == [stop]


def test_stops_say_where_and_why():
    entry = {
        "unresolved": [
            {"call": "web.post().uri(…).retrieve()", "line": 61, "reason": "library type",
             "library": "org.springframework.web"},
            {"call": "session.commit()", "line": 70, "reason": "unknown type"},
        ]
    }  # fmt: skip
    assert _stops(entry, of="place") == [
        "in place: web.post().uri(…).retrieve() at line 61: library type (org.springframework.web)",
        "in place: session.commit() at line 70: unknown type",
    ]


def test_paths_match_whatever_their_parameters_are_called():
    from steno.mcp.tools import _path_shape

    assert _path_shape("/users/{id}/") == _path_shape("/users/{userId}") == "/users/{}"
    assert _path_shape("suggestion") == "/suggestion"


def test_a_base_url_from_config_keeps_its_key_and_the_path():
    from steno.extraction.facts import config_keys, split_url

    assert split_url("${suggestion-engine.base-url}suggestion") == (None, "/suggestion")
    assert split_url("${plaid.api.baseUrl}/link/token/create") == (None, "/link/token/create")
    assert config_keys("${plaid.api.baseUrl}/x/${other:default}") == ["other", "plaid.api.baseUrl"]


def test_completeness_never_claims_to_be_complete():
    """An answer says what it checked and what it couldn't, not that it's complete (D118)."""
    out = _completeness(["the code of 3 applications"], {}, {})
    assert out == {"checked": ["the code of 3 applications"], "not_checked": {}}
    assert "complete" not in out


def test_completeness_carries_unexplained_counts():
    out = _completeness([], {"calls_not_followed": "2 calls"}, {"users-svc": {"hooks": 2}})
    assert out["unexplained"] == {"users-svc": {"hooks": 2}}
    assert out["unexplained_note"]


def test_sources_group_citations_by_repository():
    """What a consumer would fetch: each repository's folders and why it matters (D119)."""
    cites = [
        {"claim": "POST /orders is handled by A", "repo": "orders", "commit": "a1b2c3d4e5f6",
         "file": "src/main/java/com/x/orders/A.java"},
        {"claim": "A writes orders", "repo": "orders", "commit": "a1b2c3d4e5f6",
         "file": "src/main/java/com/x/orders/api/B.java"},
        {"claim": "A reads config", "repo": "orders", "file": "src/main/resources/application.yml"},
        {"claim": "billing consumes it", "repo": "billing", "file": "Consumer.java"},
        {"claim": "no repository"},
    ]  # fmt: skip
    groups = _source_groups(cites)
    assert list(groups) == ["orders", "billing"]
    assert groups["orders"]["paths"] == ["src/main/java/com/x/orders/", "src/main/resources/"]
    assert groups["orders"]["commits"] == {"a1b2c3d4e5f6"}
    assert groups["orders"]["why"] == [
        "POST /orders is handled by A",
        "A writes orders",
        "A reads config",
    ]
    assert groups["billing"]["paths"] == ["Consumer.java"]


def test_unexplained_counts_items_per_application_in_words():
    items = [
        ("unknown_hook", {"users-svc": 3, "auth-svc": 1}),
        ("unknown_hook", {"users-svc": 1}),
        ("external_call", {"users-svc": 5}),
        ("library", {"other-svc": 1}),
    ]
    assert count_by_application(items, {"users-svc", "auth-svc"}) == {
        "auth-svc": {"framework hooks no rule explains": 1},
        "users-svc": {"I/O calls no rule explains": 1, "framework hooks no rule explains": 2},
    }
