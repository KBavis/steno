"""The coverage report's signals: what no rule explained (docs/ingestion.md §4, Coverage report).

Each signal is a place a fact could hide. Everything here is deterministic and read from the
run that just happened: the engine's facts and dropped matches, the symbol resolver's view of
the code, and the graph plan's notes (effects no flow reaches, calls whose host is unknown).

Items are keyed by (kind, target), so the same gap in another run, or another repository,
is the same item: that's how triage carries over and how the org-wide report adds up (D62).
"""

import ast
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from steno.extraction.facts import Extraction
from steno.graph.build import GraphPlan
from steno.resolvers.python import FunctionInfo, PythonResolver
from steno.rule_packs.packs import Pack

SAMPLES = 5

# Libraries whose calls usually reach the network, a store, or a broker. With IO_VERBS, the
# stand-in for Jev I8 ("does this call look like I/O?"): a library call named like I/O
# (`session.execute`, `client.post`) is worth a rule if no rule explains it. Constructors and
# query builders (`httpx.AsyncClient()`, `select(...)`) aren't I/O themselves.
IO_PACKAGES = {
    "aiohttp", "aiokafka", "anthropic", "asyncpg", "atlassian", "boto3", "botocore", "chromadb",
    "confluent_kafka", "elasticsearch", "ftplib", "github", "google", "grpc", "httpx", "jira",
    "kafka", "langchain", "llama_index", "motor", "neo4j", "ollama", "openai", "paramiko",
    "pika", "psycopg", "psycopg2", "pymongo", "redis", "requests", "slack_sdk", "smtplib",
    "socket", "sqlalchemy", "urllib", "urllib3", "websockets",
}  # fmt: skip
IO_VERBS = {
    "add", "add_all", "call", "chat", "commit", "complete", "consume", "create", "delete",
    "download", "embed", "execute", "fetch", "fetchall", "generate", "get", "insert", "invoke",
    "merge", "patch", "post", "produce", "publish", "put", "query", "request", "search", "send",
    "stream", "subscribe", "update", "upload", "upsert",
}  # fmt: skip
# Standard-library modules that do I/O; the rest of the standard library isn't a library to cover
STDLIB_IO = {"socket", "smtplib", "ftplib", "urllib", "http", "sqlite3", "subprocess"}
# Drops that are rules working as designed, not gaps: a broad match that turned out not to be
# about a table (`table_of`), or a query built outside any function
EXPECTED_DROPS = ("table_of: not a mapped entity", "@function outside a function")


@dataclass
class Item:
    kind: str  # CoverageKind value
    signal: str  # finer than kind: unknown_host, dropped_match, …
    target: str  # the key: a symbol, library, class, rule, or file
    label: str  # how it reads in the report
    occurrences: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)
    applications: dict[str, int] = field(default_factory=dict)  # application → occurrences

    def add(self, sample: dict[str, Any]) -> None:
        self.occurrences += 1
        app = sample.get("application")
        if app:
            self.applications[app] = self.applications.get(app, 0) + 1
        if len(self.samples) < SAMPLES:
            self.samples.append(sample)


@dataclass
class Report:
    items: list[Item]
    # Completeness numbers for the repository (DESIGN_DOC D62)
    metrics: dict[str, Any]


def collect(
    x: Extraction,
    resolver: PythonResolver | None,
    plan: GraphPlan,
    all_packs: list[Pack],
    app_of: Callable[[str | None], str] | None = None,
) -> Report:
    """`app_of`: the application a file belongs to (its service module's), so items and
    numbers can be shown per application, not just per repository."""
    items: dict[tuple[str, str], Item] = {}
    where = app_of or (lambda _file: "")

    class _Tagged(Item):
        def add(self, sample: dict[str, Any]) -> None:
            super().add({**sample, "application": where(sample.get("file"))})

    def item(kind: str, signal: str, target: str, label: str) -> Item:
        key = (kind, target)
        if key not in items:
            items[key] = _Tagged(kind, signal, target, label)
        return items[key]

    _unknown_hosts(plan, resolver, item)
    _unreachable(plan, resolver, item)
    _dropped(x, item)
    _unparsed(x, item)
    no_io: dict[str, Any] = {"total": 0, "explained": 0, "by_app": {}}
    io = _external_calls(x, resolver, all_packs, item, where) if resolver else no_io
    _libraries(resolver, all_packs, items, item) if resolver else None

    stats = plan.stats
    reachable, total = stats.get("functions_reachable", 0), stats.get("functions", 0)
    order = ["unknown_host", "external_call", "library", "unreachable_effect", "dropped_match"]
    ranked = sorted(
        items.values(),
        key=lambda i: (order.index(i.signal) if i.signal in order else 9, -i.occurrences),
    )
    return Report(
        ranked,
        {
            "functions": total,
            "functions_reachable": reachable,
            "reachable_pct": round(100 * reachable / total, 1) if total else None,
            "io_calls": io["total"],
            "io_calls_explained": io["explained"],
            "io_explained_pct": round(100 * io["explained"] / io["total"], 1)
            if io["total"]
            else None,
            "items": len(ranked),
            "by_signal": _count(i.signal for i in ranked),
            "applications": _per_application(resolver, plan, io["by_app"], ranked, where),
        },
    )


