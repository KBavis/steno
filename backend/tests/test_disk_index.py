"""The JVM helper's expressions and calls, indexed on disk instead of loaded whole."""

import json

from steno.resolvers.disk_index import CACHED_FUNCTIONS, HelperIndex


def _parts(tmp_path, exprs, calls):
    (tmp_path / "exprs.jsonl").write_text("\n".join(json.dumps(e) for e in exprs))
    (tmp_path / "calls.jsonl").write_text("\n".join(json.dumps([s, c]) for s, c in calls))
    return HelperIndex(tmp_path)


def _expr(file, sl, sc, el, ec, **more):
    return {"file": file, "sl": sl, "sc": sc, "el": el, "ec": ec, **more}


def test_an_expression_is_found_by_its_position_and_the_last_one_written_wins(tmp_path):
    index = _parts(
        tmp_path,
        [
            _expr("A.java", 3, 5, 3, 20, symbol="first"),
            _expr("A.java", 3, 5, 3, 20, symbol="second"),
            _expr("A.java", 3, 22, 3, 30, symbol="other"),
            _expr("B.java", 3, 5, 3, 20, symbol="b"),
        ],
        [],
    )
    assert index.expressions == 4
    assert index.expr("A.java", 3, 5, 3, 20)["symbol"] == "second"
    assert index.expr("A.java", 4, 5, 4, 20) is None
    assert [e["symbol"] for e in index.exprs_on("A.java", 3, 3)] == ["first", "second", "other"]
    assert [e["symbol"] for e in index.all_exprs()] == ["first", "second", "other", "b"]


def test_calls_are_read_by_symbol_and_iterate_in_the_helpers_order(tmp_path):
    calls = [("b#f()", [{"line": 1}]), ("a#g()", [{"line": 2}, {"line": 3}])]
    index = _parts(tmp_path, [], calls)
    assert index.calls.get("a#g()") == [{"line": 2}, {"line": 3}]
    assert index.calls.get("missing#x()", []) == []
    assert "b#f()" in index.calls and "missing#x()" not in index.calls
    assert len(index.calls) == 2
    assert list(index.calls) == ["b#f()", "a#g()"]
    assert list(index.calls.items()) == calls


def test_a_symbol_with_quotes_and_brackets_reads_back(tmp_path):
    symbol = 'a.B#f(java.util.Map<java.lang.String,int[]>,"x")'
    index = _parts(tmp_path, [], [(symbol, [{"callee": "c#d()"}])])
    assert index.calls[symbol] == [{"callee": "c#d()"}]


def test_the_cache_stays_bounded(tmp_path):
    n = CACHED_FUNCTIONS + 10
    index = _parts(tmp_path, [], [(f"s{i}", [{"line": i}]) for i in range(n)])
    for i in range(n):
        assert index.calls[f"s{i}"] == [{"line": i}]
    assert len(index.calls._cache) == CACHED_FUNCTIONS


def test_missing_parts_index_as_empty(tmp_path):
    index = HelperIndex(tmp_path)
    assert index.expressions == 0 and len(index.calls) == 0
    assert index.expr("A.java", 1, 1, 1, 2) is None
