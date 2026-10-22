"""The rule engine: run rule packs over a repository and produce facts.

See docs/how-it-works/ingestion/README.md §2.

- parse:    list the files once, build the symbol resolver's symbol table and every syntax tree
- extract:  run every rule on every file; each match emits facts, facts with blanks, and clues

The assemblers (`steno.assemblers`) then fill in the blanks from the clues.
"""

import fnmatch
import itertools
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ast_grep_py import SgNode, SgRoot

from steno.claims.types import InvalidClaim, validate
from steno.extraction.facts import (
    IDENTITY,
    UNRESOLVED_TEXT,
    AppRef,
    ClaimFact,
    ClueFact,
    EdgeFact,
    EntryPointFact,
    Extraction,
    HttpRef,
    InterceptorFact,
    NodeFact,
    NodeRef,
    Origin,
    Ref,
    TableOfRef,
    config_keys,
    database_of,
    host_of,
)
from steno.resolvers import build_resolvers
from steno.resolvers.base import UNRESOLVED, Function, Resolver, Resolvers
from steno.rule_packs.packs import EXTENSIONS, Pack, Rule
from steno.source import config_files, records
from steno.source.files import is_test_file, source_files

log = logging.getLogger(__name__)

# How each clue field is read from a capture (rules-and-packs.md §4)
CLUE_FIELDS: dict[str, dict[str, str]] = {
    "prefix": {"owner": "symbol", "value": "value"},
    "mount": {"parent": "symbol", "child": "symbol", "prefix": "value"},
    "property": {"key": "text", "value": "value"},
    # Who may call an entry point (D103): request matchers and their rule, or a method's own
    "access": {"matchers": "text", "rule": "text", "value": "text", "function": "symbol"},
    # Dependency injection: a @Bean method provides its return type (D103)
    "provides": {"function": "symbol"},
    "client": {
        "type": "class",
        "target": "ref",
        "operations": "literal",
        "returns": "literal",
        "extends": "literal",
        # Declarative and generated HTTP clients: method → {method, path}, under `base`
        "calls": "literal",
        "base": "value",
    },
}
NODE_SYMBOL_PROPS = {"prefixed_by"}  # completed later by an assembler, not stored on the node

_OMIT = object()  # an optional capture that wasn't bound: the field is left out
# Config keys whose values are secrets: never read into facts or the property map
SECRET_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|credential|private[-_.]?key|api[-_.]?key|auth[-_.]?key)"
    r"([-_.]|$)",
    re.IGNORECASE,
)
# Interfaces keyed by name across applications: an unknown name would merge unrelated channels
CHANNELS = {"KafkaTopic", "Queue"}


@dataclass
class Capture:
    text: str
    expr: Any = None  # the resolver's expression for it
    node: SgNode | None = None


@dataclass
class Ctx:
    """One match being emitted."""

    rule: Rule
    origin: Origin
    captures: dict[str, Capture]
    resolver: Resolver | None = None  # the resolver for the file's language
    scope: Any = None
    function: Function | None = None
    cls: str | None = None  # the enclosing class's symbol
    named: dict[str, NodeFact] = field(default_factory=dict)
    # A config rule matching records (`each`): the record, and the document it's in
    record: records.Node | None = None
    doc: records.Node | None = None


