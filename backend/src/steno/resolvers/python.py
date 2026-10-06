"""Steno's own Python symbol resolver (D51, docs/ingestion.md §5.1b).

It answers three questions about names in a repository's Python code:
- What does this name refer to?   `symbol_of`  (job_router → app.api.routers.job.router)
- What type is this value?         `type_of`    (client → httpx.AsyncClient)
- What constant does it hold?      `value_of`   (settings.OLLAMA_LOCAL_HOST_URL → "http://…")

It's deliberately small: imports and aliases, type annotations, simple assignments,
`with … as`, `self.x` attributes, and base classes. Anything it can't follow returns
None / UNRESOLVED instead of a guess.
"""

import ast
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from steno.source.files import is_test_file, source_files

log = logging.getLogger(__name__)


# Generic types whose first argument is what `with … as x` binds
_YIELDING = {
    "Generator",
    "AsyncGenerator",
    "Iterator",
    "AsyncIterator",
    "Iterable",
    "ContextManager",
    "AsyncContextManager",
    "AbstractContextManager",
    "AbstractAsyncContextManager",
}


class _Unresolved:
    def __repr__(self) -> str:
        return "UNRESOLVED"


UNRESOLVED: Any = _Unresolved()
BUILTINS = {"dict", "list", "set", "tuple", "str", "int", "float", "bool", "bytes", "object"}
FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class ClassInfo:
    qualname: str
    module: "ModuleInfo"
    node: ast.ClassDef
    methods: dict[str, "FunctionInfo"] = field(default_factory=dict)
    # Class-level `x: T = v` / `x = v`, and `self.x …` assignments inside methods
    annotations: dict[str, ast.expr] = field(default_factory=dict)
    values: dict[str, ast.expr] = field(default_factory=dict)
    self_attrs: dict[str, tuple[ast.expr, "FunctionInfo", bool]] = field(default_factory=dict)


@dataclass
class FunctionInfo:
    qualname: str
    module: "ModuleInfo"
    node: FuncNode
    cls: ClassInfo | None = None

    @property
    def params(self) -> dict[str, ast.expr | None]:
        a = self.node.args
        return {p.arg: p.annotation for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}

    @property
    def span(self) -> tuple[int, int]:
        start = min([self.node.lineno, *(d.lineno for d in self.node.decorator_list)])
        return start, self.node.end_lineno or self.node.lineno


@dataclass
class ModuleInfo:
    name: str
    path: Path
    tree: ast.Module
    is_package: bool
    imports: dict[str, str] = field(default_factory=dict)  # local name → qualified symbol
    module_imports: set[str] = field(default_factory=set)  # local names bound by `import x`
    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    values: dict[str, ast.expr] = field(default_factory=dict)
    annotations: dict[str, ast.expr] = field(default_factory=dict)


@dataclass(frozen=True)
class Scope:
    module: ModuleInfo
    function: FunctionInfo | None = None
    # The class `self` really is when an inherited method runs for a subclass:
    # Task.run reached through DiffTaskRunner(...).run() has self_type DiffTaskRunner
    self_type: str | None = None