def _per_application(
    r: PythonResolver | None,
    plan: GraphPlan,
    io_by_app: dict[str, dict[str, int]],
    items: list[Item],
    where: Callable[[str | None], str],
) -> dict[str, dict[str, int]]:
    """The completeness numbers for each application in the repository."""
    out: dict[str, dict[str, int]] = defaultdict(
        lambda: {"functions": 0, "functions_reachable": 0, "io_calls": 0, "io_calls_explained": 0}
    )
    for fn in r.functions() if r else []:
        app = out[where(_rel(r, fn))]
        app["functions"] += 1
        app["functions_reachable"] += int(fn.qualname in plan.reached)
    for name, io in io_by_app.items():
        out[name]["io_calls"] += io["total"]
        out[name]["io_calls_explained"] += io["explained"]
    for it in items:
        for name in it.applications:
            out[name]["items"] = out[name].get("items", 0) + 1
    return {name: dict(v) for name, v in out.items() if name}


# ---------------------------------------------------------------- the signals


def _unknown_hosts(plan: GraphPlan, r: PythonResolver | None, item: Any) -> None:
    """HTTP calls whose host is runtime data (D64): one item per calling class, like the
    graph's `Unknown host · …` node."""
    for u in plan.stats.get("unmet_joins", []):
        if u.get("kind") != "http_host":
            continue
        fn = _function(r, u.get("function"))
        owner = (fn.cls.qualname if fn.cls else fn.module.name) if fn else u.get("function") or ""
        file = (u.get("file") or "").rsplit("/", 1)[-1]
        label = f"Unknown host · {owner.rsplit('.', 1)[-1]}" + (f" ({file})" if file else "")
        item("unmet_join", "unknown_host", owner, label).add(
            {
                "file": u.get("file"),
                "line": u.get("line"),
                "function": u.get("function"),
                "detail": u.get("url"),
            }
        )


def _unreachable(plan: GraphPlan, r: PythonResolver | None, item: Any) -> None:
    """Code with effects that no entry point reaches: an entry point (a receiver, a schedule)
    is probably missing, or the call graph can't see how it's reached (runtime dispatch)."""
    for u in plan.stats.get("unreached_effects", []):
        fn = _function(r, u["function"])
        it = item("unreachable", "unreachable_effect", u["function"], u["function"])
        it.add(
            {
                "file": _rel(r, fn),
                "line": fn.span[0] if fn else None,
                "function": u["function"],
                "detail": ", ".join(sorted(set(u["effects"]))),
            }
        )


def _dropped(x: Extraction, item: Any) -> None:
    """Matches a rule found but couldn't complete because Steno couldn't tell: a condition it
    couldn't check, a name it couldn't resolve. Drops that are the rule working as designed
    (EXPECTED_DROPS) aren't gaps."""
    for origin, reason in x.dropped:
        if reason in EXPECTED_DROPS:
            continue
        why = reason.split(":", 1)[0] if reason.startswith("where") else reason
        target = f"{origin.rule}: {why}"
        item("unmet_join", "dropped_match", target, target).add(
            {"file": origin.file, "line": origin.line, "detail": reason}
        )


def _unparsed(x: Extraction, item: Any) -> None:
    """Files Steno couldn't read at all: nothing in them is in the graph."""
    for error in x.errors:
        file, _, message = error.partition(": ")
        if "syntax error" in message:
            item("unparsed", "unparsed", file, file).add({"file": file, "detail": message})


