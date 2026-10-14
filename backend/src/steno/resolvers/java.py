"""Steno's Java symbol resolver (D69, docs/how-it-works/ingestion/resolvers.md §5.2).

Java's symbol table lives in the JVM helper (`resolver-jvm/`, JavaParser's symbol solver plus
Steno's own lookups). Asking it one question at a time would cost a round trip each, so the
helper reads the whole repository once and writes everything the engine could ask: every
function and type, every call site (what it calls, or why that can't be told), and the symbol,
type, class, and value of every expression a rule may capture, keyed by file and position. This
resolver looks the answers up and offers the same interface as the Python one.

It reads the repository's source and the JDK; dependency JARs come later. A library type is still
named from its import, but what a library method returns isn't known, so a chain through one stops
there, and the call says so (D71). Lombok-generated members are added by the helper (D72).
"""

import json
import logging
import re
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ast_grep_py import SgNode

from steno.config import get_settings
from steno.resolvers.base import (
    UNRESOLVED,
    Hook,
    Invocation,
    LibraryCall,
    UnresolvedCall,
    span,
)
from steno.source.files import is_test_file, source_files

log = logging.getLogger(__name__)

# Bean Validation's default group: what `@Valid` and constraints without `groups` use
DEFAULT_GROUP = "jakarta.validation.groups.Default"
# JDK packages that reach the network or a database; the rest of the JDK isn't a library to cover
JDK_IO = {"java.net", "java.sql", "java.nio", "java.io", "javax.net", "javax.sql"}
# The `javax` packages the JDK ships (17+). The rest (`javax.servlet`, `javax.persistence`,
# `javax.validation`, `javax.jms`) are Java EE libraries: Spring Boot 2 code imports them
JDK_JAVAX = {
    "javax.accessibility", "javax.annotation", "javax.crypto", "javax.imageio", "javax.lang",
    "javax.management", "javax.naming", "javax.net", "javax.print", "javax.rmi", "javax.script",
    "javax.security", "javax.smartcardio", "javax.sound", "javax.sql", "javax.swing",
    "javax.tools", "javax.xml",
}  # fmt: skip
_PROPERTY = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
_DOMAINS = {"com", "org", "io", "net", "dev", "de", "uk", "co", "me", "ch", "fr", "nl", "app"}


@dataclass(frozen=True)
class JavaExpr:
    """An expression a rule captured, by where it is: JavaParser's positions (1-based lines and
    columns, end inclusive), the helper's key for what it resolved there."""

    file: str
    sl: int
    sc: int
    el: int
    ec: int
    text: str


@dataclass(frozen=True)
class JavaFunction:
    symbol: str  # `com.x.PassServiceImpl#callToFunction(com.x.ClientRequest)`
    name: str
    container: str  # the class it's declared in
    path: Path
    span: tuple[int, int]
    param_names: tuple[str, ...]
    is_async: bool
    doc: str | None
    body_hash: str
    is_abstract: bool
    # Parameters annotated @Value("${key}"): their value comes from config
    param_values: tuple[tuple[str, str], ...] = ()
    language: str = "java"
    in_class: bool = True

    @property
    def params(self) -> dict[str, Any]:
        return {name: None for name in self.param_names}


@dataclass(frozen=True)
class JavaScope:
    file: str
    function: JavaFunction | None


@dataclass
class JavaType:
    name: str
    file: str
    span: tuple[int, int]
    kind: str
    is_abstract: bool
    bases: list[str] = field(default_factory=list)
    entity: str | None = None  # a Spring Data repository's entity
    annotations: list[str] = field(default_factory=list)
    # JPA relationships: {field, kind, target, cascade?}
    relations: list[dict[str, Any]] = field(default_factory=list)
    usages: list[dict[str, Any]] = field(default_factory=list)  # annotation usages, with groups
    valid_fields: list[dict[str, str]] = field(default_factory=list)  # fields marked @Valid
    base_args: dict[str, list[str]] = field(default_factory=dict)  # Base → its type arguments


