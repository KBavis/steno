"""The MCP tools' pure parts: folding a flow into an outline, and fitting a token budget."""

from steno.mcp.tools import _fold, _stops, fit


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
