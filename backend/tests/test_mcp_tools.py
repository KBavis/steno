"""The MCP tools' pure parts: folding a flow into an outline, and fitting a token budget."""

from steno.mcp.tools import _fold, fit


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
