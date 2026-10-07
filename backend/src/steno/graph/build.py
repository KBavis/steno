"""Turn extracted facts into a graph plan: nodes and edges with stable IDs.

docs/knowledge-graph.md. One containment tree:

    Organization → Space → Application → Interface, Entity, Flow → Step    architecture nodes
                         → Repository → Module → File                       code nodes

joined by `Application -BUILT_FROM-> Module`. Functions aren't nodes (D60): the call graph
is built in memory, walked from every entry point, and kept only as each flow's **trace**
(D63), the ordered list of functions it runs, stored on the Flow. Significant functions (an
entry point, or an effect at or below them) also become Step nodes, which carry the effects
(READS_FROM, WRITES_TO, CALLS, PRODUCES, CONSUMES); every effect is also rolled up onto the flow.

Pure: no database access, so it's testable on its own. `writer.py` sends the plan to Neo4j.
"""

import ast
import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from typing import Any

from steno.extraction.facts import (
    UNRESOLVED_TEXT,
    AppRef,
    EdgeFact,
    Extraction,
    HttpRef,
    NodeFact,
    NodeRef,
    split_url,
)
from steno.graph import ids
from steno.graph.structure import Module, Structure
from steno.resolvers.python import FunctionInfo, Invocation, PythonResolver
from steno.rule_packs.packs import HttpSystem

# Node types search will cover (they'll carry cards; knowledge-graph.md §3, Card properties)
SEARCHABLE = {"Flow", "Application", "Space", "Organization", "Interface", "Entity", "Table"}
# Shared nodes: several repositories can reference them, so a re-ingest never deletes them
# outright. Messaging interfaces (topics, queues, org-defined channels) are marked `shared`.
SHARED = {"Table", "DataStore", "ExternalSystem"}
EFFECTS = {"READS_FROM", "WRITES_TO", "CALLS", "PRODUCES", "CONSUMES"}
# Interfaces a rule can name by label alone. Any other label a rule uses on an Interface, or
# refers to with only a name, is an org-defined channel (D61), keyed by name like a topic.
APP_SCOPED = {"HttpEndpoint", "GrpcMethod"}
KNOWN_LABELS = {
    "Function", "Table", "DataStore", "ExternalSystem", "Entity", "Schedule", "Application",
    *APP_SCOPED,
}  # fmt: skip
# A function called from this many places, with nothing architectural below it, is a utility:
# listed where it's called, never expanded (D63). The threshold itself is still Open.
UTILITY_FAN_IN = 3
# Trace-entry fields that only point into the code at a commit (compared loosely, see writer)
POINTERS = ("start_line", "end_line", "call_line")
# A function in the call graph: its qualified name, and the subclass it runs for when it's an
# inherited method whose calls depend on that (Task.run as DiffTaskRunner), else None
Key = tuple[str, str | None]
UNRESOLVED_SYSTEM = "unresolved"


@dataclass(frozen=True)
class Call:
    """One call site in the in-memory call graph."""

    target: Key
    line: int
    conditional: bool = False
    in_loop: bool = False
    is_async: bool = False
    candidate: bool = False


@dataclass(frozen=True)
class Effect:
    """An effect a rule found inside a function, waiting for the steps that run it."""

    type: str
    dst: str
    props: tuple[tuple[str, Any], ...]


@dataclass
class GraphNode:
    id: str
    labels: list[str]
    props: dict[str, Any]


