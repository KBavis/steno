"""A flow's trace as stored: compact and compressed (D63, refined).

A trace lists every function a flow runs, once per call site, so a large flow repeats the same
functions thousands of times (ThingsBoard: 47,803 entries in one flow, 2.4 GB of trace JSON
across 1,565 flows). Everything about a function (its name, file, lines, doc, hash, effects,
the calls Steno couldn't follow) is stored once per flow; each call site is a short row:

    [parent row, ordinal under it, function, call line, flags, step, interceptor?]

Paths (`1.2.3`) are rebuilt from the parent rows. The JSON is zlib-compressed into bytes, which
Neo4j stores as a byte array. `decode` returns exactly the entries readers have always seen,
and still reads the old format (a JSON list) in graphs written before.
"""

import json
import zlib
from typing import Any

VERSION = 2
# Per call site: the bits that vary from one call of a function to the next
FLAGS = ("conditional", "in_loop", "async", "candidate", "repeat", "recursive", "unresolved")
# Per function: the same at every call site in the flow
FUNCTION = (
    "symbol",
    "bound_to",
    "name",
    "container",
    "file",
    "start_line",
    "end_line",
    "significant",
    "utility",
    "effects",
    "doc",
    "body_hash",
)


def encode(entries: list[dict[str, Any]]) -> bytes:
    functions: list[dict[str, Any]] = []
    index: dict[tuple[Any, ...], int] = {}
    rows: list[list[Any]] = []
    row_of: dict[str, int] = {}
    for e in entries:
        fn = {k: e[k] for k in FUNCTION if k in e}
        if "unresolved" in e:
            fn["unresolved"] = e["unresolved"]
        key = (e["symbol"], e.get("bound_to"))
        i = index.get(key)
        if i is None:
            i = index[key] = len(functions)
            functions.append(fn)
        elif "unresolved" in e and "unresolved" not in functions[i]:
            functions[i]["unresolved"] = e["unresolved"]
        parent, _, ordinal = e["path"].rpartition(".")
        flags = sum(1 << b for b, name in enumerate(FLAGS) if e.get(name))
        row = [row_of[parent] if parent else -1, int(ordinal), i, e.get("call_line") or 0, flags,
               e.get("step") or 0]  # fmt: skip
        if e.get("interceptor"):
            row.append(e["interceptor"])
        row_of[e["path"]] = len(rows)
        rows.append(row)
    text = json.dumps({"v": VERSION, "f": functions, "e": rows}, separators=(",", ":"))
    return zlib.compress(text.encode(), 6)


def decode(raw: Any) -> list[dict[str, Any]]:
    """The trace's entries, from either stored form; [] when there's none."""
    if raw is None:
        return []
    if isinstance(raw, str):  # the old form: a JSON list of entries
        return json.loads(raw)
    data = json.loads(zlib.decompress(bytes(raw)))
    functions: list[dict[str, Any]] = data["f"]
    entries: list[dict[str, Any]] = []
    for parent, ordinal, i, call_line, flags, step, *rest in data["e"]:
        fn = functions[i]
        path = f"{entries[parent]['path']}.{ordinal}" if parent >= 0 else str(ordinal)
        entry: dict[str, Any] = {"path": path}
        for k in FUNCTION:
            if k in fn:
                entry[k] = fn[k]
        if call_line:
            entry["call_line"] = call_line
        for b, name in enumerate(FLAGS):
            if flags & (1 << b):
                if name == "unresolved":
                    entry["unresolved"] = fn.get("unresolved", [])
                else:
                    entry[name] = True
        if step:
            entry["step"] = step
        if rest:
            entry["interceptor"] = rest[0]
        entries.append(entry)
    return entries


def length(raw: Any) -> int:
    """How many entries a stored trace has, without building them."""
    if raw is None:
        return 0
    if isinstance(raw, str):
        return len(json.loads(raw))
    return len(json.loads(zlib.decompress(bytes(raw)))["e"])