class PythonResolver:
    def __init__(self, root: Path, files: list[Path] | None = None):
        self.root = root
        self.modules: dict[str, ModuleInfo] = {}
        self.by_path: dict[Path, ModuleInfo] = {}
        self.errors: list[tuple[Path, str]] = []
        # Return types of library methods, taught by `client` clues: (type, method) → type
        self.return_hints: dict[tuple[str, str], str] = {}
        # Configuration values (property clues): key → value
        self.properties: dict[str, Any] = {}
        self._busy: set[Any] = set()
        self._subclasses: dict[str, list[ClassInfo]] | None = None
        self.source_roots = find_source_roots(root)
        for path in files if files is not None else source_files(root):
            if path.suffix == ".py" and not is_test_file(path.relative_to(root)):
                self._index(path)

    # ---------------------------------------------------------------- indexing

    def _index(self, path: Path) -> None:
        try:
            tree = ast.parse(path.read_text(errors="replace"), filename=str(path))
        except SyntaxError as exc:
            self.errors.append((path, f"syntax error: {exc.msg} (line {exc.lineno})"))
            return
        source_root = max(
            (r for r in self.source_roots if path.is_relative_to(r)), key=lambda r: len(r.parts)
        )
        parts = list(path.relative_to(source_root).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        mod = ModuleInfo(".".join(parts), path, tree, is_package)
        for stmt in tree.body:
            self._index_statement(mod, stmt)
        self.modules[mod.name] = mod
        self.by_path[path.resolve()] = mod

    def _index_statement(self, mod: ModuleInfo, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.asname:
                    mod.imports[alias.asname] = alias.name
                    mod.module_imports.add(alias.asname)
                else:
                    top = alias.name.split(".")[0]
                    mod.imports[top] = top
                    mod.module_imports.add(top)
        elif isinstance(stmt, ast.ImportFrom):
            base = self._import_base(mod, stmt)
            for alias in stmt.names:
                if alias.name != "*":
                    mod.imports[alias.asname or alias.name] = (
                        f"{base}.{alias.name}" if base else alias.name
                    )
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.functions[stmt.name] = FunctionInfo(f"{mod.name}.{stmt.name}", mod, stmt)
        elif isinstance(stmt, ast.ClassDef):
            mod.classes[stmt.name] = self._index_class(mod, stmt)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    mod.values[target.id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            mod.annotations[stmt.target.id] = stmt.annotation
            if stmt.value is not None:
                mod.values[stmt.target.id] = stmt.value
        elif isinstance(stmt, (ast.If, ast.Try)):  # e.g. `if TYPE_CHECKING:` / try-import blocks
            for inner in [
                *stmt.body,
                *getattr(stmt, "orelse", []),
                *getattr(stmt, "finalbody", []),
            ]:
                self._index_statement(mod, inner)

    def _import_base(self, mod: ModuleInfo, stmt: ast.ImportFrom) -> str:
        if not stmt.level:
            return stmt.module or ""
        package = mod.name.split(".") if mod.is_package else mod.name.split(".")[:-1]
        package = package[: len(package) - (stmt.level - 1)] if stmt.level > 1 else package
        return ".".join([*package, *([stmt.module] if stmt.module else [])])

    def _index_class(self, mod: ModuleInfo, node: ast.ClassDef) -> ClassInfo:
        info = ClassInfo(f"{mod.name}.{node.name}", mod, node)
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info.methods[stmt.name] = FunctionInfo(
                    f"{info.qualname}.{stmt.name}", mod, stmt, info
                )
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                info.annotations[stmt.target.id] = stmt.annotation
                if stmt.value is not None:
                    info.values[stmt.target.id] = stmt.value
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        info.values[target.id] = stmt.value
        for method in info.methods.values():  # self.x = …  /  self.x: T = …
            for sub in _walk_body(method.node):
                if isinstance(sub, ast.AnnAssign) and _is_self_attr(sub.target):
                    info.self_attrs.setdefault(sub.target.attr, (sub.annotation, method, True))
                elif isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if _is_self_attr(target):
                            info.self_attrs.setdefault(target.attr, (sub.value, method, False))
        return info

    # ------------------------------------------------------------------ lookup

    def module_at(self, path: Path) -> ModuleInfo | None:
        return self.by_path.get(path.resolve())

    def enclosing(self, path: Path, line: int) -> tuple[FunctionInfo | None, ClassInfo | None]:
        """The innermost function and class containing a 1-based line."""
        mod = self.module_at(path)
        if mod is None:
            return None, None
        for cls in mod.classes.values():
            if cls.node.lineno <= line <= (cls.node.end_lineno or cls.node.lineno) or (
                cls.node.decorator_list
                and cls.node.decorator_list[0].lineno <= line < cls.node.lineno
            ):
                for method in cls.methods.values():
                    start, end = method.span
                    if start <= line <= end:
                        return method, cls
                return None, cls
        for fn in mod.functions.values():
            start, end = fn.span
            if start <= line <= end:
                return fn, None
        return None, None

    def lookup(
        self, symbol: str
    ) -> ClassInfo | FunctionInfo | tuple[ModuleInfo, str] | ModuleInfo | None:
        """A repository definition for a qualified symbol, following re-exports."""
        if symbol in self.modules:
            return self.modules[symbol]
        parts = symbol.split(".")
        for i in range(len(parts) - 1, 0, -1):
            mod = self.modules.get(".".join(parts[:i]))
            if mod is None:
                continue
            name, rest = parts[i], parts[i + 1 :]
            if name in mod.classes:
                cls = mod.classes[name]
                if not rest:
                    return cls
                return self._class_member(cls, rest[0]) if len(rest) == 1 else None
            if name in mod.functions and not rest:
                return mod.functions[name]
            if name in mod.values or name in mod.annotations:
                return (mod, name) if not rest else None
            if name in mod.imports and mod.imports[name] != symbol:  # re-export
                return self.lookup(".".join([mod.imports[name], *rest]))
            return None
        return None

    def canonical(self, symbol: str) -> str:
        """Follow re-exports so the same object always has the same name."""
        found = self.lookup(symbol)
        if isinstance(found, (ClassInfo, FunctionInfo)):
            return found.qualname
        if isinstance(found, tuple):
            return f"{found[0].name}.{found[1]}"
        if isinstance(found, ModuleInfo):
            return found.name
        return symbol

    def _class_member(self, cls: ClassInfo, name: str) -> FunctionInfo | None:
        for c in self._mro(cls):
            if name in c.methods:
                return c.methods[name]
        return None

    def _mro(self, cls: ClassInfo) -> Iterator[ClassInfo]:
        seen, todo = set(), [cls]
        while todo:
            c = todo.pop(0)
            if c.qualname in seen:
                continue
            seen.add(c.qualname)
            yield c
            for base in c.node.bases:
                found = self.lookup(self.symbol_of(base, Scope(c.module)) or "")
                if isinstance(found, ClassInfo):
                    todo.append(found)

    def base_symbols(self, qualname: str) -> list[str]:
        """Every base class of a repository class, as qualified symbols (library bases included)."""
        found = self.lookup(qualname)
        if not isinstance(found, ClassInfo):
            return []
        out = []
        for c in self._mro(found):
            for base in c.node.bases:
                sym = self.symbol_of(base, Scope(c.module))
                if sym:
                    out.append(self.canonical(sym))
        return out

    def is_subclass(self, qualname: str | None, targets: list[str]) -> bool:
        if not qualname:
            return False
        return qualname in targets or any(b in targets for b in self.base_symbols(qualname))

    # ----------------------------------------------------------------- symbols

    def symbol_of(self, expr: ast.expr, scope: Scope) -> str | None:
        """The qualified name an expression refers to."""
        if isinstance(expr, ast.Name):
            return self._resolve_name(expr.id, scope)
        if isinstance(expr, ast.Attribute):
            if self._is_module_ref(expr.value, scope):
                base = self.symbol_of(expr.value, scope)
                return self.canonical(f"{base}.{expr.attr}") if base else None
            cls = self.class_of(expr.value, scope)
            if cls:
                return self._member_symbol(cls, expr.attr)
            typ = self.type_of(expr.value, scope)
            if typ:
                return self._member_symbol(typ, expr.attr)
        return None

    def _member_symbol(self, owner: str, attr: str) -> str:
        found = self.lookup(owner)
        if isinstance(found, ClassInfo):
            method = self._class_member(found, attr)
            if method:
                return method.qualname
        return f"{owner}.{attr}"

    def _resolve_name(self, name: str, scope: Scope) -> str:
        fn, mod = scope.function, scope.module
        if fn is not None:
            local_imports = self._local_imports(fn)
            if name in local_imports:  # `from x import Y` inside the function body
                return self.canonical(local_imports[name])
            if name in fn.params or name in _local_names(fn.node):
                return f"{fn.qualname}.<locals>.{name}"
        if (
            name in mod.classes
            or name in mod.functions
            or name in mod.values
            or name in mod.annotations
        ):
            return f"{mod.name}.{name}"
        if name in mod.imports:
            return self.canonical(mod.imports[name])
        if name in BUILTINS:
            return f"builtins.{name}"
        return f"{mod.name}.{name}"

    def _local_imports(self, fn: FunctionInfo) -> dict[str, str]:
        imports: dict[str, str] = {}
        for node in _walk_body(fn.node):
            if isinstance(node, ast.ImportFrom):
                base = self._import_base(fn.module, node)
                for alias in node.names:
                    imports[alias.asname or alias.name] = (
                        f"{base}.{alias.name}" if base else alias.name
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )
        return imports

    def _is_module_ref(self, expr: ast.expr, scope: Scope) -> bool:
        if isinstance(expr, ast.Name):
            if expr.id in scope.module.module_imports:
                return True
            sym = scope.module.imports.get(expr.id)
            return sym is not None and sym in self.modules
        if isinstance(expr, ast.Attribute):
            return self._is_module_ref(expr.value, scope)
        return False

    def class_of(self, expr: ast.expr, scope: Scope) -> str | None:
        """If the expression names a class, its qualified name (library classes included)."""
        if not isinstance(expr, (ast.Name, ast.Attribute)):
            return None
        sym = self.symbol_of(expr, scope)
        if not sym or ".<locals>." in sym:
            return None
        found = self.lookup(sym)
        if isinstance(found, ClassInfo):
            return found.qualname
        if found is None and sym.rsplit(".", 1)[-1][:1].isupper():  # a library class, by convention
            return sym
        return None

    def function_of(self, expr: ast.expr, scope: Scope) -> FunctionInfo | None:
        sym = self.symbol_of(expr, scope)
        found = self.lookup(sym) if sym else None
        return found if isinstance(found, FunctionInfo) else None

    def scope_of(self, fn: FunctionInfo, self_type: str | None = None) -> Scope:
        return Scope(fn.module, fn, self_type)

    # ------------------------------------------------------------------- types

    def type_of(self, expr: ast.expr, scope: Scope) -> str | None:
        """The qualified type of the value an expression produces, when it can be known."""
        key = ("type", id(expr), id(scope.function), scope.self_type)
        if key in self._busy:
            return None
        self._busy.add(key)
        try:
            return self._type_of(expr, scope)
        finally:
            self._busy.discard(key)

    def _type_of(self, expr: ast.expr, scope: Scope) -> str | None:
        if isinstance(expr, ast.Await):
            return self.type_of(expr.value, scope)
        if isinstance(expr, ast.Constant):
            return f"builtins.{type(expr.value).__name__}" if expr.value is not None else None
        if isinstance(expr, ast.JoinedStr):
            return "builtins.str"
        if isinstance(expr, (ast.Dict, ast.DictComp)):
            return "builtins.dict"
        if isinstance(expr, (ast.List, ast.ListComp)):
            return "builtins.list"
        if isinstance(expr, ast.Name):
            return self._name_type(expr.id, scope)
        if isinstance(expr, ast.Attribute):
            if self._is_module_ref(expr.value, scope):
                return None
            owner = self.type_of(expr.value, scope)
            found = self.lookup(owner) if owner else None
            if isinstance(found, ClassInfo):
                return self._attr_type(found, expr.attr)
            return None
        if isinstance(expr, ast.Call):
            cls = self.class_of(expr.func, scope)
            if cls:
                return cls
            fn = self.function_of(expr.func, scope)
            if fn is not None and fn.node.returns is not None:
                return self.annotation_type(fn.node.returns, self.scope_of(fn))
            if isinstance(expr.func, ast.Attribute):  # a library method we've been taught about
                owner = self.type_of(expr.func.value, scope)
                if owner:
                    return self._hinted_return(owner, expr.func.attr)
        return None

    def _hinted_return(self, owner: str, method: str) -> str | None:
        for (typ, name), ret in self.return_hints.items():
            if name == method and self.is_subclass(owner, [typ]):
                return ret
        return None

    def _name_type(self, name: str, scope: Scope) -> str | None:
        fn = scope.function
        if fn is not None:
            if (
                fn.cls is not None
                and name in ("self", "cls")
                and next(iter(fn.params), None) == name
            ):
                return scope.self_type or fn.cls.qualname
            if name in fn.params or name in _local_names(fn.node):
                return self._local_type(fn, name, scope)
        mod = scope.module
        if name in mod.annotations:
            return self.annotation_type(mod.annotations[name], Scope(mod))
        if name in mod.values:
            return self.type_of(mod.values[name], Scope(mod))
        if (
            name in mod.imports
        ):  # an imported module-level value, e.g. `from core.config import settings`
            found = self.lookup(mod.imports[name])
            if isinstance(found, tuple):
                return self._name_type(found[1], Scope(found[0]))
        return None

    def _local_type(self, fn: FunctionInfo, name: str, scope: Scope) -> str | None:
        annotation = fn.params.get(name)
        if annotation is not None:
            return self.annotation_type(annotation, Scope(fn.module))
        for node in _walk_body(fn.node):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
            ):
                return self.annotation_type(node.annotation, Scope(fn.module))
        for node in _walk_body(fn.node):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                typ = self.type_of(node.value, scope)
                if typ:
                    return typ
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == name:
                        return self._with_type(item.context_expr, scope)
        return None

    def _with_type(self, expr: ast.expr, scope: Scope) -> str | None:
        """The type bound by `with expr as x`. A @contextmanager function annotated
        `-> AsyncGenerator[AsyncSession, None]` binds an AsyncSession, not the generator."""
        if isinstance(expr, ast.Call):
            fn = self.function_of(expr.func, scope)
            ret = fn.node.returns if fn is not None else None
            if isinstance(ret, ast.Subscript):
                base = self.symbol_of(ret.value, Scope(fn.module)) or ""  # type: ignore[union-attr]
                if base.rsplit(".", 1)[-1] in _YIELDING:
                    first = ret.slice.elts[0] if isinstance(ret.slice, ast.Tuple) else ret.slice
                    return self.annotation_type(first, Scope(fn.module))  # type: ignore[union-attr]
        return self.type_of(expr, scope)

    def _attr_type(self, cls: ClassInfo, attr: str) -> str | None:
        for c in self._mro(cls):
            if attr in c.annotations:
                return self.annotation_type(c.annotations[attr], Scope(c.module))
            if attr in c.self_attrs:
                expr, method, is_annotation = c.self_attrs[attr]
                if is_annotation:
                    return self.annotation_type(expr, Scope(c.module))
                return self.type_of(expr, self.scope_of(method))
            if attr in c.values:
                return self.type_of(c.values[attr], Scope(c.module))
        return None

    def annotation_type(self, ann: ast.expr, scope: Scope) -> str | None:
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):  # "Job" forward references
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):  # X | None
            for side in (ann.left, ann.right):
                if not (isinstance(side, ast.Constant) and side.value is None):
                    return self.annotation_type(side, scope)
        if isinstance(ann, ast.Subscript):
            base = self.symbol_of(ann.value, scope) or ""
            if base.endswith(("Optional", "Annotated", "Mapped", "ClassVar")):
                inner = ann.slice.elts[0] if isinstance(ann.slice, ast.Tuple) else ann.slice
                return self.annotation_type(inner, scope)
            return self.canonical(base) if base else None
        if isinstance(ann, (ast.Name, ast.Attribute)):
            sym = self.symbol_of(ann, scope)
            return self.canonical(sym) if sym else None
        return None

    # ------------------------------------------------------------------ values

    def value_of(self, expr: ast.expr, scope: Scope) -> Any:
        """The constant an expression evaluates to, or UNRESOLVED."""
        key = ("value", id(expr), id(scope.function), scope.self_type)
        if key in self._busy:
            return UNRESOLVED
        self._busy.add(key)
        try:
            return self._value_of(expr, scope)
        finally:
            self._busy.discard(key)

    def _value_of(self, expr: ast.expr, scope: Scope) -> Any:
        if isinstance(expr, ast.Constant):
            return expr.value
        if isinstance(expr, ast.JoinedStr):
            return self._join([self._fpart(p, scope) for p in expr.values])
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            left, right = self.value_of(expr.left, scope), self.value_of(expr.right, scope)
            if isinstance(left, str) or isinstance(right, str):
                return self._join(
                    [
                        self._text_part(left, expr.left, scope),
                        self._text_part(right, expr.right, scope),
                    ]
                )
            if left is UNRESOLVED or right is UNRESOLVED:
                return UNRESOLVED
            try:
                return left + right
            except TypeError:
                return UNRESOLVED
        if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.USub):
            v = self.value_of(expr.operand, scope)
            return -v if isinstance(v, (int, float)) else UNRESOLVED
        if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
            items = [self.value_of(e, scope) for e in expr.elts]
            return UNRESOLVED if UNRESOLVED in items else items
        if isinstance(expr, ast.Dict):
            if None in expr.keys:
                return UNRESOLVED
            keys = [self.value_of(k, scope) for k in expr.keys]  # type: ignore[arg-type]
            vals = [self.value_of(v, scope) for v in expr.values]
            return (
                UNRESOLVED
                if UNRESOLVED in keys or UNRESOLVED in vals
                else dict(zip(keys, vals, strict=True))
            )
        if isinstance(expr, ast.Name):
            return self._name_value(expr.id, scope)
        if isinstance(expr, ast.Attribute):
            if self._is_module_ref(expr.value, scope):
                found = self.lookup(self.symbol_of(expr, scope) or "")
                if isinstance(found, tuple):
                    return self._name_value(found[1], Scope(found[0]))
                return UNRESOLVED
            owner = self.type_of(expr.value, scope) or self.class_of(expr.value, scope)
            found = self.lookup(owner) if owner else None
            if isinstance(found, ClassInfo):
                for c in self._mro(found):
                    if expr.attr in c.values:
                        return self.value_of(c.values[expr.attr], Scope(c.module))
            return UNRESOLVED
        if isinstance(expr, ast.Call):
            return self._call_value(expr, scope)
        return UNRESOLVED

    def _call_value(self, call: ast.Call, scope: Scope) -> Any:
        target = self.symbol_of(call.func, scope)
        if target in ("os.getenv", "os.environ.get"):
            key = self.value_of(call.args[0], scope) if call.args else UNRESOLVED
            if key in self.properties:
                return self.properties[key]
            default = (
                call.args[1]
                if len(call.args) > 1
                else next((k.value for k in call.keywords if k.arg == "default"), None)
            )
            return self.value_of(default, scope) if default is not None else UNRESOLVED
        if isinstance(call.func, ast.Attribute) and call.func.attr in ("rstrip", "lstrip", "strip"):
            base = self.value_of(call.func.value, scope)
            chars = self.value_of(call.args[0], scope) if call.args else None
            if isinstance(base, str) and (chars is None or isinstance(chars, str)):
                return getattr(base, call.func.attr)(chars)
        return UNRESOLVED

    def _name_value(self, name: str, scope: Scope) -> Any:
        fn = scope.function
        if fn is not None:
            if name in fn.params:
                return UNRESOLVED  # depends on the caller
            assigned = [
                n.value
                for n in _walk_body(fn.node)
                if isinstance(n, (ast.Assign, ast.AnnAssign))
                and n.value is not None
                and any(isinstance(t, ast.Name) and t.id == name for t in _targets(n))
            ]
            if assigned:
                return self.value_of(assigned[0], scope) if len(assigned) == 1 else UNRESOLVED
        mod = scope.module
        if name in mod.values:
            return self.value_of(mod.values[name], Scope(mod))
        if name in mod.imports:
            found = self.lookup(mod.imports[name])
            if isinstance(found, tuple):
                return self._name_value(found[1], Scope(found[0]))
        return UNRESOLVED

    def _fpart(self, part: ast.expr, scope: Scope) -> Any:
        if isinstance(part, ast.Constant):
            return part.value
        if isinstance(part, ast.FormattedValue):
            return self._text_part(self.value_of(part.value, scope), part.value, scope)
        return UNRESOLVED

    def _text_part(self, value: Any, expr: ast.expr, scope: Scope) -> Any:
        if value is UNRESOLVED:
            # A function parameter becomes a {placeholder}, like a route parameter
            if isinstance(expr, ast.Name) and scope.function and expr.id in scope.function.params:
                return _Placeholder(expr.id)
            return UNRESOLVED
        return str(value)

    @staticmethod
    def _join(parts: list[Any]) -> Any:
        """Join string parts. Anything unknown makes the whole value unresolved, except a
        parameter placeholder after the start (`…/pull-requests/{pr_id}`)."""
        if not parts or parts[0] is UNRESOLVED or isinstance(parts[0], _Placeholder):
            return UNRESOLVED if parts else ""
        if any(p is UNRESOLVED for p in parts):
            return UNRESOLVED
        return "".join(f"{{{p.name}}}" if isinstance(p, _Placeholder) else p for p in parts)

    # -------------------------------------------------------------- call sites

    def functions(self) -> Iterator[FunctionInfo]:
        for mod in self.modules.values():
            yield from mod.functions.values()
            for cls in mod.classes.values():
                yield from cls.methods.values()

    def invocations(self, fn: FunctionInfo, self_type: str | None = None) -> list["Invocation"]:
        """Calls from `fn` to first-party functions, in source order (the call graph's edges).

        A constructor call (`Job()`) counts as a call to the class's `__init__` when the
        repository defines one. Calls into libraries aren't listed.

        `self_type` reads an inherited method as the subclass it runs for, so `self.execute()`
        inside Task.run finds DiffTaskRunner.execute. Each call records the class it was made
        on (`receiver`) when the method found is inherited from a base class, and a call that
        lands on an abstract method lists the subclasses' implementations (`candidates`).
        """
        found: list[Invocation] = []
        scope = self.scope_of(fn, self_type)

        def visit(node: ast.AST, conditional: bool, in_loop: bool) -> None:
            # Nested functions and lambdas (closures, `async def _run(...)` handed to
            # asyncio.gather) run as part of this function's work, so their calls count here.
            # Nested classes don't.
            if isinstance(node, ast.ClassDef):
                return
            if isinstance(node, ast.Call):
                callee, receiver = self._call_target(node.func, scope)
                if callee is not None:
                    # Ordered by where each call ends: in `Runner(x).run()`,
                    # the constructor runs first
                    end = (node.end_lineno or node.lineno, node.end_col_offset or node.col_offset)
                    inherited = receiver if callee.cls and receiver != callee.cls.qualname else None
                    candidates = (
                        tuple(o.qualname for o in self.implementations(callee, receiver))
                        if self.is_abstract(callee)
                        else ()
                    )
                    found.append(
                        Invocation(
                            callee.qualname,
                            end[0],
                            end[1],
                            conditional,
                            in_loop,
                            inherited,
                            candidates,
                        )
                    )
            branchy = isinstance(node, (ast.If, ast.IfExp, ast.Try, ast.Match, ast.BoolOp))
            loopy = isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.comprehension))
            for child in ast.iter_child_nodes(node):
                visit(child, conditional or branchy, in_loop or loopy)

        for stmt in fn.node.body:
            visit(stmt, False, False)
        return sorted(found, key=lambda i: (i.line, i.column))

    def _call_target(self, func: ast.expr, scope: Scope) -> tuple[FunctionInfo | None, str | None]:
        """The function a call runs, and the repository class it was called on (if any)."""
        if isinstance(func, ast.Attribute) and not self._is_module_ref(func.value, scope):
            owner = self.class_of(func.value, scope) or self.type_of(func.value, scope)
            found = self.lookup(owner) if owner else None
            if isinstance(found, ClassInfo):
                method = self._class_member(found, func.attr)
                if method is not None:
                    return method, found.qualname
        fn = self.function_of(func, scope)
        if fn is not None:
            return fn, None
        cls = self.class_of(func, scope)
        target = self.lookup(cls) if cls else None
        if isinstance(target, ClassInfo):  # a constructor call: its __init__, maybe inherited
            init = self._class_member(target, "__init__")
            return (init, target.qualname) if init else (None, None)
        return None, None

    # ------------------------------------------------------- abstract methods

    def is_abstract(self, fn: FunctionInfo) -> bool:
        """An @abstractmethod, or a method with no body of its own (docstring, `pass`, `...`,
        `raise NotImplementedError`): calling it really runs a subclass's version."""
        if fn.cls is None:
            return False
        scope = Scope(fn.module)
        for d in fn.node.decorator_list:
            if (self.symbol_of(d, scope) or "").endswith("abstractmethod"):
                return True
        body = list(fn.node.body)
        first = body[0] if body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                body = body[1:]  # the docstring
        return all(_is_placeholder(stmt) for stmt in body)

    def implementations(self, fn: FunctionInfo, within: str | None = None) -> list[FunctionInfo]:
        """Subclass methods that implement an abstract `fn`. With `within`, only classes
        that are, or inherit from, that class (the call's receiver)."""
        if fn.cls is None:
            return []
        out = []
        for cls in self.subclasses(fn.cls.qualname):
            if (
                within
                and within != fn.cls.qualname
                and not self.is_subclass(cls.qualname, [within])
            ):
                continue
            method = cls.methods.get(fn.node.name)
            if method is not None and not self.is_abstract(method):
                out.append(method)
        return sorted(out, key=lambda m: m.qualname)

    def subclasses(self, qualname: str) -> list[ClassInfo]:
        """Every repository class that inherits from `qualname`, directly or not."""
        if self._subclasses is None:
            index: dict[str, list[ClassInfo]] = {}
            for mod in self.modules.values():
                for cls in mod.classes.values():
                    for base in self.base_symbols(cls.qualname):
                        index.setdefault(base, []).append(cls)
            self._subclasses = index
        return self._subclasses.get(qualname, [])

    def method_calls(self) -> Iterator[tuple[FunctionInfo, ast.Call]]:
        """Every `receiver.method(...)` call, with the function it's in."""
        for fn in self.functions():
            for node in _walk_body(fn.node):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    yield fn, node


PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def find_source_roots(repo: Path) -> list[Path]:
    """Directories that module names are relative to: each Python project in the repository
    (a folder with pyproject.toml, setup.py, setup.cfg, or requirements*.txt), or its `src/`.
    In a monorepo, `apps/backend/app/models.py` is the module `app.models`."""
    roots = {repo}
    for f in source_files(repo):
        if f.name in PROJECT_MARKERS or (f.name.startswith("requirements") and f.suffix == ".txt"):
            project = f.parent
            roots.add(project / "src" if (project / "src").is_dir() else project)
    return sorted(roots)


@dataclass(frozen=True)
class Invocation:
    callee: str  # qualified name of the called function
    line: int
    column: int
    conditional: bool  # inside an if / try / match / boolean shortcut
    in_loop: bool
    # The subclass the call was made on, when `callee` is inherited from a base class
    receiver: str | None = None
    # When `callee` is abstract: the subclass methods that can really run
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Placeholder:
    name: str


def _is_placeholder(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return True  # `...` or a stray string
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _is_self_attr(target: ast.expr) -> bool:
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


def _targets(node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
    return node.targets if isinstance(node, ast.Assign) else [node.target]


def _walk_body(fn: FuncNode) -> Iterator[ast.AST]:
    """Nodes in a function body, not descending into nested functions or classes."""
    todo: list[ast.AST] = list(fn.body)
    while todo:
        node = todo.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                todo.append(child)


def _local_names(fn: FuncNode) -> set[str]:
    names = set()
    for node in _walk_body(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names |= {t.id for t in _targets(node) if isinstance(t, ast.Name)}
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            names |= {
                i.optional_vars.id for i in node.items if isinstance(i.optional_vars, ast.Name)
            }
        elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names