class JavaResolver:
    language = "java"
    extensions = (".java",)

    def __init__(self, root: Path, files: list[Path] | None = None, jars: list[Path] | None = None):
        self.root = root
        self.errors: list[tuple[Path, str]] = []
        self.properties: dict[str, Any] = {}
        self.return_hints: dict[tuple[str, str], str] = {}  # not used yet: no library types
        sources = [
            p
            for p in (files if files is not None else source_files(root, (".java",)))
            if p.suffix == ".java" and not is_test_file(p.relative_to(root))
        ]
        self.language_level = java_version(root)
        data = self._run(sources, jars or []) if sources else {}
        self._stats: dict[str, Any] = data.get("stats", {})
        for e in data.get("errors", []):
            self.errors.append((root / e["file"], e["message"]))
        self.types = {
            t["name"]: JavaType(
                t["name"],
                t["file"],
                (t.get("start", 0), t.get("end", 0)),
                t["kind"],
                bool(t.get("abstract")),
                t.get("bases", []),
                t.get("entity"),
                t.get("annotations", []),
                t.get("relations", []),
                t.get("usages", []),
                t.get("valid_fields", []),
                t.get("base_args", {}),
            )
            for t in data.get("types", [])
        }
        self._functions = {
            f["symbol"]: JavaFunction(
                f["symbol"],
                f["name"],
                f["container"],
                root / f["file"],
                (f.get("start", 0), f.get("end", 0)),
                tuple(f.get("params", [])),
                bool(f.get("async")),
                f.get("doc"),
                f.get("hash", ""),
                bool(f.get("abstract")),
                tuple((f.get("param_values") or {}).items()),
            )
            for f in data.get("functions", [])
        }
        self._parameters = {
            f["symbol"]: f["parameters"] for f in data.get("functions", []) if f.get("parameters")
        }
        # What a method returns, when its body is that implementation (a lambda bean)
        self._returns = {
            f["symbol"]: f["returns"]
            for f in data.get("functions", [])
            if f.get("returns") and f.get("returns_implementation")
        }
        self._accessors = {
            f["symbol"]: f["accessors"] for f in data.get("functions", []) if f.get("accessors")
        }
        self._annotations = {
            f["symbol"]: f["annotations"] for f in data.get("functions", []) if f.get("annotations")
        }
        # Repository methods with their own query: symbol → (query text, native SQL?)
        self.queries = {
            f["symbol"]: (f["query"], bool(f.get("native")))
            for f in data.get("functions", [])
            if f.get("query")
        }
        self._implementations = {
            f["symbol"]: f["implementations"]
            for f in data.get("functions", [])
            if f.get("implementations")
        }
        self._calls: dict[str, list[dict[str, Any]]] = data.get("calls", {})
        self._exprs: dict[tuple[str, int, int, int, int], dict[str, Any]] = {}
        self._by_lines: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        for e in data.get("exprs", []):
            self._exprs[(e["file"], e["sl"], e["sc"], e["el"], e["ec"])] = e
            self._by_lines.setdefault((e["file"], e["sl"], e["el"]), []).append(e)
        self._imports: dict[str, list[str]] = data.get("imports", {})
        self._by_file: dict[str, list[JavaFunction]] = {}
        for fn in self._functions.values():
            self._by_file.setdefault(self._rel(fn.path), []).append(fn)
        self._packages = {t.rsplit(".", 1)[0] for t in self.types}
        self._ancestors: dict[str, list[str]] = {}

    # ------------------------------------------------------------- the helper

    def _run(self, sources: list[Path], jars: list[Path]) -> dict[str, Any]:
        settings = get_settings()
        lib = settings.resolver_jvm_dir / "lib"
        if not lib.is_dir():
            self.errors.append(
                (self.root, f"Java resolver unavailable: {lib} not found (run `make resolver`)")
            )
            return {}
        with tempfile.TemporaryDirectory(prefix="steno-java-") as tmp:
            listing, out, jar_list = (
                Path(tmp, "files.txt"),
                Path(tmp, "out.json"),
                Path(tmp, "jars.txt"),
            )
            listing.write_text("\n".join(str(p) for p in sources))
            jar_list.write_text("\n".join(str(j) for j in jars))
            cmd = [
                settings.java_bin,
                "-Xss8m",
                f"-Xmx{settings.resolver_jvm_max_heap}",
                "-cp",
                f"{lib}/*",
                "dev.steno.resolver.Main",
                "--root",
                str(self.root),
                "--files",
                str(listing),
                "--out",
                str(out),
                "--jars",
                str(jar_list),
                *(["--language-level", self.language_level] if self.language_level else []),
            ]
            try:
                done = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=settings.resolver_jvm_timeout
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.errors.append((self.root, f"Java resolver failed: {exc}"))
                return {}
            if done.returncode != 0 or not out.exists():
                tail = (done.stderr or "").strip().splitlines()[-3:]
                self.errors.append((self.root, f"Java resolver failed: {' | '.join(tail)}"))
                return {}
            return json.loads(out.read_text())

    # ------------------------------------------------- reading a rule's match

    def expression(self, path: Path, node: SgNode) -> JavaExpr:
        sl, sc, el, ec = span(node)
        return JavaExpr(self._rel(path), sl + 1, sc + 1, el + 1, ec, node.text())

    def scope_at(self, path: Path, line: int) -> tuple[JavaScope, JavaFunction | None, str | None]:
        rel = self._rel(path)
        inside = [f for f in self._by_file.get(rel, []) if f.span[0] <= line <= f.span[1]]
        fn = min(inside, key=lambda f: f.span[1] - f.span[0]) if inside else None
        types = [t for t in self.types.values() if t.file == rel and t.span[0] <= line <= t.span[1]]
        cls = min(types, key=lambda t: t.span[1] - t.span[0]).name if types else None
        return JavaScope(rel, fn), fn, (fn.container if fn else cls)

    @staticmethod
    def names_code(node: SgNode) -> bool:
        """A capture that names code: a method's name in a call, a field's in an access, an
        annotation's name."""
        parent = node.parent()
        if parent is None:
            return False
        for kind, field_name in (
            ("method_declaration", "name"),
            ("class_declaration", "name"),
            ("interface_declaration", "name"),
            ("method_invocation", "name"),
            ("field_access", "field"),
            ("marker_annotation", "name"),
            ("annotation", "name"),
            ("method_reference", None),
        ):
            if parent.kind() != kind:
                continue
            if field_name is None:
                return True
            child = parent.field(field_name)
            if child is not None and span(child) == span(node):
                return True
        return False

    def _entry(self, expr: Any) -> dict[str, Any]:
        if not isinstance(expr, JavaExpr):
            return {}
        found = self._exprs.get((expr.file, expr.sl, expr.sc, expr.el, expr.ec))
        if found is not None:
            return found
        # Columns can disagree on lines with tabs or non-ASCII text: same lines, same width
        width = expr.ec - expr.sc
        for e in self._by_lines.get((expr.file, expr.sl, expr.el), []):
            if e["ec"] - e["sc"] == width:
                return e
        return {}

    # ------------------------------------------------------- what is this?

    def symbol_of(self, expr: Any, scope: Any) -> str | None:
        return self._entry(expr).get("symbol")

    def canonical(self, symbol: str) -> str:
        return symbol

    def class_of(self, expr: Any, scope: Any) -> str | None:
        return self._entry(expr).get("class")

    def type_of(self, expr: Any, scope: Any) -> str | None:
        return self._entry(expr).get("type")

    def value_of(self, expr: Any, scope: Any) -> Any:
        entry = self._entry(expr)
        if "value" in entry:
            value = entry["value"]
            return self._fill(value) if isinstance(value, str) else value
        if isinstance(expr, JavaExpr):
            literal = _literal(expr.text)
            if literal is not UNRESOLVED:
                return self._fill(literal) if isinstance(literal, str) else literal
        return UNRESOLVED

    def template_of(self, expr: Any, scope: Any) -> str | None:
        entry = self._entry(expr)
        template = entry.get("template")
        if template is None and isinstance(expr, JavaExpr):
            literal = entry.get("value", _literal(expr.text))
            if isinstance(literal, str):  # "${base.url}/users": config, or a placeholder
                template = literal
        if template is None:
            value = self.value_of(expr, scope)
            return str(value) if isinstance(value, (str, int, float)) else None
        out = self._fill(template, placeholders=True)
        return out if isinstance(out, str) and re.sub(r"\{[^{}]*\}", "", out) else None

    def _fill(self, text: str, placeholders: bool = False) -> Any:
        """Spring's `${key}` and `${key:default}` from config (the property map, D70). A key
        config doesn't set and no default for: the value is unknown, or a `{key}` placeholder."""
        missing = False

        def sub(m: re.Match[str]) -> str:
            nonlocal missing
            key, default = m.group(1), m.group(2)
            if key in self.properties and isinstance(self.properties[key], (str, int, float)):
                return str(self.properties[key])
            if default is not None:
                return default
            missing = True
            # Kept as written, so whoever reads the URL sees which setting holds the value
            return "${" + key + "}"

        out = _PROPERTY.sub(sub, text)
        return UNRESOLVED if missing and not placeholders else out

    def is_subclass(self, qualname: str | None, targets: list[str]) -> bool:
        if not qualname:
            return False
        if qualname in targets or any(a in targets for a in self.ancestors(qualname)):
            return True
        return any(self._imported_base(t, targets) for t in [qualname, *self.ancestors(qualname)])

    def _imported_base(self, qualname: str, targets: list[str]) -> bool:
        """A base named only by its simple name (`extends JpaRepository<…>` with several
        on-demand imports) is a target when its file imports the target's package on demand."""
        t = self.types.get(qualname)
        if t is None:
            return False
        imports = set(self._imports.get(t.file, []))
        return any(
            "." not in base
            and target.rsplit(".", 1)[-1] == base
            and target.rsplit(".", 1)[0] + ".*" in imports
            for base in t.bases
            for target in targets
        )

    def ancestors(self, qualname: str) -> list[str]:
        """Every ancestor of a repository type, nearest first (library ones by name)."""
        if qualname not in self._ancestors:
            out: list[str] = []
            todo = list(self.types[qualname].bases) if qualname in self.types else []
            seen = {qualname}
            while todo:
                b = todo.pop(0)
                if b in seen:
                    continue
                seen.add(b)
                out.append(b)
                if b in self.types:
                    todo.extend(self.types[b].bases)
            self._ancestors[qualname] = out
        return self._ancestors[qualname]

    def entity_of(self, expr: Any, scope: Any) -> str | None:
        """The entity behind an expression: the class itself, an instance's class, or the
        entity a Spring Data repository manages (`orders.save(o)` → Order)."""
        cls = self.class_of(expr, scope) or self.type_of(expr, scope)
        for t in [cls, *self.ancestors(cls)] if cls else []:
            if t in self.types and self.types[t].entity:
                return self.types[t].entity
        return cls

    def cascades(self, entity: str) -> list[tuple[str, str, list[str]]]:
        """An entity's JPA relationships that cascade: (target entity, field, cascade types)."""
        t = self.types.get(entity)
        return [
            (r["target"], r["field"], r["cascade"])
            for r in (t.relations if t else [])
            if r.get("cascade")
        ]

    def returns_of(self, symbol: str) -> str | None:
        """The type a method returns its own implementation of (a @Bean whose body is a lambda,
        a method reference, or an anonymous class): a call on that type runs this code."""
        return self._returns.get(symbol)

    def validation_applies(self, validator: str, entry: str) -> bool:
        """Whether Bean Validation runs `validator` (a `ConstraintValidator<A, T>`) when the
        framework calls `entry`: a parameter marked `@Valid` (Default group) or `@Validated(G)`
        whose type, or a type it cascades into through `@Valid` fields, carries constraint `A`
        in an active group."""
        t = self.types.get(validator)
        args = next(
            (
                v
                for k, v in (t.base_args.items() if t else [])
                if k.endswith(".ConstraintValidator")
            ),
            None,
        )
        if not args:
            return False
        constraint = args[0]
        for p in self._parameters.get(entry, []):
            active: set[str] | None = None
            for a in p["annotations"]:
                name = a["annotation"]
                if name.endswith(".Valid") or name == "Valid":
                    active = {DEFAULT_GROUP}
                elif name.endswith(".Validated") or name == "Validated":
                    active = set(a.get("groups") or []) or {DEFAULT_GROUP}
            if active is not None and self._carries(p["type"], constraint, active, set()):
                return True
        # A constraint directly on a parameter (`@MonthYearConstraint MonthYear m`)
        return any(
            a["annotation"] == constraint
            for p in self._parameters.get(entry, [])
            for a in p["annotations"]
        )

    def _carries(
        self, type_name: str | None, constraint: str, active: set[str], seen: set[str]
    ) -> bool:
        if not type_name or type_name in seen:
            return False
        seen.add(type_name)
        t = self.types.get(type_name)
        if t is None:
            return False
        for u in t.usages:
            if u["annotation"] != constraint:
                continue
            groups = {_group(g) for g in u.get("groups") or []} or {DEFAULT_GROUP}
            if groups & {_group(g) for g in active}:
                return True
        return any(self._carries(f["type"], constraint, active, seen) for f in t.valid_fields)

    def touched_fields(self, fn: JavaFunction) -> set[str]:
        """Entity relationships `fn` reads or sets: `category.getValidTimes()`,
        `user.setAccounts(...)`, `Category.builder().validTimes(...)`, whether the accessor is
        Lombok's or written by hand (`entity.field`)."""
        members = list(self._accessors.get(fn.symbol, []))
        members += [c["callee"] for c in self._calls.get(fn.symbol, []) if c.get("callee")]
        out = set()
        for member in members:
            owner, _, name = member.partition("#")
            name = name.split("(", 1)[0]
            if owner.endswith("Builder") and "." in owner:  # Category.CategoryBuilder
                owner = owner.rsplit(".", 1)[0]
            field_name = name
            for prefix in ("get", "set", "add", "is"):
                if name.startswith(prefix) and name[len(prefix) : len(prefix) + 1].isupper():
                    field_name = name[len(prefix)].lower() + name[len(prefix) + 1 :]
                    break
            t = self.types.get(owner)
            if t is not None and any(r["field"] == field_name for r in t.relations):
                out.add(f"{owner}.{field_name}")
        return out

    def relation_target(self, entity: str, field_name: str) -> str | None:
        t = self.types.get(entity)
        return next(
            (r["target"] for r in (t.relations if t else []) if r["field"] == field_name), None
        )

    def annotations_of(self, symbol: str) -> list[str]:
        return self._annotations.get(symbol, [])

    def hook_candidates(self) -> Iterator[Hook]:
        """Framework types this code extends or implements, and framework annotations on its
        classes and methods (D101). The framework calls a hook class's overriding methods."""
        for t in self.types.values():
            if t.kind == "annotation":
                continue
            header = (t.span[0], t.span[0] + len(t.annotations))
            for base in t.bases:
                if base not in self.types:
                    yield Hook(t.name, "extends", base, t.file, header, self._overrides(t))
            for a in t.annotations:
                if a not in self.types:
                    yield Hook(t.name, "annotation", a, t.file, header)
        for fn in self._functions.values():
            for a in self._annotations.get(fn.symbol, []):
                if a not in self.types and a != "java.lang.Override":
                    header = (fn.span[0], fn.span[0] + len(self._annotations[fn.symbol]))
                    yield Hook(fn.symbol, "annotation", a, self._rel(fn.path), header, (fn.symbol,))

    def _overrides(self, t: JavaType) -> tuple[str, ...]:
        """Methods of `t` that override a framework type's (`@Override`, and no first-party
        ancestor declares them): what the framework calls."""
        ours = {
            f.name
            for a in self.ancestors(t.name)
            if a in self.types
            for f in self._functions.values()
            if f.container == a
        }
        return tuple(
            f.symbol
            for f in self._functions.values()
            if f.container == t.name
            and "java.lang.Override" in self._annotations.get(f.symbol, [])
            and f.name not in ours
        )

    def entry_function(self, symbol: str) -> str:
        """Where a flow really starts: an endpoint declared on an interface (an OpenAPI-generated
        `UsersApi`) runs its one implementation."""
        impls = self._implementations.get(symbol, [])
        return impls[0] if len(impls) == 1 else symbol

    def parameter_of(self, expr: Any, scope: Any) -> str | None:
        fn = scope.function if isinstance(scope, JavaScope) else None
        if isinstance(expr, JavaExpr) and fn is not None and expr.text.strip() in fn.param_names:
            return expr.text.strip()
        return None

    # ---------------------------------------------------------------- code

    def functions(self) -> Iterator[JavaFunction]:
        yield from self._functions.values()

    def function(self, symbol: str) -> JavaFunction | None:
        return self._functions.get(symbol)

    def invocations(self, fn: JavaFunction, self_type: str | None = None) -> list[Invocation]:
        """Calls from `fn` to first-party functions, in source order. A call on `this` from an
        inherited method, read as `self_type`, runs `self_type`'s version (D58)."""
        out = []
        for c in self._calls.get(fn.symbol, []):
            callee = c.get("callee")
            if callee is None or callee not in self._functions:
                continue
            candidates = tuple(c.get("candidates", ()))
            if self_type and c.get("this_call"):
                own = self._override(callee, self_type)
                if own is not None:
                    callee, candidates = own, ()
            receiver = c.get("receiver_type")
            container = self._functions[callee].container
            # Called on a subclass that inherits the method: which subclass matters (D58)
            inherited = (
                receiver
                if receiver in self.types
                and not c.get("this_call")
                and container in self.ancestors(receiver)
                else None
            )
            out.append(
                Invocation(
                    callee,
                    c["end_line"],
                    c["end_col"],
                    bool(c.get("conditional")),
                    bool(c.get("in_loop")),
                    inherited,
                    candidates,
                )
            )
        return sorted(out, key=lambda i: (i.line, i.column))

    def _override(self, callee: str, self_type: str) -> str | None:
        """The method `self_type` runs for `callee`: its own, or its nearest ancestor's."""
        signature = callee.split("#", 1)[1]
        stop = self._functions[callee].container
        for cls in [self_type, *self.ancestors(self_type)]:
            own = self._functions.get(f"{cls}#{signature}")
            if own is not None and not own.is_abstract:
                return own.symbol
            if cls == stop:
                break
        return None

    def unresolved_calls(self, fn: JavaFunction) -> list[UnresolvedCall]:
        return [
            UnresolvedCall(
                c["line"],
                0,
                c["unresolved"].get("text", ""),
                c.get("method", ""),
                c["unresolved"].get("reason", "unknown type"),
                c["unresolved"].get("library"),
            )
            for c in self._calls.get(fn.symbol, [])
            if "unresolved" in c
        ]

    def receiver_calls(self) -> Iterator[tuple[JavaFunction, str, str, int]]:
        for symbol, calls in self._calls.items():
            fn = self._functions.get(symbol)
            for c in calls if fn else []:
                if c.get("receiver_type") and c.get("method"):
                    yield fn, c["receiver_type"], c["method"], c["line"]  # type: ignore[misc]

    def functions_passed(self, fn: Any, name: str) -> list[tuple[Any, str, int]]:
        return []

    def library_calls(self) -> Iterator[LibraryCall]:
        for symbol, calls in self._calls.items():
            fn = self._functions.get(symbol)
            for c in calls if fn else []:
                library = c.get("library")
                if c.get("library_call") and library and not _jdk_without_io(library):
                    yield LibraryCall(
                        fn,  # type: ignore[arg-type]
                        c["library_call"],
                        library,
                        c.get("method", ""),
                        c["line"],
                        c["end_line"],
                    )

    def imported_libraries(self) -> dict[str, list[str]]:
        files: dict[str, list[str]] = {}
        for file, names in self._imports.items():
            for name in names:
                if name.startswith("static "):
                    name = name.removeprefix("static ").rsplit(".", 1)[0]
                name = name.removesuffix(".*")
                if self._first_party(name):
                    continue
                library = library_of(name)
                if library and not is_jdk(library):
                    files.setdefault(library, []).append(file)
        return files

    def _first_party(self, name: str) -> bool:
        return name in self.types or any(
            name == p or name.startswith(p + ".") for p in self._packages
        )

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def stats(self) -> dict[str, Any]:
        return {**self._stats, "language_level": self.language_level or "default (21)"}


