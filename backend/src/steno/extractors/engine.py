"""The extractor engine: run rule packs over a repository and produce facts.

Pass 1 (docs/ingestion.md §2): every rule runs on every file and emits nodes, edges,
clues, and entry points. Pass 3 (`resolve.py`) then joins clues and resolves references.
"""

import ast
import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ast_grep_py import SgNode, SgRoot

from steno.extractors import config_files
from steno.extractors.facts import (
    UNRESOLVED_TEXT,
    AppRef,
    ClueFact,
    EdgeFact,
    EntryPointFact,
    Extraction,
    HttpRef,
    NodeFact,
    NodeRef,
    Origin,
    Ref,
    TableOfRef,
    host_of,
)
from steno.extractors.files import is_test_file, source_files
from steno.extractors.packs import EXTENSIONS, Pack, Rule
from steno.extractors.resolve import resolve
from steno.resolvers.python import UNRESOLVED, ClassInfo, FunctionInfo, PythonResolver, Scope

log = logging.getLogger(__name__)

# How each clue field is read from a capture (extractor-rules.md §4)
CLUE_FIELDS: dict[str, dict[str, str]] = {
    "prefix": {"owner": "symbol", "value": "value"},
    "mount": {"parent": "symbol", "child": "symbol", "prefix": "value"},
    "property": {"key": "text", "value": "value"},
    "client": {"type": "class", "target": "ref", "operations": "literal", "returns": "literal"},
}
NODE_SYMBOL_PROPS = {"prefixed_by"}  # resolved later by a clue resolver, not stored on the node

_OMIT = object()  # an optional capture that wasn't bound: the field is left out


@dataclass
class Capture:
    text: str
    expr: ast.expr | None = None
    node: SgNode | None = None


@dataclass
class Ctx:
    """One match being emitted."""

    rule: Rule
    origin: Origin
    captures: dict[str, Capture]
    scope: Scope | None = None
    function: FunctionInfo | None = None
    cls: ClassInfo | None = None
    named: dict[str, NodeFact] = field(default_factory=dict)