@dataclass
class GraphEdge:
    type: str
    src: str
    dst: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphPlan:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], GraphEdge] = field(default_factory=dict)
    # What the run measured but doesn't write: call graph size, effects no flow reaches
    stats: dict[str, Any] = field(default_factory=dict)

    def node(self, id: str, labels: list[str], **props: Any) -> str:
        existing = self.nodes.get(id)
        if existing:
            existing.labels = list(dict.fromkeys([*existing.labels, *labels]))
            existing.props.update({k: v for k, v in props.items() if v is not None})
        else:
            self.nodes[id] = GraphNode(
                id, labels, {k: v for k, v in props.items() if v is not None}
            )
        return id

    def edge(self, type: str, src: str, dst: str, **props: Any) -> None:
        key = (type, src, dst)
        if key in self.edges:  # one edge per pair; keep the first call site, merge flags
            current = self.edges[key].props
            for k, v in props.items():
                if isinstance(v, bool) and isinstance(current.get(k), bool):
                    current[k] = current[k] or v  # e.g. async: true from a background-task rule
                else:
                    current.setdefault(k, v)
        else:
            self.edges[key] = GraphEdge(
                type, src, dst, {k: v for k, v in props.items() if v is not None}
            )

    def counts(self) -> dict[str, dict[str, int]]:
        nodes: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            nodes[n.labels[0]] += 1
        edges: dict[str, int] = defaultdict(int)
        for e in self.edges.values():
            edges[e.type] += 1
        return {"nodes": dict(nodes), "edges": dict(edges)}


@dataclass
class Context:
    """Where this ingestion fits: the repository row, its space, its job and commit."""

    repo: str
    space_id: str | None
    org_id: str | None
    job_id: int
    commit: str | None


def build(
    x: Extraction,
    resolver: PythonResolver | None,
    structure: Structure,
    ctx: Context,
    http_systems: list[HttpSystem] | None = None,
) -> GraphPlan:
    """`http_systems`: the enabled packs' HTTP signatures, which name the services calls go to."""
    return _Builder(x, resolver, structure, ctx, http_systems or []).run()