class Engine:
    def __init__(self, repo: Path, packs: list[Pack]):
        self.repo = repo
        self.packs = packs
        self._pack = {p.name: p for p in packs}
        self.out = Extraction()
        # One symbol resolver per language the enabled packs read (D70)
        self.resolver: Resolvers | None = None
        self.files: list[Path] = []
        self._roots: dict[Path, SgNode] = {}

    def parse(self) -> None:
        """Read the code once: list the files, read them into the symbol table, and build
        a syntax tree for every file a code rule will look at. No rules run here."""
        self.files = list(source_files(self.repo))
        languages = {p.language for p in self.packs}
        if languages & {"python", "java"}:
            self.resolver = build_resolvers(self.repo, self.files, languages)
            self.out.errors += [
                f"{p.relative_to(self.repo).as_posix() if p != self.repo else '.'}: {msg}"
                for p, msg in self.resolver.errors
            ]
        for language in {r.language for p in self.packs for r in p.rules if r.kind == "code"}:
            for path in self._code_files(language):
                self._root(path, language)

    def extract(self) -> Extraction:
        """Run every rule on every file."""
        rules = [r for p in self.packs for r in p.rules]
        # Config first: its property clues feed value resolution. Rules that only read
        # settings run before rules that state facts from config, which may use them
        config = [r for r in rules if r.kind == "config"]
        for rule in sorted(config, key=lambda r: not all("clue" in e for e in r.emit)):
            self._run_config(rule)
            self._learn_properties()
        self._learn_properties()
        for rule in rules:
            if rule.kind == "code":
                self._run_code(rule)
        # Every rule has run: the syntax trees aren't needed again, and on a large repository
        # they're a large share of the job's memory (D127)
        self._roots.clear()
        return self.out

    def _learn_properties(self) -> None:
        if self.resolver:  # one map, shared by every language's resolver
            for clue in self.out.clues:
                if clue.kind == "property" and "key" in clue.fields:
                    self.resolver.properties[clue.fields["key"]] = clue.fields.get("value")

    # ------------------------------------------------------------ code rules

    def _code_files(self, language: str) -> list[Path]:
        exts = EXTENSIONS.get(language, ())
        return [
            p for p in self.files if p.suffix in exts and not is_test_file(p.relative_to(self.repo))
        ]

    def _run_code(self, rule: Rule) -> None:
        config = {
            k: rule.match[k]
            for k in ("rule", "constraints", "utils", "transform")
            if k in rule.match
        }
        for path in self._code_files(rule.language):
            root = self._root(path, rule.language)
            if root is None:
                continue
            try:
                matches = root.find_all(config)  # type: ignore[arg-type]
            except Exception as exc:  # an invalid rule: report once, skip the rule
                self.out.errors.append(f"{rule.id}: {exc}")
                return
            for match in matches:
                self._emit_match(rule, path, match)

    def _root(self, path: Path, language: str) -> SgNode | None:
        if path not in self._roots:
            try:
                self._roots[path] = SgRoot(path.read_text(errors="replace"), language).root()
            except Exception as exc:
                self.out.errors.append(f"{path.relative_to(self.repo)}: {exc}")
                return None
        return self._roots[path]

    def _emit_match(self, rule: Rule, path: Path, match: SgNode) -> None:
        line = match.range().start.line + 1
        origin = Origin(rule.id, rule.pack, path.relative_to(self.repo).as_posix(), line)
        resolver = self.resolver.for_path(path) if self.resolver else None
        scope, fn, cls = resolver.scope_at(path, line) if resolver else (None, None, None)
        captures = {
            name: self._capture(match, name, path, resolver) for name in _capture_names(rule)
        }
        ctx = Ctx(rule, origin, {k: v for k, v in captures.items() if v}, resolver, scope, fn, cls)
        if not self._where(ctx):
            return
        self._emit(ctx)

    @staticmethod
    def _capture(match: SgNode, name: str, path: Path, resolver: Resolver | None) -> Capture | None:
        node = match.get_match(name)
        if node is None:
            many = match.get_multiple_matches(name)
            if many:
                return Capture(", ".join(n.text() for n in many))
            # A `transform:` output (ast-grep): text, e.g. a class name in snake_case
            transformed = match.get_transformed(name)
            return Capture(transformed) if transformed is not None else None
        return Capture(node.text(), resolver.expression(path, node) if resolver else None, node)

    def _where(self, ctx: Ctx) -> bool:
        r = ctx.resolver
        for var, cond in ctx.rule.where.items():
            cap = ctx.captures.get(var.lstrip("$"))
            if cap is None or cap.expr is None or r is None or ctx.scope is None:
                self._drop(ctx, f"where {var}: can't check")
                return False
            if "type" in cond and not r.is_subclass(r.type_of(cap.expr, ctx.scope), cond["type"]):
                return False  # not the type the rule is about: an expected non-match
            if "class" in cond and not r.is_subclass(
                r.class_of(cap.expr, ctx.scope), cond["class"]
            ):
                return False
        return True

    # ---------------------------------------------------------- config rules

    def _config_files(self, rule: Rule, default: list[str]) -> list[tuple[Path, str]]:
        globs = rule.files or default
        out = []
        for path in self.files:
            rel = path.relative_to(self.repo).as_posix()
            # Test configuration (src/test/resources, test fixtures) describes no running app
            if is_test_file(path.relative_to(self.repo)):
                continue
            if any(
                fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(path.name, g.rsplit("/", 1)[-1])
                for g in globs
            ):
                out.append((path, rel))
        return out

    def _run_config(self, rule: Rule) -> None:
        if "each" in rule.match:
            self._run_records(rule)
            return
        pattern = config_files.key_regex(rule.match.get("key", ""))
        for path, rel in self._config_files(rule, config_files.DEFAULT_GLOBS):
            try:
                entries = config_files.read(path)
                lines = config_files.lines(path)
            except Exception as exc:
                self.out.errors.append(f"{rel}: {exc}")
                continue
            for key, value in entries.items():
                if SECRET_KEY.search(key):
                    continue  # secret values are never ingested (docs/how-it-works/security.md)
                m = pattern.match(key)
                if m:
                    caps = {"KEY": Capture(key), "VALUE": Capture(value)} | {
                        k: Capture(v) for k, v in m.groupdict().items()
                    }
                    origin = Origin(rule.id, rule.pack, rel, lines.get(key, 0))
                    self._emit(Ctx(rule, origin, caps))

    def _run_records(self, rule: Rule) -> None:
        """A config rule over records (`each: "topics[*]"`): one match per record, whose fields
        the emits read as `$.name` (claims-and-reconcile.md §4)."""
        each, when = str(rule.match["each"]), rule.match.get("when") or {}
        for path, rel in self._config_files(rule, records.DEFAULT_GLOBS):
            try:
                docs = records.load(path)
            except Exception as exc:
                self.out.errors.append(f"{rel}: {exc}")
                continue
            for doc in docs:
                for record in records.walk(doc, each):
                    if not _when(record, doc, when):
                        continue
                    origin = Origin(rule.id, rule.pack, rel, record.line)
                    self._emit(Ctx(rule, origin, {}, record=record, doc=doc))

    # ------------------------------------------------------------------ emit

    def _emit(self, ctx: Ctx) -> None:
        for item in ctx.rule.emit:
            try:
                if "node" in item:
                    self._emit_node(ctx, item)
                elif "edge" in item:
                    self._emit_edge(ctx, item)
                elif "clue" in item:
                    self._emit_clue(ctx, item)
                elif "entry_point" in item:
                    self._emit_entry_point(ctx, item["entry_point"])
                elif "interceptor" in item:
                    self._emit_interceptor(ctx, item["interceptor"])
                elif "claim" in item:
                    self._emit_claim(ctx, item)
                else:
                    self.out.errors.append(f"{ctx.rule.id}: unknown emit {sorted(item)}")
            except _Drop as reason:
                self._drop(ctx, str(reason))

    def _emit_node(self, ctx: Ctx, item: dict[str, Any]) -> None:
        labels = item["node"] if isinstance(item["node"], list) else [item["node"]]
        props, pending = {}, {}
        for key, spec in item.items():
            if key in ("node", "as"):
                continue
            if key in NODE_SYMBOL_PROPS:
                value = self._symbol(ctx, spec)
                if value is not _OMIT:
                    pending[key] = value
                continue
            value = self._value(ctx, spec)
            if value is not _OMIT:
                props[key] = value
            elif key == "name":
                # A node's name is its identity: without it, it's a gap, not a fact
                # (`name: "@class"` without the symbol resolver)
                raise _Drop(f"{labels[-1]} name {spec!r} unresolved")
        if labels[-1] == "HttpEndpoint" and isinstance(props.get("method"), str):
            props["method"] = props["method"].upper()
        if labels[-1] == "ExternalSystem" and "host" in props:
            props["host"] = host_of(props["host"]) or UNRESOLVED_TEXT
        if "DataStore" in labels and "url" in props:
            # A connection URL names the store: vendor, host, database (credentials dropped).
            # Placeholders come from config; a default (`${DB_HOST:localhost}`) is only a
            # fallback, often a developer's own machine, so it doesn't name the store.
            url = self._placeholders(props.pop("url"))
            store = database_of(url) if "${" not in str(url) else None
            if store is None:
                raise _Drop("data store URL unresolved")
            props = {**store, **props}
        nodes = [NodeFact(labels, p, ctx.origin, dict(pending)) for p in _each(props)]
        for node in nodes:
            if _unknown_channel(node.labels, node.props):
                raise _Drop(f"{node.label} name unresolved")
        self.out.nodes.extend(nodes)
        if "as" in item:
            ctx.named[item["as"]] = nodes[0] if len(nodes) == 1 else nodes

    def _placeholders(self, value: Any) -> Any:
        """`${key}` and `${key:default}` filled from config properties, when config sets them."""
        if not isinstance(value, str) or "${" not in value:
            return value
        props = self.resolver.properties if self.resolver else {}
        return re.sub(
            r"\$\{([^}:]+)(?::[^}]*)?\}",
            lambda m: str(props[m.group(1)]) if m.group(1) in props else m.group(0),
            value,
        )

    def _emit_edge(self, ctx: Ctx, item: dict[str, Any]) -> None:
        srcs, dsts = self._refs(ctx, item["from"]), self._refs(ctx, item["to"])
        props = {}
        for key, spec in item.items():
            if key not in ("edge", "from", "to"):
                value = self._value(ctx, spec)
                if value is not _OMIT:
                    props[key] = value
        for src, dst in itertools.product(srcs, dsts):
            self.out.edges.append(EdgeFact(item["edge"], src, dst, dict(props), ctx.origin))

    def _emit_clue(self, ctx: Ctx, item: dict[str, Any]) -> None:
        kind = item["clue"]
        roles = CLUE_FIELDS.get(kind)
        if roles is None:
            raise _Drop(f"unknown clue type {kind!r}")
        fields: dict[str, Any] = {}
        for key, spec in item.items():
            if key == "clue":
                continue
            role = roles.get(key, "value")
            if role == "symbol":
                value = self._symbol(ctx, spec)
            elif role == "class":
                value = self._class(ctx, spec)
            elif role == "ref":
                value = self._ref(ctx, spec)
            elif role == "text":
                value = self._text(ctx, spec)
            elif role == "literal":
                value = spec
            else:
                value = self._value(ctx, spec)
            if value is not _OMIT:
                fields[key] = value
        self.out.clues.append(ClueFact(kind, fields, ctx.origin))

    def _emit_interceptor(self, ctx: Ctx, item: dict[str, Any]) -> None:
        function = self._anchor(ctx, item.get("function", "@function"))
        if not function:
            raise _Drop("interceptor outside a function")
        scope = item.get("applies_to", "http")
        if scope == "http":
            scope = {"http": True}
        elif isinstance(scope, dict):
            scope = {k: self._value(ctx, v) for k, v in scope.items()}
        self.out.interceptors.append(
            InterceptorFact(function, str(item.get("kind", "interceptor")), scope, ctx.origin)
        )

    def _emit_claim(self, ctx: Ctx, item: dict[str, Any]) -> None:
        """A claim (claims-and-reconcile.md §3): one per combination when a field names several
        values (`application: $.consumers[*]`), each citing the line its values came from."""
        kind = item["claim"]
        pack = self._pack.get(ctx.rule.pack)
        fields = [(k, self._claim_values(ctx, v)) for k, v in item.items() if k != "claim"]
        fields = [(k, vals) for k, vals in fields if vals]
        for combo in itertools.product(*(vals for _, vals in fields)):
            values = {k: v for (k, _), (v, _) in zip(fields, combo, strict=True)}
            lines = [line for _, line in combo if line]
            origin = Origin(
                ctx.origin.rule,
                ctx.origin.pack,
                ctx.origin.file,
                max(lines, default=ctx.origin.line),
            )
            try:
                if any("${" in str(v) for v in values.values()):
                    raise InvalidClaim(f"{kind}: a value is an unresolved ${{placeholder}}")
                validate(kind, values)
            except InvalidClaim as exc:
                self.out.dropped.append((origin, str(exc)))
                continue
            self.out.claims.append(
                ClaimFact(
                    kind,
                    values,
                    origin,
                    pack.witness_type if pack else "CODE",
                    ctx.rule.pack,
                    pack.generated if pack else False,
                )
            )

    def _claim_values(self, ctx: Ctx, spec: Any) -> list[tuple[Any, int | None]]:
        """A claim field's possible values, each with the line it came from (None: the match's)."""
        if isinstance(spec, dict):
            parts = [(k, self._claim_values(ctx, v)) for k, v in spec.items()]
            if any(not vals for _, vals in parts):
                return []
            return [
                (
                    {k: v for (k, _), (v, _) in zip(parts, combo, strict=True)},
                    max((line for _, line in combo if line), default=None),
                )
                for combo in itertools.product(*(vals for _, vals in parts))
            ]
        if isinstance(spec, str) and spec.startswith(("$.", "$doc.")) and ctx.doc is not None:
            base, path = (ctx.doc, spec[5:]) if spec.startswith("$doc.") else (ctx.record, spec[2:])
            if base is None or SECRET_KEY.search(path.rsplit(".", 1)[-1]):
                return []  # secret values are never ingested
            out: list[tuple[Any, int | None]] = []
            for node in records.walk(base, path):
                items = node.value if isinstance(node.value, list) else [node]
                for n in items:
                    value = records.plain(n)
                    if value not in (None, "") and not isinstance(value, (dict, list)):
                        out.append((str(value), n.line))
            return out
        value = self._value(ctx, spec)
        if value is _OMIT or value in (None, ""):
            return []
        if isinstance(value, list):
            return [(v, None) for v in value if v not in (None, "")]
        return [(value, None)]

    def _emit_entry_point(self, ctx: Ctx, item: dict[str, Any]) -> None:
        function = self._anchor(ctx, item["function"])
        for trigger in self._refs(ctx, item["trigger"]):
            if not isinstance(trigger, (NodeFact, NodeRef)) or not function:
                raise _Drop("entry point without a trigger node or function")
            self.out.entry_points.append(EntryPointFact(trigger, function, ctx.origin))

    # ---------------------------------------------------- reading emit values

    def _anchor(self, ctx: Ctx, spec: str) -> str | None:
        if spec == "@function":
            return ctx.function.symbol if ctx.function else None
        if spec == "@class":
            return ctx.cls
        if spec == "@file":
            return ctx.origin.file
        return None

    def _cap(self, ctx: Ctx, spec: Any) -> Capture | None | object:
        """The capture a `$NAME` spec refers to; _OMIT if unbound; None if not a capture."""
        if isinstance(spec, str) and spec.startswith("$") and spec.lstrip("$").isidentifier():
            return ctx.captures.get(spec.lstrip("$"), _OMIT)  # $X, and $$$X (several nodes)
        return None

    def _value(self, ctx: Ctx, spec: Any) -> Any:
        if isinstance(spec, dict):
            return {k: self._value(ctx, v) for k, v in spec.items()}
        if isinstance(spec, list):
            return [self._value(ctx, v) for v in spec]
        if isinstance(spec, str) and spec.startswith("@"):
            return self._anchor(ctx, spec) or _OMIT
        cap = self._cap(ctx, spec)
        if cap is None:
            return spec  # a literal
        if cap is _OMIT:
            return _OMIT
        assert isinstance(cap, Capture)
        r = ctx.resolver
        if r is None or ctx.scope is None or cap.expr is None or _names_code(r, cap.node):
            return _strip_quotes(cap.text)  # config values, method names, operators: their text
        value = r.value_of(cap.expr, ctx.scope)
        if value is UNRESOLVED:
            self._unset(ctx, cap.text)
        return UNRESOLVED_TEXT if value is UNRESOLVED else value

    def _parameter_function(self, ctx: Ctx, spec: Any) -> NodeRef | None:
        """A function reference that's one of the enclosing function's parameters, as in a
        helper that wraps `FunctionTool.from_defaults(async_fn=async_fn)`: which function it
        holds is up to each caller, so the assembler fills it in from the call sites."""
        cap = self._cap(ctx, spec)
        if not isinstance(cap, Capture) or cap.expr is None or ctx.resolver is None:
            return None
        param = ctx.resolver.parameter_of(cap.expr, ctx.scope) if ctx.scope is not None else None
        if param is not None and ctx.function is not None:
            return NodeRef.of("Function", {"param_of": ctx.function.symbol, "param": param})
        return None

    def _url_template(self, ctx: Ctx, spec: Any) -> str | None:
        """A URL the resolver can't fully know, with its runtime parts as placeholders
        (`https://{domain}/rest/api/content`): the host or the path is often still there."""
        cap = self._cap(ctx, spec)
        if not isinstance(cap, Capture) or cap.expr is None or ctx.scope is None:
            return None
        out = ctx.resolver.template_of(cap.expr, ctx.scope) if ctx.resolver else None
        self._unset(ctx, out)
        return out

    def _text(self, ctx: Ctx, spec: Any) -> Any:
        cap = self._cap(ctx, spec)
        if cap is None:
            return spec
        return _OMIT if cap is _OMIT else _strip_quotes(cap.text)  # type: ignore[union-attr]

    def _symbol(self, ctx: Ctx, spec: Any) -> Any:
        if isinstance(spec, str) and spec.startswith("@"):
            return self._anchor(ctx, spec) or _OMIT
        cap = self._cap(ctx, spec)
        if cap is None:
            return spec
        if cap is _OMIT:
            return _OMIT
        assert isinstance(cap, Capture)
        r = ctx.resolver
        if not (r and ctx.scope and cap.expr is not None):
            return cap.text
        sym = r.symbol_of(cap.expr, ctx.scope)
        if sym is None:
            raise _Drop(f"can't resolve {cap.text!r}")
        return r.canonical(sym)

    def _class(self, ctx: Ctx, spec: Any) -> Any:
        if isinstance(spec, str) and spec.startswith("@"):
            return self._anchor(ctx, spec) or _OMIT  # `type: "@class"`: the class it's in
        cap = self._cap(ctx, spec)
        if cap is None:
            return spec  # a class named literally in the rule
        if cap is _OMIT:
            return _OMIT
        assert isinstance(cap, Capture)
        cls = (
            ctx.resolver.class_of(cap.expr, ctx.scope)
            if ctx.resolver and ctx.scope and cap.expr is not None
            else None
        )
        if cls is None:
            raise _Drop(f"{cap.text!r} isn't a class")
        return cls

    def _refs(self, ctx: Ctx, spec: Any) -> list[Ref | NodeFact]:
        """A reference, or one per value when it names several (`topics = {"a", "b"}`)."""
        ref = self._ref(ctx, spec)
        return ref if isinstance(ref, list) else [ref]

    def _ref(self, ctx: Ctx, spec: Any) -> Any:
        if isinstance(spec, str):
            if spec == "@app":
                return AppRef()
            if spec == "@function":
                fn = self._anchor(ctx, spec)
                if not fn:
                    raise _Drop("@function outside a function")
                return NodeRef.of("Function", {"symbol": fn})
            if spec in ctx.named:
                return ctx.named[spec]
            raise _Drop(f"unknown reference {spec!r}")
        if not isinstance(spec, dict) or len(spec) != 1:
            raise _Drop(f"bad reference {spec!r}")
        ((kind, body),) = spec.items()
        if kind == "table_of":
            cap = self._cap(ctx, body)
            if not isinstance(cap, Capture) or cap.expr is None or ctx.scope is None:
                raise _Drop("table_of needs a captured expression")
            assert ctx.resolver is not None
            return TableOfRef(cap.expr, ctx.scope, ctx.resolver.language)
        if kind == "http":
            method = self._value(ctx, body.get("method", "GET"))
            method = "GET" if method is _OMIT or not isinstance(method, str) else method
            if "parts" in body:
                url: Any = self._url_parts(ctx, body["parts"])
            else:
                url = self._value(ctx, body.get("url"))
                if not isinstance(url, str) or url == UNRESOLVED_TEXT:
                    url = self._url_template(ctx, body.get("url"))
            return HttpRef(method.upper(), url if isinstance(url, str) else UNRESOLVED_TEXT)
        if kind == "Function":
            held = self._parameter_function(ctx, body.get("symbol"))
            if held is not None:
                return held
            symbol = self._symbol(ctx, body.get("symbol"))
            if symbol is _OMIT:
                raise _Drop("function reference without a symbol")
            if ctx.resolver and ctx.resolver.function(str(symbol)) is None:
                # A parameter or a value built at runtime: which function it holds isn't known
                raise _Drop(
                    f"function reference {str(symbol).rsplit('.', 1)[-1]!r} isn't a known function"
                )
            return NodeRef.of("Function", {"symbol": symbol})
        props = {
            k: v for k, v in ((k, self._value(ctx, v)) for k, v in body.items()) if v is not _OMIT
        }
        if kind == "ExternalSystem" and "host" in props:
            host = host_of(props.pop("host"))
            if host:
                props["host"] = host
        refs = [NodeRef.of(kind, p) for p in _each(props)]
        for r in refs:
            if _unknown_channel([kind], r.as_dict):
                raise _Drop(f"{kind} name unresolved")
        return refs[0] if len(refs) == 1 else refs

    def _url_parts(self, ctx: Ctx, parts: list[Any]) -> str | None:
        """A URL from parts (`[$BASE_URL, $PREFIX, $PATH]`), joined with one `/` between them.
        A part that isn't bound is left out; one that can't be known becomes a placeholder
        named after it, so the constant parts survive (D64)."""
        out = ""
        for spec in parts:
            value = self._value(ctx, spec)
            if value is _OMIT or value == "" or value is None:
                continue
            if not isinstance(value, (str, int, float)) or value == UNRESOLVED_TEXT:
                value = self._url_template(ctx, spec) or "{" + _placeholder(spec) + "}"
            out = _join_url(out, str(value))
        return out or None

    def _unset(self, ctx: Ctx, text: Any) -> None:
        """Remember the config keys a value needed and the repository doesn't set."""
        for key in config_keys(text):
            if not (self.resolver and key in self.resolver.properties):
                self.out.unset_config.append((ctx.origin, key))

    def _drop(self, ctx: Ctx, reason: str) -> None:
        self.out.dropped.append((ctx.origin, reason))


