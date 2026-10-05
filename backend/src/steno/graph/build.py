"""Turn extracted facts into a graph plan: nodes and edges with stable IDs.

docs/knowledge-graph.md. Two kinds of node hang off one containment tree:

    Organization → Space → Application → Interface, Entity, Flow      architecture nodes
                         → Repository → Module → File → Function       code nodes

joined by the bridges `Application -BUILT_FROM-> Module` and `Flow -ENTRY-> Function`.
Effects found on functions (READS_FROM, WRITES_TO, CALLS) are lifted onto the flows
that reach them, so the architecture side shows what a flow touches without opening code.

Pure: no database access, so it's testable on its own. `writer.py` sends the plan to Neo4j.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from steno.extractors.facts import (
    UNRESOLVED_TEXT,
    AppRef,
    EdgeFact,
    Extraction,
    HttpRef,
    NodeFact,
    NodeRef,
    host_of,
)
from steno.extractors.structure import Module, Structure
from steno.graph import ids
from steno.resolvers.python import FunctionInfo, PythonResolver

# Node types search will cover (they'll carry cards; knowledge-graph.md §3, Card properties)
SEARCHABLE = {"Flow", "Application", "Space", "Organization", "Interface", "Entity", "Table"}
# Shared nodes: several repositories can reference them, so a re-ingest never deletes them outright
SHARED = {"Table", "DataStore", "ExternalSystem"}
EFFECTS = {"READS_FROM", "WRITES_TO", "CALLS", "PRODUCES", "CONSUMES"}
UNRESOLVED_SYSTEM = "unresolved"


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
    x: Extraction, resolver: PythonResolver | None, structure: Structure, ctx: Context
) -> GraphPlan:
    return _Builder(x, resolver, structure, ctx).run()


class _Builder:
    def __init__(self, x: Extraction, r: PythonResolver | None, s: Structure, ctx: Context):
        self.x, self.r, self.s, self.ctx = x, r, s, ctx
        self.plan = GraphPlan()
        self.now = datetime.now(UTC).isoformat()
        self.apps: dict[str, str] = {}  # module path → application node id
        self.functions: dict[str, FunctionInfo] = (
            {f.qualname: f for f in r.functions()} if r is not None else {}
        )
        self.fact_ids: dict[int, str] = {}  # id(NodeFact) → node id

    def run(self) -> GraphPlan:
        self._structure()
        for node in self.x.nodes:
            self._fact_node(node)
        for edge in self.x.edges:
            self._fact_edge(edge)
        self._code()
        self._flows()
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
            **{k: v for k, v in props.items() if isinstance(v, (str, int))},
            **prov,
        )
        if self.ctx.space_id:
            self.plan.edge("BELONGS_TO", sid, self.ctx.space_id, **prov)
        return sid

    def _external(self, host: str | None, name: str | None, prov: dict[str, Any]) -> str:
        key = host or name or UNRESOLVED_SYSTEM
        eid = self.plan.node(
            ids.external_system_id(key),
            ["ExternalSystem"],
            host=host,
            name=name or host or "Unresolved HTTP target",
            stub=key == UNRESOLVED_SYSTEM,
            **prov,
        )
        if self.ctx.org_id:
            self.plan.edge("BELONGS_TO", eid, self.ctx.org_id, **prov)
        return eid

    def _fact_edge(self, e: EdgeFact) -> None:
        o = e.origin
        prov = self._prov(o.rule, o.file, o.line)
        src = self._end(e.src, o.file, prov)
        dst = self._end(e.dst, o.file, prov)
        if src is None or dst is None:
            return
        props = {k: v for k, v in e.props.items() if isinstance(v, (str, int, float, bool))}
        if isinstance(e.dst, HttpRef):
            props |= {"method": e.dst.method, "url": e.dst.url}
        self.plan.edge(e.type, src, dst, **props, **prov)

    def _end(self, ref: Any, file: str, prov: dict[str, Any]) -> str | None:
        if isinstance(ref, NodeFact):
            return self._fact_node(ref)
        if isinstance(ref, AppRef):
            return self._app_for(file)
        if isinstance(ref, HttpRef):
            host = host_of(ref.url) if ref.url != UNRESOLVED_TEXT else None
            return self._external(host, None, prov)
        if isinstance(ref, NodeRef):
            props = ref.as_dict
            if ref.label == "Function":
                return self._function(props["symbol"])
            if ref.label == "Table":
                return self._table(props["name"], prov)
            if ref.label == "DataStore":
                return self._datastore(props, ["DataStore"], prov)
            if ref.label == "ExternalSystem":
                return self._external(props.get("host"), props.get("name"), prov)
        return None

    # ----------------------------------------------------------- code nodes

    def _function(self, qualname: str) -> str | None:
        """A Function node, with its File (and the File's Module) created on first use."""
        fn = self.functions.get(qualname)
        if fn is None or self.r is None:
            return None
        rel = fn.module.path.relative_to(self.r.root).as_posix()
        fid = ids.function_id(self.ctx.repo, qualname)
        if fid in self.plan.nodes:
            return fid
        p, prov = self.plan, self._prov("structure", rel, fn.node.lineno)
        file_node = ids.file_id(self.ctx.repo, rel)
        if file_node not in p.nodes:
            p.node(
                file_node,
                ["File"],
                path=rel,
                name=rel.rsplit("/", 1)[-1],
                language="python",
                **prov,
            )
            module = self.s.module_of(rel)
            if module:
                p.edge("BELONGS_TO", file_node, ids.module_id(self.ctx.repo, module.path), **prov)
        node = fn.node
        p.node(
            fid,
            ["Function"],
            symbol=qualname,
            name=node.name,
            container=fn.cls.qualname if fn.cls else fn.module.name,
            kind="method" if fn.cls else "function",
            params=[a for a in fn.params if a not in ("self", "cls")],
            is_async=node.__class__.__name__ == "AsyncFunctionDef",
            visibility="private"
            if node.name.startswith("_") and not node.name.startswith("__")
            else "public",
            doc=_first_sentence(node),
            language="python",
            start_line=node.lineno,
            end_line=node.end_lineno,
            reachable=False,  # set by _code() for functions an entry point reaches
            **prov,
        )
        p.edge("BELONGS_TO", fid, file_node, **prov)
        return fid

    def _code(self) -> None:
        """Function nodes for everything reachable from an entry point (D8), plus INVOKES."""
        if self.r is None:
            return
        self.calls: dict[str, list] = {q: self.r.invocations(f) for q, f in self.functions.items()}
        async_edges = {
            (e.src.as_dict.get("symbol"), e.dst.as_dict.get("symbol"))
            for e in self.x.edges
            if e.type == "INVOKES" and isinstance(e.src, NodeRef) and isinstance(e.dst, NodeRef)
        }
        self.graph: dict[str, list[str]] = defaultdict(list)
        for caller, invs in self.calls.items():
            for inv in invs:
                self.graph[caller].append(inv.callee)
        for caller, callee in async_edges:
            if caller and callee:
                self.graph[caller].append(callee)

        roots = {ep.function for ep in self.x.entry_points}
        self.reachable = _reach(roots, self.graph)
        effectful = {
            self._symbol_of_node(e.src) for e in self.plan.edges.values() if e.type in EFFECTS
        }
        for q in sorted(self.reachable):
            self._function(q)
        for q in sorted(self.reachable):
            for seq, inv in enumerate(self.calls.get(q, []), start=1):
                if inv.callee in self.reachable:
                    self.plan.edge(
                        "INVOKES",
                        ids.function_id(self.ctx.repo, q),
                        ids.function_id(self.ctx.repo, inv.callee),
                        seq=seq,
                        line=inv.line,
                        conditional=inv.conditional,
                        in_loop=inv.in_loop,
                        **self._prov("call-graph"),
                    )
        significant = _reaches_effect(self.reachable, self.graph, effectful)
        for q in self.reachable:
            n = self.plan.nodes.get(ids.function_id(self.ctx.repo, q))
            if n:
                n.props["reachable"] = True
                n.props["significant"] = q in significant or q in roots

    def _symbol_of_node(self, node_id: str) -> str:
        n = self.plan.nodes.get(node_id)
        return n.props.get("symbol", "") if n else ""

    # --------------------------------------------------------------- flows

    def _flows(self) -> None:
        """One Flow per entry point: STARTS from its trigger, ENTRY into its function, and
        the effects of everything it reaches lifted onto it (L1 rollups)."""
        if self.r is None:
            return
        effects: dict[str, list[GraphEdge]] = defaultdict(list)
        for e in self.plan.edges.values():
            if e.type in EFFECTS:
                effects[self._symbol_of_node(e.src)].append(e)
        for ep in self.x.entry_points:
            trigger = self._fact_node(ep.trigger) if isinstance(ep.trigger, NodeFact) else None
            entry = self._function(ep.function)
            if trigger is None or entry is None:
                continue
            app = self._app_for(ep.origin.file)
            app_name = self.plan.nodes[app].props["name"] if app else self.ctx.repo
            trigger_name = self.plan.nodes[trigger].props.get("name", trigger)
            prov = self._prov(ep.origin.rule, ep.origin.file, ep.origin.line)
            reached = _reach({ep.function}, self.graph)
            fid = self.plan.node(
                ids.flow_id(app_name, trigger_name),
                ["Flow", "Searchable"],
                name=trigger_name,
                trigger=trigger_name,
                entry_symbol=ep.function,
                functions=len(reached),
                **prov,
            )
            self.plan.edge("STARTS", trigger, fid, **prov)
            self.plan.edge("ENTRY", fid, entry, **prov)
            if app:
                self.plan.edge("BELONGS_TO", fid, app, **prov)
            for q in reached:
                for e in effects.get(q, []):
                    self.plan.edge(e.type, fid, e.dst, rollup=True, **self._prov("flow-rollup"))


def _reach(roots: set[str], graph: dict[str, list[str]]) -> set[str]:
    seen, todo = set(roots), deque(roots)
    while todo:
        for callee in graph.get(todo.popleft(), []):
            if callee not in seen:
                seen.add(callee)
                todo.append(callee)
    return seen


def _reaches_effect(nodes: set[str], graph: dict[str, list[str]], effectful: set[str]) -> set[str]:
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