class _Builder:
    def __init__(
        self,
        x: Extraction,
        r: PythonResolver | None,
        s: Structure,
        ctx: Context,
        http_systems: list[HttpSystem],
    ):
        self.x, self.r, self.s, self.ctx = x, r, s, ctx
        self.http_systems = http_systems
        self.unmet: list[dict[str, Any]] = []  # coverage: calls whose target can't be named
        self.plan = GraphPlan()
        self.now = datetime.now(UTC).isoformat()
        self.apps: dict[str, str] = {}  # module path → application node id
        self.functions: dict[str, FunctionInfo] = (
            {f.qualname: f for f in r.functions()} if r is not None else {}
        )
        self.fact_ids: dict[int, str] = {}  # id(NodeFact) → node id
        self.effects: dict[str, list[Effect]] = defaultdict(list)  # function symbol → effects
        self.graph: dict[Key, list[Call]] = {}
        self.significant: set[Key] = set()
        self.utility: set[Key] = set()
        self.files: dict[str, dict[str, int]] = {}  # file path → counts, for File nodes
        self.counted: set[Key] = set()

    def run(self) -> GraphPlan:
        self._structure()
        for node in self.x.nodes:
            self._fact_node(node)
        for edge in self.x.edges:
            self._fact_edge(edge)
        self._call_graph()
        self._flows()
        self._file_nodes()
        self.plan.stats["unmet_joins"] = self.unmet
        return self.plan

    # ----------------------------------------------------------- provenance

    def _prov(
        self, extracted_by: str, file: str | None = None, line: int | None = None
    ) -> dict[str, Any]:
        return {
            "repo": self.ctx.repo,
            "ingestion_job": self.ctx.job_id,
            "commit": self.ctx.commit,
            "extracted_by": extracted_by,
            "source_file": file,
            "source_line": line or None,
            "confidence": 1.0,
            "last_seen": self.now,
        }

    # ------------------------------------------------------------ structure

    def _structure(self) -> None:
        p, repo = self.plan, self.ctx.repo
        repo_id = p.node(
            ids.repository_id(repo), ["Repository"], name=repo, **self._prov("structure")
        )
        if self.ctx.space_id:
            p.edge("BELONGS_TO", repo_id, self.ctx.space_id, **self._prov("structure"))
        for m in self.s.modules:
            mid = p.node(
                ids.module_id(repo, m.path),
                ["Module", *sorted(m.roles)],
                path=m.path,
                build_tool=m.build_tool,
                language=m.language,
                name=m.path if m.path != "." else repo,
                **self._prov("structure"),
            )
            p.edge("BELONGS_TO", mid, repo_id, **self._prov("structure"))
            if "Service" in m.roles:
                self._application(m, mid)

    def _application(self, m: Module, module_node: str) -> None:
        name = self.s.application_name(self.ctx.repo, m)
        app = self.plan.node(
            ids.application_id(name),
            ["Application", "Searchable"],
            name=name,
            **self._prov("structure"),
        )
        self.apps[m.path] = app
        self.plan.edge("BUILT_FROM", app, module_node, **self._prov("structure"))
        if self.ctx.space_id:
            self.plan.edge("BELONGS_TO", app, self.ctx.space_id, **self._prov("structure"))

    def _app_for(self, rel_file: str) -> str | None:
        m = self.s.module_of(rel_file)
        return self.apps.get(m.path) if m else None

    # ------------------------------------------------------- extracted facts

    def _fact_node(self, n: NodeFact) -> str | None:
        if id(n) in self.fact_ids:
            return self.fact_ids[id(n)]
        p, o, label = self.plan, n.origin, n.label
        prov = self._prov(o.rule, o.file, o.line)
        app = self._app_for(o.file)
        app_name = self.plan.nodes[app].props["name"] if app else self.ctx.repo
        labels = [*n.labels, *(["Searchable"] if SEARCHABLE & set(n.labels) else [])]
        props = {
            k: v for k, v in n.props.items() if not isinstance(v, (dict, list)) or k == "fields"
        }
        if label == "HttpEndpoint":
            nid = p.node(
                ids.http_endpoint_id(app_name, n.props["method"], n.props["path"]),
                labels,
                name=f"{n.props['method']} {n.props['path']}",
                **props,
                **prov,
            )
            if app:
                p.edge("BELONGS_TO", nid, app, **prov)
        elif label == "Entity":
            nid = p.node(
                ids.entity_id(app_name, n.props["name"]),
                labels,
                short_name=n.props["name"].rsplit(".", 1)[-1],
                **props,
                **prov,
            )
            if app:
                p.edge("BELONGS_TO", nid, app, **prov)
        elif label == "Table":
            nid = self._table(n.props["name"], prov)
        elif label == "DataStore":
            nid = self._datastore(n.props, labels, prov)
        elif label == "ExternalSystem":
            nid = self._external(n.props.get("host"), n.props.get("name"), prov)
        elif "Interface" in n.labels and label not in APP_SCOPED and n.props.get("name"):
            nid = self._channel(label, str(n.props["name"]), props, prov)
        else:
            nid = p.node(f"{label.lower()}:{app_name}:{n.ref().props}", labels, **props, **prov)
        self.fact_ids[id(n)] = nid
        return nid

    def _table(self, name: str, prov: dict[str, Any], datastore: str | None = None) -> str:
        """Tables whose data store isn't known yet hang off the repository's placeholder store."""
        store = datastore or self._unknown_relational_store(prov)
        tid = self.plan.node(
            ids.table_id(store, None, name),
            ["Table", "Searchable"],
            name=name,
            kind="table",
            stub=True,
            shared=True,
            **prov,
        )
        self.plan.edge("BELONGS_TO", tid, store, **prov)
        return tid

    def _unknown_relational_store(self, prov: dict[str, Any]) -> str:
        """Assume an unidentified store belongs to this repository, so that separate
        applications in one space don't appear to share a database."""
        sid = self.plan.node(
            ids.datastore_id("unknown", "unresolved", f"repo-{self.ctx.repo}"),
            ["DataStore", "Relational"],
            name=f"{self.ctx.repo} relational store (not identified yet)",
            vendor="unknown",
            stub=True,
            shared=True,
            **prov,
        )
        if self.ctx.space_id:
            self.plan.edge("BELONGS_TO", sid, self.ctx.space_id, **prov)
        return sid

    def _datastore(self, props: dict[str, Any], labels: list[str], prov: dict[str, Any]) -> str:
        vendor, host, db = (
            props.get("vendor", "unknown"),
            props.get("host", ""),
            props.get("database", ""),
        )
        # A host we couldn't resolve says nothing about which server it is: keep it per repository
        host_key = host if host and host != UNRESOLVED_TEXT else f"unresolved-{self.ctx.repo}"
        sid = self.plan.node(
            ids.datastore_id(vendor, host_key, db),
            [lbl for lbl in labels if lbl != "Searchable"],
            name=f"{vendor} @ {host or '?'}",
            shared=True,
            **{k: v for k, v in props.items() if isinstance(v, (str, int))},
            **prov,
        )
        if self.ctx.space_id:
            self.plan.edge("BELONGS_TO", sid, self.ctx.space_id, **prov)
        return sid

    def _external(
        self,
        host: str | None,
        name: str | None,
        prov: dict[str, Any],
        key: str | None = None,
        **props: Any,
    ) -> str:
        key = key or host or name or UNRESOLVED_SYSTEM
        eid = self.plan.node(
            ids.external_system_id(key),
            ["ExternalSystem"],
            host=host,
            name=name or host or "Unresolved HTTP target",
            stub=key == UNRESOLVED_SYSTEM or props.pop("stub", False),
            shared=True,
            **props,
            **prov,
        )
        if self.ctx.org_id:
            self.plan.edge("BELONGS_TO", eid, self.ctx.org_id, **prov)
        return eid

    def _channel(self, label: str, name: str, props: dict[str, Any], prov: dict[str, Any]) -> str:
        """A topic, queue, or org-defined channel (D61): keyed by its name, so the application
        that sends and the one that receives meet here. Owned by the space that first wrote it
        (the producer's, by convention; knowledge-graph.md §3, Topic ownership)."""
        cid = self.plan.node(
            ids.channel_id(label, name),
            ["Interface", label, "Searchable"],
            **{**props, "name": name},
            shared=True,
            **prov,
        )
        owner = self.ctx.space_id or self.ctx.org_id
        if owner and not any(k[0] == "BELONGS_TO" and k[1] == cid for k in self.plan.edges):
            self.plan.edge("BELONGS_TO", cid, owner, **prov)
        return cid

    def _http_target(self, ref: HttpRef, prov: dict[str, Any], caller: str | None = None) -> str:
        """Where an HTTP call goes. A known host is the identity, and an HTTP signature names
        the service when it's one of that service's own hosts. A call whose host is runtime
        data (a customer's own Jira site) isn't merged with anything: it goes to a node for
        the class that makes it, named after that class and its file, and the call keeps its
        URL template (`{domain}/rest/api/content/{page_id}`). It's also listed as an unmet
        join for the coverage report. Which service that is stays unnamed for now."""
        host, _ = split_url(ref.url)
        if host:
            system = next(
                (s for s in self.http_systems if any(fnmatch(host, h) for h in s.hosts)), None
            )
            if system:  # the service's own host: one system, however many hosts it has
                return self._external(host, system.name, prov, key=system.name)
            return self._external(host, host, prov)
        fn = self.functions.get(caller or "")
        owner = (fn.cls.qualname if fn.cls else fn.module.name) if fn else caller or self.ctx.repo
        file = (prov.get("source_file") or "").rsplit("/", 1)[-1]
        self.unmet.append(
            {
                "kind": "http_host",
                "function": caller,
                "file": prov.get("source_file"),
                "line": prov.get("source_line"),
                "url": ref.url,
            }
        )
        short = owner.rsplit(".", 1)[-1]
        return self._external(
            None,
            f"Unknown host · {short} ({file})" if file else f"Unknown host · {short}",
            prov,
            key=f"{UNRESOLVED_SYSTEM}:{owner}",
            stub=True,
            called_from=owner,
        )

    def _fact_edge(self, e: EdgeFact) -> None:
        o = e.origin
        prov = self._prov(o.rule, o.file, o.line)
        if _is_function(e.src) or _is_function(e.dst):
            # Function → function is a call graph edge (an async background task, D45), read
            # in _call_graph. Function → anything else is an effect, placed on the steps that
            # run that function.
            if _is_function(e.src) and not _is_function(e.dst):
                symbol = e.src.as_dict["symbol"]  # type: ignore[union-attr]
                props = _scalars(e.props)
                if isinstance(e.dst, HttpRef):
                    props |= {"method": e.dst.method, "url": e.dst.url}
                    dst: str | None = self._http_target(e.dst, prov, symbol)
                else:
                    dst = self._end(e.dst, o.file, prov)
                if dst is not None:
                    effect = Effect(e.type, dst, tuple(sorted({**props, **prov}.items())))
                    if effect not in self.effects[symbol]:
                        self.effects[symbol].append(effect)
            return
        src = self._end(e.src, o.file, prov)
        dst = self._end(e.dst, o.file, prov)
        if src is None or dst is None:
            return
        props = _scalars(e.props)
        if isinstance(e.dst, HttpRef):
            props |= {"method": e.dst.method, "url": e.dst.url}
        self.plan.edge(e.type, src, dst, **props, **prov)

    def _end(self, ref: Any, file: str, prov: dict[str, Any]) -> str | None:
        if isinstance(ref, NodeFact):
            return self._fact_node(ref)
        if isinstance(ref, AppRef):
            return self._app_for(file)
        if isinstance(ref, HttpRef):
            return self._http_target(ref, prov)
        if isinstance(ref, NodeRef):
            props = ref.as_dict
            if ref.label == "Table":
                return self._table(props["name"], prov)
            if ref.label == "DataStore":
                return self._datastore(props, ["DataStore"], prov)
            if ref.label == "ExternalSystem":
                return self._external(props.get("host"), props.get("name"), prov)
            if ref.label not in KNOWN_LABELS and props.get("name"):
                return self._channel(ref.label, str(props["name"]), {}, prov)
        return None

    # ------------------------------------------------------ the call graph

    def _call_graph(self) -> None:
        """The call graph of everything an entry point reaches, in memory only (D60).

        A call to an inherited method follows the subclass it was made on, so Task.run reached
        through DiffTaskRunner(...).run() runs DiffTaskRunner's methods; that pairing becomes
        its own key when it calls something different from the base version (D58). A call to
        an abstract method goes to every implementation, marked `candidate`.
        """
        if self.r is None:
            return
        self.calls: dict[Key, list[Invocation]] = {}
        self._bind_memo: dict[Key, bool] = {}
        # Calls a rule found that the resolver can't see (a function handed to a background
        # task): (callee, line), merged into the caller's calls by line
        rule_calls: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for e in self.x.edges:
            if _is_function(e.src) and _is_function(e.dst):
                caller = e.src.as_dict.get("symbol")  # type: ignore[union-attr]
                callee = e.dst.as_dict.get("symbol")  # type: ignore[union-attr]
                if caller in self.functions and callee in self.functions:
                    rule_calls[caller].append((callee, e.origin.line))

        self.roots = {
            (ep.function, None) for ep in self.x.entry_points if ep.function in self.functions
        }
        todo = deque(sorted(self.roots, key=_key_order))
        while todo:
            key = todo.popleft()
            if key in self.graph:
                continue
            self.graph[key] = self._calls_from(key, rule_calls.get(key[0], []))
            todo.extend(c.target for c in self.graph[key] if c.target not in self.graph)

        reachable = set(self.graph)
        callers: dict[Key, set[Key]] = defaultdict(set)
        for key, out in self.graph.items():
            for c in out:
                callers[c.target].add(key)
        effectful = {k for k in reachable if self.effects.get(k[0])}
        plain = {k: [c.target for c in out] for k, out in self.graph.items()}
        self.significant = _reaches_effect(reachable, plain, effectful) | self.roots
        self.utility = {
            k for k in reachable if len(callers[k]) >= UTILITY_FAN_IN and k not in self.significant
        }
        reached_symbols = {k[0] for k in reachable}
        unreached = sorted(sym for sym in self.effects if sym not in reached_symbols)
        self.plan.stats.update(
            functions=len(self.functions),
            functions_reachable=len(reached_symbols),
            calls=sum(len(out) for out in self.graph.values()),
            utility=len({k[0] for k in self.utility}),
            # Effects in code no entry point reaches: coverage items (unreachable code), not facts
            unreached_effects=[
                {"function": sym, "effects": [e.type for e in self.effects[sym]]}
                for sym in unreached
            ],
        )

    def _calls_from(self, key: Key, rule_calls: list[tuple[str, int]]) -> list[Call]:
        """A function's calls in source order. A rule's call to a function the code already
        calls there marks that call async instead of adding another (D45)."""
        out: list[Call] = []
        for inv in self._calls_of(key):
            for target, candidate in self._targets(inv):
                out.append(Call(target, inv.line, inv.conditional, inv.in_loop, False, candidate))
        for callee, line in rule_calls:
            same = [i for i, c in enumerate(out) if c.target[0] == callee]
            if same:
                c = out[same[0]]
                out[same[0]] = Call(c.target, c.line, c.conditional, c.in_loop, True, c.candidate)
            else:
                out.append(Call((callee, None), line, is_async=True))
        return sorted(out, key=lambda c: c.line)  # stable: same-line calls keep their order

    def _calls_of(self, key: Key) -> list[Invocation]:
        if key not in self.calls:
            assert self.r is not None
            self.calls[key] = self.r.invocations(self.functions[key[0]], self_type=key[1])
        return self.calls[key]

    def _targets(self, inv: Invocation) -> list[tuple[Key, bool]]:
        """Where a call goes: its implementations if it's abstract, else the method itself,
        as the subclass it was called on when that changes what it does."""
        if inv.candidates:
            return [((c, None), True) for c in inv.candidates if c in self.functions]
        if inv.callee not in self.functions:
            return []
        bound = (
            inv.receiver if inv.receiver and self._needs_binding(inv.callee, inv.receiver) else None
        )
        return [((inv.callee, bound), False)]

    def _needs_binding(self, qualname: str, receiver: str) -> bool:
        """Does `qualname`, run for `receiver`, call anything different from the base version?"""
        key = (qualname, receiver)
        if key in self._bind_memo:
            return self._bind_memo[key]
        self._bind_memo[key] = False  # a cycle adds nothing new
        bound, plain = self._calls_of(key), self._calls_of((qualname, None))
        differs = [(i.callee, i.candidates) for i in bound] != [
            (i.callee, i.candidates) for i in plain
        ] or any(i.receiver and self._needs_binding(i.callee, i.receiver) for i in bound)
        self._bind_memo[key] = differs
        return differs

    # --------------------------------------------------------------- flows

    def _flows(self) -> None:
        """One Flow per entry point: STARTS from its trigger, its trace (L3) on the node, its
        significant functions as Steps (L2) carrying their effects, and every effect rolled
        up onto the flow (L1)."""
        if self.r is None:
            return
        for ep in self.x.entry_points:
            prov = self._prov(ep.origin.rule, ep.origin.file, ep.origin.line)
            if isinstance(ep.trigger, NodeFact):
                trigger = self._fact_node(ep.trigger)
            else:
                trigger = self._end(ep.trigger, ep.origin.file, prov)
            entry = self.functions.get(ep.function)
            if trigger is None or entry is None:
                continue
            app = self._app_for(ep.origin.file)
            app_name = self.plan.nodes[app].props["name"] if app else self.ctx.repo
            trigger_name = self.plan.nodes[trigger].props.get("name", trigger)
            flow = ids.flow_id(app_name, trigger_name)
            trace = self._trace(flow, (ep.function, None))
            start, end = entry.span
            self.plan.node(
                flow,
                ["Flow", "Searchable"],
                name=trigger_name,
                trigger=trigger_name,
                entry_symbol=ep.function,
                entry_file=self._rel(entry),
                entry_start_line=start,
                entry_end_line=end,
                functions=len({t["symbol"] for t in trace}),
                steps=sum(1 for t in trace if t.get("step")),
                trace=json.dumps(trace, separators=(",", ":")),
                trace_files=sorted({t["file"] for t in trace}),
                trace_symbols=sorted({t["symbol"] for t in trace}),
                **prov,
            )
            self.plan.edge("STARTS", trigger, flow, **prov)
            if app:
                self.plan.edge("BELONGS_TO", flow, app, **prov)
            self._steps(flow, trace)

    def _trace(self, flow: str, root: Key) -> list[dict[str, Any]]:
        """The flow's trace: a depth-first walk from the entry, each call in source order.

        Every function the flow calls is listed once per call site (D63). A function already
        expanded earlier in the flow is listed again but not expanded (`repeat`); a utility
        is never expanded; a call back into a function on the current path is `recursive`.
        """
        entries: list[dict[str, Any]] = []
        expanded: set[Key] = set()
        occurrences: dict[Key, int] = defaultdict(int)

        def visit(key: Key, path: str, call: Call | None, stack: frozenset[Key]) -> None:
            fn = self.functions[key[0]]
            entry = self._entry(key, fn, path, call)
            entries.append(entry)
            if key in self.significant:
                occurrences[key] += 1
                entry["step"] = occurrences[key]  # its Step node: step_of(flow, entry)
            if key in stack:
                entry["recursive"] = True
                return
            if key in expanded:
                if self.graph.get(key):
                    entry["repeat"] = True
                return
            if key in self.utility:
                return
            expanded.add(key)
            for i, c in enumerate(self.graph.get(key, []), start=1):
                visit(c.target, f"{path}.{i}", c, stack | {key})

        visit(root, "1", None, frozenset())
        return entries

    def _entry(self, key: Key, fn: FunctionInfo, path: str, call: Call | None) -> dict[str, Any]:
        start, end = fn.span
        rel = self._rel(fn)
        counts = self.files.setdefault(rel, {"functions": 0, "significant": 0})
        entry: dict[str, Any] = {
            "path": path,
            "symbol": key[0],
            "bound_to": key[1],
            "name": fn.node.name,
            "container": fn.cls.qualname if fn.cls else fn.module.name,
            "file": rel,
            "start_line": start,
            "end_line": end,
            "call_line": call.line if call else None,
            "conditional": bool(call and call.conditional),
            "in_loop": bool(call and call.in_loop),
            "async": bool(call and call.is_async)
            or fn.node.__class__.__name__ == "AsyncFunctionDef",
            "candidate": bool(call and call.candidate),
            "significant": key in self.significant,
            "utility": key in self.utility,
            "effects": [
                {"edge": e.type, "target": e.dst, **_pick(e.props, "operation", "method", "url")}
                for e in self.effects.get(key[0], [])
            ],
            "doc": _first_sentence(fn.node),
            "body_hash": self._body_hash(fn),
        }
        if key not in self.counted:  # count each function once per file, not per flow
            self.counted.add(key)
            counts["functions"] += 1
            counts["significant"] += int(key in self.significant)
        # Keep the JSON small: drop empty and false fields
        return {k: v for k, v in entry.items() if v not in (None, False, [], "")}

    def _steps(self, flow: str, trace: list[dict[str, Any]]) -> None:
        """Steps (L2): the significant entries, nested under their nearest significant
        ancestor, chained FIRST_STEP / NEXT / SUBSTEP, each with its effects."""
        by_path = {t["path"]: t for t in trace}
        children: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for t in trace:
            if "step" not in t:
                continue
            parent = t["path"].rsplit(".", 1)[0] if "." in t["path"] else None
            while parent is not None and "step" not in by_path[parent]:
                parent = parent.rsplit(".", 1)[0] if "." in parent else None
            children[step_of(flow, by_path[parent]) if parent else None].append(t)

        def place(parent: str | None, number: str) -> None:
            previous = None
            for i, t in enumerate(children.get(parent, []), start=1):
                step_path = f"{number}.{i}" if number else str(i)
                prov = self._prov("flow-steps", t["file"], t["start_line"])
                sid = self.plan.node(
                    step_of(flow, t),
                    ["Step"],
                    name=t["name"],
                    path=step_path,
                    trace_path=t["path"],
                    symbol=t["symbol"],
                    bound_to=t.get("bound_to"),
                    container=t.get("container"),
                    file=t["file"],
                    start_line=t["start_line"],
                    end_line=t["end_line"],
                    call_line=t.get("call_line"),
                    conditional=t.get("conditional"),
                    is_async=t.get("async"),
                    candidate=t.get("candidate"),
                    **prov,
                )
                self.plan.edge("BELONGS_TO", sid, flow, **prov)
                if previous is None:
                    self.plan.edge(
                        "FIRST_STEP" if parent is None else "SUBSTEP", parent or flow, sid, **prov
                    )
                else:
                    self.plan.edge("NEXT", previous, sid, **prov)
                previous = sid
                for e in self.effects.get(t["symbol"], []):
                    self.plan.edge(e.type, sid, e.dst, **dict(e.props))
                    self.plan.edge(
                        e.type,
                        flow,
                        e.dst,
                        rollup=True,
                        **_pick(dict(e.props), "operation", "method", "url"),
                        **self._prov("flow-rollup"),
                    )
                place(sid, step_path)

        place(None, "")

    def _file_nodes(self) -> None:
        """A File node for every file a flow runs through, under its module."""
        for rel, counts in sorted(self.files.items()):
            prov = self._prov("structure", rel)
            fid = self.plan.node(
                ids.file_id(self.ctx.repo, rel),
                ["File"],
                path=rel,
                name=rel.rsplit("/", 1)[-1],
                language="python",
                functions=counts["functions"],
                significant=counts["significant"],
                **prov,
            )
            module = self.s.module_of(rel)
            if module:
                self.plan.edge("BELONGS_TO", fid, ids.module_id(self.ctx.repo, module.path), **prov)

    def _rel(self, fn: FunctionInfo) -> str:
        assert self.r is not None
        return fn.module.path.relative_to(self.r.root).as_posix()

    def _body_hash(self, fn: FunctionInfo) -> str:
        """Changes when the function's code changes, not when it only moves or is reformatted."""
        dump = ast.dump(fn.node, include_attributes=False)
        return hashlib.sha1(dump.encode()).hexdigest()[:12]