def _group(name: str) -> str:
    """jakarta's and javax's Default group are the same group."""
    return DEFAULT_GROUP if name.rsplit(".", 1)[-1] == "Default" else name


# Where a build declares its Java version
_JAVA_VERSION = [
    re.compile(r"(?:source|target)Compatibility\s*=\s*['\"]?(?:JavaVersion\.VERSION_)?([\d._]+)"),
    re.compile(r"languageVersion(?:\s*=\s*|\.set\()\s*JavaLanguageVersion\.of\(\s*(\d+)"),
    re.compile(r"\brelease(?:\s*=\s*|\.set\()\s*(\d+)"),
    re.compile(r"<maven\.compiler\.(?:source|release|target)>\s*([\d.]+)\s*<"),
    re.compile(r"<java\.version>\s*([\d.]+)\s*<"),
    re.compile(r"<release>\s*(\d+)\s*</release>"),
]


def java_version(root: Path) -> str | None:
    """The Java version a repository's builds declare (Gradle `sourceCompatibility`, a
    toolchain's `languageVersion`, `release`; Maven `maven.compiler.source` / `release`,
    `java.version`). With several modules, the highest: it parses the older ones too. None
    when no build says, and the helper uses the newest it knows."""
    found: list[tuple[int, ...]] = []
    for f in source_files(root):
        if f.name not in ("build.gradle", "build.gradle.kts", "pom.xml"):
            continue
        text = f.read_text(errors="replace")
        for pattern in _JAVA_VERSION:
            for v in pattern.findall(text):
                parts = v.replace("_", ".").strip(".").split(".")
                if parts and all(p.isdigit() for p in parts):
                    nums = tuple(int(p) for p in parts)
                    found.append(nums[1:] if nums[0] == 1 and len(nums) > 1 else nums)
    if not found:
        return None
    best = max(found)
    return ".".join(str(n) for n in best) if best[0] > 4 else "1." + ".".join(map(str, best))


