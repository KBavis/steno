"""Pass 3: join clues and resolve references (docs/ingestion.md §2, extractor-rules.md §4).

Each clue type has exactly one resolver here:
- prefix + mount → prefix chains (full endpoint paths)
- client         → calls through SDK client objects
- property       → config values (fed to the symbol resolver before values are read)
plus the references a rule can't complete on its own: `table_of` and partial identities.
"""

import ast
from pathlib import Path
from typing import Any

from steno.extractors.facts import (
    UNRESOLVED_TEXT,
    AppRef,
    ClueFact,
    EdgeFact,
    Extraction,
    HttpRef,
    NodeFact,
    NodeRef,
    Origin,
    TableOfRef,
)
from steno.resolvers.python import PythonResolver, Scope


def resolve(x: Extraction, r: PythonResolver | None, repo: Path) -> None:
    _dedupe_clues(x)
    _prefix_chains(x)
    if r is not None:
        _tables(x, r)
        _clients(x, r, repo)
    _partial_references(x)
    _dedupe_edges(x)


# ---------------------------------------------------------------- prefix chain


def _prefix_chains(x: Extraction) -> None:
    prefixes = {
        c.fields["owner"]: c.fields.get("value", "")
        for c in x.clues
        if c.kind == "prefix" and "owner" in c.fields
    }
    mounts = {
        c.fields["child"]: (c.fields["parent"], c.fields.get("prefix", ""))
        for c in x.clues
        if c.kind == "mount" and "child" in c.fields and "parent" in c.fields
    }

    def chain(owner: str, seen: frozenset[str] = frozenset()) -> str:
        if owner in seen:
            return ""
        own = prefixes.get(owner, "")
        if owner in mounts:
            parent, mount_prefix = mounts[owner]
            return chain(parent, seen | {owner}) + _text(mount_prefix) + _text(own)
        return _text(own)

    for node in x.nodes:
        owner = node.pending.pop("prefixed_by", None)
        if owner and "path" in node.props:
            node.props["path"] = _join_path(chain(owner), _text(node.props["path"]))


def _text(value: Any) -> str:
    return value if isinstance(value, str) else UNRESOLVED_TEXT


def _join_path(prefix: str, path: str) -> str:
    joined = (prefix.rstrip("/") + "/" + path.lstrip("/")) if prefix else path
    return joined if joined.startswith(("/", UNRESOLVED_TEXT)) else "/" + joined


# ---------------------------------------------------------------------- tables


def _tables(x: Extraction, r: PythonResolver) -> None:
    """`table_of`: the table an entity maps to, from the MAPS_TO edges rules emitted."""
    entity_table = {
        e.src.props.get("name"): e.dst.props.get("name")
        for e in x.edges
        if e.type == "MAPS_TO" and isinstance(e.src, NodeFact) and isinstance(e.dst, NodeFact)
    }
    kept = []
    for edge in x.edges:
        for end in ("src", "dst"):
            ref = getattr(edge, end)
            if isinstance(ref, TableOfRef):
                table = entity_table.get(_entity_of(ref.expr, ref.scope, r))
                if table is None:
                    x.dropped.append((edge.origin, "table_of: not a mapped entity"))
                    break
                setattr(edge, end, NodeRef.of("Table", {"name": table}))
        else:
            kept.append(edge)
    x.edges = kept


def _entity_of(expr: ast.expr, scope: Scope, r: PythonResolver) -> str | None:
    """The entity class behind `Job`, `Job.status`, or an instance like `job`."""
    if isinstance(expr, ast.Attribute):
        owner = r.class_of(expr.value, scope)
        if owner:
            return owner
    return r.class_of(expr, scope) or r.type_of(expr, scope)


# --------------------------------------------------------------------- clients


def _clients(x: Extraction, r: PythonResolver, repo: Path) -> None:
    clients = [
        c for c in x.clues if c.kind == "client" and "type" in c.fields and "target" in c.fields
    ]
    for c in clients:
        for method, returned in (c.fields.get("returns") or {}).items():
            r.return_hints[(c.fields["type"], method)] = returned
    if not clients:
        return
    for fn, call in r.method_calls():
        assert isinstance(call.func, ast.Attribute)
        receiver = r.type_of(call.func.value, r.scope_of(fn))
        if receiver is None:
            continue
        for clue in clients:
            if not r.is_subclass(receiver, [clue.fields["type"]]):
                continue
            edge_type = _client_edge(clue, call.func.attr)
            if edge_type:
                origin = Origin(
                    clue.origin.rule,
                    clue.origin.pack,
                    fn.module.path.relative_to(repo).as_posix(),
                    call.lineno,
                )
                x.edges.append(
                    EdgeFact(
                        edge_type,
                        NodeRef.of("Function", {"symbol": fn.qualname}),
                        clue.fields["target"],
                        {},
                        origin,
                    )
                )


def _client_edge(clue: ClueFact, method: str) -> str | None:
    target = clue.fields["target"]
    label = target.label if isinstance(target, (NodeFact, NodeRef)) else None
    if label == "ExternalSystem":
        return "CALLS"
    operation = (clue.fields.get("operations") or {}).get(method)
    return {"read": "READS_FROM", "write": "WRITES_TO"}.get(operation or "")


# ---------------------------------------------------------- partial identities


def _partial_references(x: Extraction) -> None:
    """{DataStore: {vendor: chroma}} → the app's single matching node (D50)."""
    for edge in x.edges:
        for end in ("src", "dst"):
            ref = getattr(edge, end)
            if not isinstance(ref, NodeRef) or ref.label == "Function":
                continue
            wanted = ref.as_dict
            matches = [
                n
                for n in x.nodes
                if ref.label in n.labels and all(n.props.get(k) == v for k, v in wanted.items())
            ]
            unique = {m.ref() for m in matches}
            if len(unique) == 1:
                setattr(edge, end, matches[0])
            elif len(unique) > 1:
                edge.props["ambiguous"] = True


# ---------------------------------------------------------------------- dedupe


def ref_key(ref: Any) -> tuple:
    if isinstance(ref, NodeFact):
        ref = ref.ref()
    if isinstance(ref, NodeRef):
        return ("node", ref.label, ref.props)
    if isinstance(ref, AppRef):
        return ("app",)
    if isinstance(ref, HttpRef):
        return ("http", ref.method, ref.url)
    return ("other", id(ref))


def _dedupe_edges(x: Extraction) -> None:
    seen, kept = set(), []
    for e in x.edges:
        key = (
            e.type,
            ref_key(e.src),
            ref_key(e.dst),
            tuple(sorted((k, repr(v)) for k, v in e.props.items())),
        )
        if key not in seen:
            seen.add(key)
            kept.append(e)
    x.edges = kept


def _dedupe_clues(x: Extraction) -> None:
    seen, kept = set(), []
    for c in x.clues:
        key = (
            c.kind,
            repr(
                sorted(
                    (k, ref_key(v) if isinstance(v, (NodeFact, NodeRef)) else repr(v))
                    for k, v in c.fields.items()
                )
            ),
        )
        if key not in seen:
            seen.add(key)
            kept.append(c)
    x.clues = kept
