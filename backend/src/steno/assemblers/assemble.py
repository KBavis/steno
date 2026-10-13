"""The assemblers: fill in extracted facts' blanks from clues.

See docs/how-it-works/ingestion/stages.md §5 and rules-and-packs.md §4.

Each clue type has exactly one assembler here:
- prefix + mount → prefix chains (full endpoint paths)
- client         → calls through SDK client objects
- property       → config values (fed to the symbol resolver before values are read)
plus the references a rule can't complete on its own: `table_of` and partial identities.
"""

import re
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
    _access(x)
    if r:
        _tables(x, r)
        _queries(x, r)
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


# ---------------------------------------------------------------------- access

ACCESS = {
    "permitAll": "public",
    "anonymous": "anonymous",
    "authenticated": "authenticated",
    "fullyAuthenticated": "authenticated",
    "denyAll": "denied",
}


def _access(x: Extraction) -> None:
    """Who may call each HTTP endpoint (D103): `access` from request matchers
    (`requestMatchers("/auth/**").permitAll()`; the most specific matcher wins, one naming the
    method before one that doesn't, `anyRequest()` last) or the handler's own annotation
    (`@PreAuthorize("hasRole('ADMIN')")`, which wins over matchers)."""
    rules = []  # (method, pattern regex, specificity, access)
    by_function = {}
    for c in x.clues:
        if c.kind != "access":
            continue
        rule = _rule_text(str(c.fields.get("rule", "")), str(c.fields.get("value", "")))
        if rule is None:
            continue
        if c.fields.get("function"):
            by_function[c.fields["function"]] = rule
            continue
        text = str(c.fields.get("matchers", ""))
        methods = re.findall(r"HttpMethod\.(\w+)", text)
        patterns = [t for t in _tokens(text) if t.startswith("/")] or ["/**"]
        for pattern in patterns:
            literal = len(re.split(r"[*{]", pattern, maxsplit=1)[0])
            rules.append(
                (methods[0] if methods else None, _ant(pattern), (bool(methods), literal), rule)
            )
    handler_of = {
        id(ep.trigger): ep.function for ep in x.entry_points if isinstance(ep.trigger, NodeFact)
    }
    for node in x.nodes:
        if node.label != "HttpEndpoint" or not isinstance(node.props.get("path"), str):
            continue
        own = by_function.get(handler_of.get(id(node)))
        if own:
            node.props["access"] = own
            continue
        method, path = node.props.get("method"), re.sub(r"\{[^}]*\}", "x", node.props["path"])
        matching = [
            (spec, rule)
            for m, rx, spec, rule in rules
            if (m is None or m == method) and rx.match(path)
        ]
        if matching:
            node.props["access"] = max(matching, key=lambda r: r[0])[1]


def _rule_text(rule: str, value: str) -> str | None:
    if rule in ACCESS:
        return ACCESS[rule]
    names = _tokens(value)
    if rule in ("hasRole", "hasAnyRole", "hasAuthority", "hasAnyAuthority"):
        return "role:" + ",".join(names) if names else "role"
    if rule == "expression":  # @PreAuthorize("hasRole('ADMIN')")
        inner = re.findall(r"has(?:Any)?(?:Role|Authority)\(([^)]*)\)", value)
        if inner:
            return "role:" + ",".join(re.findall(r"'([^']+)'", inner[0]))
        if "permitAll" in value:
            return "public"
        if "isAuthenticated" in value:
            return "authenticated"
        return f"expression:{value.strip(chr(34))}"
    return None


def _tokens(text: str) -> list[str]:
    """Arguments as text (`"/a", ,, "/b"` or `/a`), quotes and separators dropped."""
    return [t.strip().strip("\"'") for t in text.split(",") if t.strip().strip("\"'")]


def _ant(pattern: str) -> re.Pattern[str]:
    """An Ant-style path pattern: `**` any segments, `*` one segment's characters."""
    out = ""
    for token in re.findall(r"\*\*|\*|[^*]+", pattern):
        out += ".*" if token == "**" else "[^/]*" if token == "*" else re.escape(token)
    if out.endswith("/.*"):
        out = out[:-3] + "(/.*)?"
    return re.compile(f"^{out}$")


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
                if edge.type == "WRITES_TO" and resolver is not None and entity:
                    kept += _cascaded(edge, entity, resolver, entity_table)
        else:
            kept.append(edge)
    x.edges = kept