def library_of(fqn: str) -> str | None:
    """The library a class or package belongs to, by package: `org.springframework.web`,
    `com.fasterxml.jackson`, `reactor.core`, `lombok`. Mirrors the helper's `libraryOf`."""
    owner = fqn.split("#", 1)[0]
    pkg = []
    for part in owner.split("."):
        if not part or part[0].isupper():
            break
        pkg.append(part)
    if not pkg:
        return owner or None
    n = 3 if pkg[0] in _DOMAINS else 2
    return ".".join(pkg[:n])


def is_jdk(library: str) -> bool:
    """`java.util`, `javax.crypto`: the JDK. `javax.persistence` isn't (Java EE)."""
    return library == "java" or library.startswith("java.") or library in JDK_JAVAX


def _jdk_without_io(library: str) -> bool:
    return is_jdk(library) and library not in JDK_IO


def _literal(text: str) -> Any:
    """A Java literal's value: strings, text blocks, numbers, booleans, characters, and
    `{"a", "b"}` annotation arrays of them."""
    t = text.strip()
    if t.startswith('"""') and t.endswith('"""') and len(t) >= 6:
        body = t[3:-3]
        return body.split("\n", 1)[1] if "\n" in body else body
    if len(t) >= 2 and t[0] == t[-1] == '"':
        try:
            return json.loads(t)
        except ValueError:
            return t[1:-1]
    if len(t) == 3 and t[0] == t[-1] == "'":
        return t[1]
    if t in ("true", "false"):
        return t == "true"
    if t.startswith("{") and t.endswith("}"):
        items = [_literal(p) for p in _split_top(t[1:-1])]
        return UNRESOLVED if UNRESOLVED in items else items
    number = t.rstrip("lLfFdD").replace("_", "")
    try:
        return int(number, 0)
    except ValueError:
        try:
            return float(number)
        except ValueError:
            return UNRESOLVED


def _split_top(text: str) -> list[str]:
    """Split `"a", "b, c"` on commas outside quotes."""
    parts, buf, quoted = [], "", False
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            quoted = not quoted
        if ch == "," and not quoted:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return [p for p in (p.strip() for p in parts) if p]
