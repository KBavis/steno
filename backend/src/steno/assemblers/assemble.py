"""The assemblers: fill in extracted facts' blanks from clues.

See docs/how-it-works/ingestion/stages.md §5 and rules-and-packs.md §4.

Each clue type has exactly one assembler here:
- prefix + mount → prefix chains (full endpoint paths)
- client         → calls through SDK client objects
- property       → config values (fed to the symbol resolver before values are read)
plus the references a rule can't complete on its own: `table_of` and partial identities.
"""

from pathlib import Path
from typing import Any

from steno.extraction.facts import (
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
from steno.resolvers.base import Resolver, Resolvers


def assemble(x: Extraction, r: Resolvers | None, repo: Path) -> None:
    _dedupe_clues(x)
    _prefix_chains(x)
    if r:
        _tables(x, r)
        _clients(x, r, repo)
        _passed_functions(x, r, repo)
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
    if not path:  # a method mapped at its controller's own path (`@PostMapping` alone)
        return prefix or "/"
    joined = (prefix.rstrip("/") + "/" + path.lstrip("/")) if prefix else path
    return joined if joined.startswith(("/", UNRESOLVED_TEXT)) else "/" + joined


# ---------------------------------------------------------------------- tables


def _tables(x: Extraction, r: Resolvers) -> None:
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
                resolver = r.by_language.get(ref.language)
                entity = resolver.entity_of(ref.expr, ref.scope) if resolver else None
                table = entity_table.get(entity)
                if table is None:
                    x.dropped.append((edge.origin, "table_of: not a mapped entity"))
                    break
                setattr(edge, end, NodeRef.of("Table", {"name": table}))
        else:
            kept.append(edge)
    x.edges = kept


# --------------------------------------------------------------------- clients


def _clients(x: Extraction, rs: Resolvers, repo: Path) -> None:
    for r in rs:
        _clients_in(x, r, repo)


def _clients_in(x: Extraction, r: Resolver, repo: Path) -> None:
    clients = [
        c
        for c in x.clues
        if c.kind == "client"
        and "type" in c.fields
        and "target" in c.fields
        and Path(c.origin.file).suffix in r.extensions
    ]
    for c in clients:
        for method, returned in (c.fields.get("returns") or {}).items():
            r.return_hints[(c.fields["type"], method)] = returned
    if not clients:
        return
    for fn, receiver, method, line in r.receiver_calls():
        matched = [c for c in clients if r.is_subclass(receiver, [c.fields["type"]])]
        props: dict[str, Any] = {}
        if not matched:
            # Typed only as a library base class (`FunctionCallingLLM`): every client this app
            # builds whose type extends it can be the one that runs (D47), like DI candidates
            matched = [c for c in clients if _extends(r, c, receiver)]
            props = {"candidate": True, **({"ambiguous": True} if len(matched) > 1 else {})}
        for clue in matched:
            call = (clue.fields.get("calls") or {}).get(method)
            if call is not None:  # a declarative or generated HTTP client's method
                x.edges.append(
                    EdgeFact(
                        "CALLS",
                        NodeRef.of("Function", {"symbol": fn.symbol}),
                        _http_call(clue, call),
                        dict(props),
                        Origin(
                            clue.origin.rule,
                            clue.origin.pack,
                            fn.path.relative_to(repo).as_posix(),
                            line,
                        ),
                    )
                )
                continue
            if "calls" in clue.fields:
                continue  # a method the client doesn't map isn't a call
            edge_type = _client_edge(clue, method)
            if edge_type:
                origin = Origin(
                    clue.origin.rule,
                    clue.origin.pack,
                    fn.path.relative_to(repo).as_posix(),
                    line,
                )
                x.edges.append(
                    EdgeFact(
                        edge_type,
                        NodeRef.of("Function", {"symbol": fn.symbol}),
                        clue.fields["target"],
                        dict(props),
                        origin,
                    )
                )


def _extends(r: Resolver, clue: ClueFact, receiver: str) -> bool:
    """Whether a client's type is known to inherit from `receiver`. Library classes aren't
    read, so the rule lists the library bases its client extends, the whole chain
    (`extends: [FunctionCallingLLM, LLM, BaseLLM]`)."""
    bases = clue.fields.get("extends") or []
    return isinstance(bases, list) and receiver in bases


def _passed_functions(x: Extraction, r: Resolvers, repo: Path) -> None:
    """A rule's edge to "the function parameter `p` holds" (a helper wrapping
    `FunctionTool.from_defaults(async_fn=p)`) becomes an edge from each caller of the helper
    to the function it passes: `_init_tooling` registers `_grep_search_wrapper`."""
    kept = []
    for edge in x.edges:
        dst = edge.dst
        held = dst.as_dict if isinstance(dst, NodeRef) and dst.label == "Function" else {}
        if "param_of" not in held:
            kept.append(edge)
            continue
        helper = r.function(held["param_of"])
        passed = r.for_function(helper).functions_passed(helper, held["param"]) if helper else []
        if not passed:
            x.dropped.append(
                (edge.origin, f"function passed as {held['param']!r} isn't known at any call site")
            )
            continue
        for caller, function, line in passed:
            kept.append(
                EdgeFact(
                    edge.type,
                    NodeRef.of("Function", {"symbol": caller.symbol}),
                    NodeRef.of("Function", {"symbol": function}),
                    dict(edge.props),
                    Origin(
                        edge.origin.rule,
                        edge.origin.pack,
                        caller.path.relative_to(repo).as_posix(),
                        line,
                    ),
                )
            )
    x.edges[:] = kept


def _http_call(clue: ClueFact, call: Any) -> HttpRef:
    """`calls: {getUser: {method: GET, path: /users/{id}}}` under `base`, as an HTTP target."""
    spec = call if isinstance(call, dict) else {"method": "GET", "path": str(call)}
    base = clue.fields.get("base")
    base = base if isinstance(base, str) and base != UNRESOLVED_TEXT else ""
    path = str(spec.get("path", ""))
    url = (base.rstrip("/") + "/" + path.lstrip("/")) if base and path else (base or path)
    return HttpRef(str(spec.get("method", "GET")).upper(), url or UNRESOLVED_TEXT)


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
    """The same fact found twice at the same place (two rules, or one rule matching twice).
    The same effect at two places (two saves to one table) is two facts: each is a call site."""
    seen, kept = set(), []
    for e in x.edges:
        key = (
            e.type,
            ref_key(e.src),
            ref_key(e.dst),
            tuple(sorted((k, repr(v)) for k, v in e.props.items())),
            e.origin.file,
            e.origin.line,
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