class _Drop(Exception):
    """A fact the rule matched but couldn't complete; recorded, not fatal."""


def _each(props: dict[str, Any]) -> list[dict[str, Any]]:
    """One set of properties per value when a value is a list (`topics = {"a", "b"}`)."""
    lists = {k: v for k, v in props.items() if isinstance(v, list) and k != "fields"}
    if not lists or not all(lists.values()):
        return [props]
    keys = list(lists)
    combos = itertools.product(*lists.values())
    return [props | dict(zip(keys, combo, strict=True)) for combo in combos]


def _when(record: records.Node, doc: records.Node, cond: dict[str, Any]) -> bool:
    """A record's filter (`when: {"$.resource.type": topic, "$.operations": {contains: Read}}`):
    every condition holds. A value equals, `contains` (in a list), `in` / `not_in` (one of),
    `matches` (a regex), or `exists` (true or false)."""
    for spec, want in cond.items():
        base, path = (
            (doc, spec[5:]) if spec.startswith("$doc.") else (record, spec.removeprefix("$."))
        )
        found = [records.plain(n) for n in records.walk(base, path)]
        values = [v for f in found for v in (f if isinstance(f, list) else [f])]
        values = [str(v) for v in values if v is not None]
        if isinstance(want, dict):
            if "exists" in want and bool(values) != bool(want["exists"]):
                return False
            if "contains" in want and str(want["contains"]) not in values:
                return False
            if "in" in want and not set(values) & {str(w) for w in want["in"]}:
                return False
            if "not_in" in want and set(values) & {str(w) for w in want["not_in"]}:
                return False
            if "matches" in want and not any(re.search(str(want["matches"]), v) for v in values):
                return False
        elif str(want) not in values:
            return False
    return True


def _unknown_channel(labels: list[str], props: dict[str, Any]) -> bool:
    channel = bool(CHANNELS & set(labels)) or ("Interface" in labels and labels[-1] not in IDENTITY)
    return channel and props.get("name") == UNRESOLVED_TEXT


def _join_url(left: str, right: str) -> str:
    if not left:
        return right
    if left.endswith("/") and right.startswith("/"):
        return left + right[1:]
    if left.endswith("/") or right.startswith("/") or left.endswith("//"):
        return left + right
    return f"{left}/{right}"


def _placeholder(spec: Any) -> str:
    return spec[1:].lower() if isinstance(spec, str) and spec.startswith("$") else "?"


def _capture_names(rule: Rule) -> set[str]:
    text = repr(rule.match) + repr(rule.emit) + repr(rule.where)
    return set(re.findall(r"\$\$?\$?([A-Z_][A-Z0-9_]*)", text))


def _names_code(r: Resolver, node: SgNode | None) -> bool:
    """True for a capture that names code rather than holding a value: an attribute or method
    name (the `post` in router.post) or a called function. Each language's grammar says which."""
    return node is not None and r.names_code(node)


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text