def step_of(flow: str, entry: dict[str, Any]) -> str:
    """The Step node for a significant trace entry."""
    return ids.step_id(flow, entry["symbol"], entry.get("bound_to"), entry["step"])


def _is_function(ref: Any) -> bool:
    return isinstance(ref, NodeRef) and ref.label == "Function"


def _scalars(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if isinstance(v, (str, int, float, bool))}


def _pick(props: Any, *keys: str) -> dict[str, Any]:
    d = dict(props)
    return {k: d[k] for k in keys if d.get(k) is not None}


def _key_order(key: Key) -> tuple[str, str]:
    return key[0], key[1] or ""


def _reaches_effect(nodes: set[Any], graph: dict[Any, list[Any]], effectful: set[Any]) -> set[Any]:
    """Functions with an effect, directly or anywhere below them ("significant")."""
    result = set(effectful) & nodes
    changed = True
    while changed:
        changed = False
        for q in nodes - result:
            if any(c in result for c in graph.get(q, [])):
                result.add(q)
                changed = True
    return result


def _first_sentence(node: Any) -> str | None:
    import ast

    doc = ast.get_docstring(node)
    if not doc:
        return None
    first = doc.strip().split("\n\n")[0].replace("\n", " ")
    return first.split(". ")[0].rstrip(".") + "." if first else None