# Which cascade types carry which write (JPA): a save persists or merges, a delete removes
CASCADES = {
    "insert": {"ALL", "PERSIST"},
    "upsert": {"ALL", "PERSIST", "MERGE"},
    "update": {"ALL", "MERGE"},
    "delete": {"ALL", "REMOVE"},
}


def _cascaded(
    edge: EdgeFact, entity: str, resolver: Resolver, entity_table: dict[Any, Any], depth: int = 0
) -> list[EdgeFact]:
    """Writes an ORM makes for you: saving a `Category` also writes its `validTimes` rows into
    `category_vt` when the relationship cascades (`@OneToMany(cascade = ALL)`)."""
    cascades = getattr(resolver, "cascades", None)
    if cascades is None or depth > 3:
        return []
    wanted = CASCADES.get(str(edge.props.get("operation")), {"ALL"})
    out = []
    for target, field_name, kinds in cascades(entity):
        table = entity_table.get(target)
        if table is None or not wanted & set(kinds):
            continue
        child = EdgeFact(
            edge.type,
            edge.src,
            NodeRef.of("Table", {"name": table}),
            {
                **edge.props,
                # the relationships it goes through, all of which the flow must touch (D102)
                "cascade": " > ".join(
                    [*filter(None, [edge.props.get("cascade")]), f"{entity}.{field_name}"]
                ),
            },
            edge.origin,
        )
        out.append(child)
        out += _cascaded(child, target, resolver, entity_table, depth + 1)
    return out


# `FROM Category c`, `JOIN c.validTimes vt`, `JOIN FETCH t.account a`, `UPDATE Order o`
_JPQL_SOURCE = re.compile(
    r"\b(FROM|JOIN(?:\s+FETCH)?|UPDATE|INTO)\s+([A-Za-z_][\w.]*)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?",
    re.IGNORECASE,
)
_SQL_KEYWORDS = {"select", "where", "on", "set", "join", "left", "inner", "order", "group", "as"}


def _queries(x: Extraction, rs: Resolvers) -> None:
    """A repository method's own query (`@Query`): the tables it reads, and for an UPDATE,
    DELETE, or INSERT, writes. JPQL names entities and follows their relationships
    (`JOIN c.validTimes` reads `category_vt`); native SQL names tables."""
    java = rs.by_language.get("java")
    queries = getattr(java, "queries", None)
    if not queries:
        return
    entity_table = {
        e.src.props.get("name"): e.dst.props.get("name")
        for e in x.edges
        if e.type == "MAPS_TO" and isinstance(e.src, NodeFact) and isinstance(e.dst, NodeFact)
    }
    by_simple = {name.rsplit(".", 1)[-1]: name for name in entity_table if isinstance(name, str)}
    for symbol, (query, native) in queries.items():
        fn = rs.function(symbol)
        if fn is None:
            continue
        verb = query.strip().split(None, 1)[0].lower() if query.strip() else ""
        writes = {"update": "update", "delete": "delete", "insert": "insert"}.get(verb)
        tables: dict[str, bool] = {}  # table → is it the one written
        aliases: dict[str, str] = {}
        for kw, name, alias in _JPQL_SOURCE.findall(query):
            target = writes is not None and kw.lower() in ("update", "into", "from") and not tables
            if native:
                tables.setdefault(name.split(".")[-1], target)
                continue
            if "." in name and name.split(".")[0] in aliases:  # JOIN c.validTimes
                head, field_name = name.split(".", 1)
                entity = java.relation_target(aliases[head], field_name)  # type: ignore[union-attr]
            else:
                entity = by_simple.get(name.rsplit(".", 1)[-1])
            if entity is None:
                continue
            if alias and alias.lower() not in _SQL_KEYWORDS:
                aliases[alias] = entity
            table = entity_table.get(entity)
            if table:
                tables.setdefault(table, target)
        origin = Origin(
            "query-tables", "core", fn.path.relative_to(java.root).as_posix(), fn.span[0]
        )  # type: ignore[union-attr]
        for table, written in tables.items():
            src = NodeRef.of("Function", {"symbol": symbol})
            dst = NodeRef.of("Table", {"name": table})
            if written and writes:
                x.edges.append(EdgeFact("WRITES_TO", src, dst, {"operation": writes}, origin))
            else:
                x.edges.append(EdgeFact("READS_FROM", src, dst, {"operation": "select"}, origin))


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
