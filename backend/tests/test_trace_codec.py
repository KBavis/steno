"""Traces stored compactly (D63, refined): decoding gives back exactly the entries a flow was
built with, and the old JSON form still reads."""

import json

from steno.graph.trace import decode, encode, length

FN = {"name": "save", "container": "com.x.Repo", "file": "Repo.java", "start_line": 10,
      "end_line": 20, "doc": "Saves it.", "body_hash": "abc"}  # fmt: skip
TRACE = [
    {"path": "1", "symbol": "com.x.Filter#doFilter()", "name": "doFilter", "file": "F.java",
     "start_line": 1, "end_line": 5, "significant": True, "step": 1, "interceptor": "filter"},
    {"path": "2", "symbol": "com.x.Api#create()", "name": "create", "file": "Api.java",
     "start_line": 3, "end_line": 9, "significant": True, "step": 1,
     "unresolved": [{"call": "web.retrieve()", "line": 7, "reason": "library type"}]},
    {"path": "2.1", "symbol": "com.x.Repo#save()", **FN, "call_line": 5, "conditional": True,
     "significant": True, "step": 1,
     "effects": [{"edge": "WRITES_TO", "target": "table:orders", "operation": "insert"}]},
    {"path": "2.2", "symbol": "com.x.Repo#save()", **FN, "call_line": 6, "in_loop": True,
     "async": True, "repeat": True, "significant": True, "step": 2,
     "effects": [{"edge": "WRITES_TO", "target": "table:orders", "operation": "insert"}]},
    {"path": "2.2.1", "symbol": "com.x.Base#run()", "bound_to": "com.x.Diff", "name": "run",
     "file": "Base.java", "start_line": 1, "end_line": 2, "call_line": 3, "candidate": True,
     "recursive": True, "utility": True},
]  # fmt: skip


def test_decoding_gives_back_the_entries_exactly():
    assert decode(encode(TRACE)) == TRACE
    assert length(encode(TRACE)) == len(TRACE)


def test_the_old_form_still_reads():
    old = json.dumps(TRACE)
    assert decode(old) == TRACE
    assert length(old) == len(TRACE)
    assert decode(None) == []


def test_a_function_called_many_times_is_stored_once():
    calls = [{"path": "1", "symbol": "com.x.Api#create()", "name": "create", "file": "Api.java",
              "start_line": 1, "end_line": 50, "significant": True, "step": 1}]  # fmt: skip
    calls += [
        {
            "path": f"1.{i}",
            "symbol": "com.x.Repo#save()",
            **FN,
            "call_line": 10 + i,
            "repeat": i > 1,
        }  # fmt: skip
        for i in range(1, 2001)
    ]
    calls = [{k: v for k, v in e.items() if v is not False} for e in calls]
    stored = encode(calls)
    assert decode(stored) == calls
    assert len(stored) < len(json.dumps(calls)) / 20