class Engine:
    def __init__(self, repo: Path, packs: list[Pack]):
        self.repo = repo
        self.packs = packs
        self.out = Extraction()
        self.resolver = PythonResolver(repo) if any(p.language == "python" for p in packs) else None
        if self.resolver:
            self.out.errors += [f"{p.relative_to(repo)}: {msg}" for p, msg in self.resolver.errors]
        self._roots: dict[Path, SgNode] = {}

    def run(self) -> Extraction:
        self.collect()
        self.resolve()
        return self.out

    def resolve(self) -> None:
        """Pass 3: join clues and resolve references."""
        resolve(self.out, self.resolver, self.repo)

    def collect(self) -> Extraction:
        """Pass 1: run every rule on every file."""
        rules = [r for p in self.packs for r in p.rules]
        for rule in rules:  # config first: its property clues feed value resolution
            if rule.kind == "config":
                self._run_config(rule)
        if self.resolver:
            for clue in self.out.clues:
                if clue.kind == "property" and "key" in clue.fields:
                    self.resolver.properties[clue.fields["key"]] = clue.fields.get("value")
        for rule in rules:
            if rule.kind == "code":
                self._run_code(rule)
        return self.out

    # ------------------------------------------------------------ code rules

    def _run_code(self, rule: Rule) -> None:
        config = {
            k: rule.match[k]
            for k in ("rule", "constraints", "utils", "transform")
            if k in rule.match
        }
        for path in source_files(self.repo, EXTENSIONS.get(rule.language, ())):
            if is_test_file(path.relative_to(self.repo)):
                continue
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
        fn = cls = None
        scope = None
        if self.resolver:
            fn, cls = self.resolver.enclosing(path, line)
            module = self.resolver.module_at(path)
            scope = Scope(module, fn) if module else None
        captures = {name: self._capture(match, name) for name in _capture_names(rule)}
        ctx = Ctx(rule, origin, {k: v for k, v in captures.items() if v}, scope, fn, cls)
        if not self._where(ctx):
            return
        self._emit(ctx)

    @staticmethod
    def _capture(match: SgNode, name: str) -> Capture | None:
        node = match.get_match(name)
        if node is None:
            many = match.get_multiple_matches(name)
            return Capture(", ".join(n.text() for n in many)) if many else None
        try:
            expr = ast.parse(node.text().strip(), mode="eval").body
        except SyntaxError:
            expr = None
        return Capture(node.text(), expr, node)

    def _where(self, ctx: Ctx) -> bool:
        r = self.resolver
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

    def _run_config(self, rule: Rule) -> None:
        pattern = config_files.key_regex(rule.match.get("key", ""))
        globs = rule.files or config_files.DEFAULT_GLOBS
        for path in source_files(self.repo):
            rel = path.relative_to(self.repo).as_posix()
            if not any(
                fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(path.name, g.rsplit("/", 1)[-1])
                for g in globs
            ):
                continue
            try:
                entries = config_files.read(path)
            except Exception as exc:
                self.out.errors.append(f"{rel}: {exc}")
                continue
            for key, value in entries.items():
                m = pattern.match(key)
                if m:
                    caps = {"KEY": Capture(key), "VALUE": Capture(value)} | {
                        k: Capture(v) for k, v in m.groupdict().items()
                    }
                    self._emit(Ctx(rule, Origin(rule.id, rule.pack, rel, 0), caps))

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
        if labels[-1] == "HttpEndpoint" and isinstance(props.get("method"), str):
            props["method"] = props["method"].upper()
        if labels[-1] == "ExternalSystem" and "host" in props:
            props["host"] = host_of(props["host"]) or UNRESOLVED_TEXT
        node = NodeFact(labels, props, ctx.origin, pending)
        self.out.nodes.append(node)
        if "as" in item:
            ctx.named[item["as"]] = node

    def _emit_edge(self, ctx: Ctx, item: dict[str, Any]) -> None:
        src, dst = self._ref(ctx, item["from"]), self._ref(ctx, item["to"])
        props = {}
        for key, spec in item.items():
            if key not in ("edge", "from", "to"):
                value = self._value(ctx, spec)
                if value is not _OMIT:
                    props[key] = value
        self.out.edges.append(EdgeFact(item["edge"], src, dst, props, ctx.origin))

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

    def _emit_entry_point(self, ctx: Ctx, item: dict[str, Any]) -> None:
        trigger = self._ref(ctx, item["trigger"])
        function = self._anchor(ctx, item["function"])
        if not isinstance(trigger, (NodeFact, NodeRef)) or not function:
            raise _Drop("entry point without a trigger node or function")
        self.out.entry_points.append(EntryPointFact(trigger, function, ctx.origin))

    # ---------------------------------------------------- reading emit values

    def _anchor(self, ctx: Ctx, spec: str) -> str | None:
        if spec == "@function":
            return ctx.function.qualname if ctx.function else None
        if spec == "@class":
            return ctx.cls.qualname if ctx.cls else None
        if spec == "@file":
            return ctx.origin.file
        return None

    def _cap(self, ctx: Ctx, spec: Any) -> Capture | None | object:
        """The capture a `$NAME` spec refers to; _OMIT if unbound; None if not a capture."""
        if isinstance(spec, str) and spec.startswith("$") and spec[1:].isidentifier():
            return ctx.captures.get(spec[1:], _OMIT)
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
        if ctx.scope is None or cap.expr is None or _names_code(cap.node):
            return _strip_quotes(cap.text)  # config values, method names, operators: their text
        value = self.resolver.value_of(cap.expr, ctx.scope) if self.resolver else UNRESOLVED
        return UNRESOLVED_TEXT if value is UNRESOLVED else value

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
        if not (self.resolver and ctx.scope and cap.expr is not None):
            return cap.text
        sym = self.resolver.symbol_of(cap.expr, ctx.scope)
        if sym is None:
            raise _Drop(f"can't resolve {cap.text!r}")
        return self.resolver.canonical(sym)

    def _class(self, ctx: Ctx, spec: Any) -> Any:
        cap = self._cap(ctx, spec)
        if cap is None:
            return spec  # a class named literally in the rule
        if cap is _OMIT:
            return _OMIT
        assert isinstance(cap, Capture)
        cls = (
            self.resolver.class_of(cap.expr, ctx.scope)
            if self.resolver and ctx.scope and cap.expr
            else None
        )
        if cls is None:
            raise _Drop(f"{cap.text!r} isn't a class")
        return cls

    def _ref(self, ctx: Ctx, spec: Any) -> Ref | NodeFact:
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
            return TableOfRef(cap.expr, ctx.scope)
        if kind == "http":
            method = self._value(ctx, body.get("method", "GET"))
            url = self._value(ctx, body.get("url"))
            return HttpRef(str(method).upper(), url if isinstance(url, str) else UNRESOLVED_TEXT)
        if kind == "Function":
            symbol = self._symbol(ctx, body.get("symbol"))
            if symbol is _OMIT:
                raise _Drop("function reference without a symbol")
            return NodeRef.of("Function", {"symbol": symbol})
        props = {
            k: v for k, v in ((k, self._value(ctx, v)) for k, v in body.items()) if v is not _OMIT
        }
        if kind == "ExternalSystem" and "host" in props:
            host = host_of(props.pop("host"))
            if host:
                props["host"] = host
        return NodeRef.of(kind, props)

    def _drop(self, ctx: Ctx, reason: str) -> None:
        self.out.dropped.append((ctx.origin, reason))


class _Drop(Exception):
    """A fact the rule matched but couldn't complete; recorded, not fatal."""


def _capture_names(rule: Rule) -> set[str]:
    import re

    text = repr(rule.match) + repr(rule.emit) + repr(rule.where)
    return set(re.findall(r"\$\$?\$?([A-Z_][A-Z0-9_]*)", text))


def _names_code(node: SgNode | None) -> bool:
    """True for a capture that names code rather than holding a value: an attribute name
    (the `post` in router.post) or a called function (`update` in update(Model))."""
    if node is None:
        return False
    parent = node.parent()
    if parent is None:
        return False
    for field_name in ("attribute", "function"):
        child = parent.field(field_name)
        if child is not None and _span(child) == _span(node):
            return parent.kind() in ("attribute", "call")
    return False


def _span(node: SgNode) -> tuple[int, int, int, int]:
    r = node.range()
    return r.start.line, r.start.column, r.end.line, r.end.column


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def extract(repo: Path, packs: list[Pack]) -> Extraction:
    return Engine(repo, packs).run()