def _external_calls(
    x: Extraction,
    r: PythonResolver,
    packs: list[Pack],
    item: Any,
    where: Callable[[str | None], str],
) -> dict[str, Any]:
    """Calls from first-party code into a library that look like I/O and that no rule
    explains, grouped by the library function called (`com.x.Publisher#send`).

    Explained: a rule matched on the call's lines, or a rule from the pack that covers that
    library recorded something in the same function. (`stmt = select(Job)` is the read;
    `await session.execute(stmt)` a few lines later runs it.)
    """
    explained = _matched_lines(x)
    libs_of = {p.name: _libs(p) for p in packs}
    by_file: dict[str, list[tuple[int, set[str]]]] = defaultdict(list)
    for o in _origins(x):
        by_file[o.file].append((o.line, libs_of.get(o.pack, set())))
    first_party = {name.split(".")[0] for name in r.modules}
    total = covered = 0
    by_app: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "explained": 0})
    for fn in r.functions():
        scope = r.scope_of(fn, fn.cls.qualname if fn.cls else None)
        rel = _rel(r, fn) or ""
        counts = by_app[where(rel)]
        start, end = fn.span
        for call in _calls(fn.node):
            symbol = r.symbol_of(call.func, scope)
            if not symbol or not _is_io(symbol, first_party):
                continue
            total += 1
            counts["total"] += 1
            lines = range(call.lineno, (call.end_lineno or call.lineno) + 1)
            lib = _norm(symbol.split(".")[0])
            in_function = any(
                start <= line <= end and lib in libs for line, libs in by_file.get(rel, [])
            )
            if in_function or any((rel, line) in explained for line in lines):
                covered += 1
                counts["explained"] += 1
                continue
            item("external_call", "external_call", symbol, symbol).add(
                {"file": rel, "line": call.lineno, "function": fn.qualname}
            )
    return {"total": total, "explained": covered, "by_app": dict(by_app)}


def _libraries(r: PythonResolver, packs: list[Pack], items: dict[Any, Item], item: Any) -> None:
    """Libraries the code imports that no rule pack covers, when they're I/O libraries or
    have unexplained I/O calls: each is a pack (or an org rule) worth writing."""
    covered = set().union(*(_libs(p) for p in packs)) if packs else set()
    first_party = {name.split(".")[0] for name in r.modules}
    with_calls = {
        i.target.split(".")[0] for (kind, _), i in items.items() if kind == "external_call"
    }
    files: dict[str, list[str]] = defaultdict(list)
    for mod in r.modules.values():
        tops = {s.split(".")[0] for s in mod.imports.values()} | {
            s.split(".")[0] for s in mod.module_imports
        }
        for top in tops:
            if top and top not in first_party and not _stdlib(top):
                files[top].append(_rel_path(r, mod.path))
    for lib, where in sorted(files.items()):
        if _norm(lib) in covered or (lib not in IO_PACKAGES and lib not in with_calls):
            continue
        it = item("library", "library", lib, lib)
        for f in sorted(where):
            it.add({"file": f, "detail": f"imports {lib}"})


# ---------------------------------------------------------------- helpers


def _origins(x: Extraction) -> list[Any]:
    """Where every rule produced something, or tried to (a dropped match is reported on its
    own, not again as an unexplained call)."""
    return [
        *(n.origin for n in x.nodes),
        *(e.origin for e in x.edges),
        *(c.origin for c in x.clues),
        *(ep.origin for ep in x.entry_points),
        *(o for o, _ in x.dropped),
    ]


def _matched_lines(x: Extraction) -> set[tuple[str, int]]:
    return {(o.file, o.line) for o in _origins(x)}


def _libs(pack: Pack) -> set[str]:
    """The libraries a pack covers: what turns it on."""
    when = pack.enabled_when
    return {_norm(n) for n in [*when.get("imports", []), *when.get("dependencies", [])]}


def _calls(node: ast.AST) -> list[ast.Call]:
    """Calls in a function's body, closures included (they run as part of it), not nested
    classes, and not its decorators (`@router.post(...)` declares a route; it isn't I/O)."""
    out: list[ast.Call] = []
    todo: list[ast.AST] = list(getattr(node, "body", []))
    while todo:
        n = todo.pop()
        if isinstance(n, ast.ClassDef):
            continue
        if isinstance(n, ast.Call):
            out.append(n)
        todo.extend(ast.iter_child_nodes(n))
    return out


def _is_io(symbol: str, first_party: set[str]) -> bool:
    top = symbol.split(".")[0]
    if top in first_party or top == "builtins":
        return False
    if _stdlib(top) and top not in STDLIB_IO:
        return False
    name = symbol.rsplit(".", 1)[-1].lower()
    # Async variants: `acomplete`, `achat`, `aquery`, `send_async`
    return any(
        n in IO_VERBS
        for n in (name, name[1:] if name.startswith("a") else "", name.removesuffix("_async"))
    )


def _stdlib(top: str) -> bool:
    return top in sys.stdlib_module_names


def _function(r: PythonResolver | None, symbol: str | None) -> FunctionInfo | None:
    if r is None or not symbol:
        return None
    found = r.lookup(symbol)
    return found if isinstance(found, FunctionInfo) else None


def _rel(r: PythonResolver | None, fn: FunctionInfo | None) -> str | None:
    return _rel_path(r, fn.module.path) if r and fn else None


def _rel_path(r: PythonResolver, path: Any) -> str:
    try:
        return path.relative_to(r.root).as_posix()
    except ValueError:
        return str(path)


def _norm(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(out)


__all__ = ["Item", "Report", "collect"]
