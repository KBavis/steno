"""What every symbol resolver answers, whatever the language (D70, docs/ingestion.md §5.1c).

The engine, the assemblers, the graph builder, and the coverage report ask these questions and
never look inside a language's syntax. Each resolver has its own expressions (Python: `ast`
nodes; Java: positions the JVM helper resolved) and scopes; callers only pass them back.

`Resolvers` holds one resolver per language in a repository, picks one by file, and answers the
call-graph questions across all of them. The config property map belongs to it and is shared by
every resolver, so a Spring `${key}` and a Python `os.getenv("KEY")` read the same config.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ast_grep_py import SgNode


class _Unresolved:
    def __repr__(self) -> str:
        return "UNRESOLVED"


UNRESOLVED: Any = _Unresolved()

# Method names that usually mean I/O: with an unknown receiver, a call named like this may be an
# effect no rule saw. The stand-in for Jev I8 (coverage report, unresolved calls in flows).
IO_VERBS = {
    "add", "add_all", "call", "chat", "commit", "complete", "consume", "create", "delete",
    "download", "embed", "execute", "fetch", "fetchall", "generate", "get", "insert", "invoke",
    "merge", "patch", "post", "produce", "publish", "put", "query", "request", "search", "send",
    "stream", "subscribe", "update", "upload", "upsert",
    # Java names for the same things
    "exchange", "getforobject", "postforobject", "postforentity", "getforentity", "save",
    "saveall", "saveandflush", "deletebyid", "findall", "findbyid", "retrieve", "newcall",
    "sendandreceive", "convertandsend",
}  # fmt: skip


def span(node: SgNode) -> tuple[int, int, int, int]:
    """Where a matched node is: start line and column, end line and column (0-based)."""
    r = node.range()
    return r.start.line, r.start.column, r.end.line, r.end.column


def looks_like_io(method: str) -> bool:
    """`acomplete`, `send_async`, `postForObject`: named like I/O."""
    name = method.lower()
    return any(
        n in IO_VERBS
        for n in (name, name[1:] if name.startswith("a") else "", name.removesuffix("_async"))
    )


@dataclass(frozen=True)
class Invocation:
    callee: str  # the called function's symbol
    line: int
    column: int
    conditional: bool  # inside an if / try / match / boolean shortcut
    in_loop: bool
    # The subclass the call was made on, when `callee` is inherited from a base class
    receiver: str | None = None
    # When `callee` is abstract: the implementations that can really run
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnresolvedCall:
    """A call a resolver couldn't follow (D71): kept so a flow that stops here says so."""

    line: int
    column: int
    text: str  # the call as written, shortened: `webClient.post().uri(…)`
    method: str  # the method's name, for deciding whether the gap matters
    reason: str  # `library type`, `unknown type`, `unknown method`, `function value`
    library: str | None = None  # the library whose type couldn't be seen

    def describe(self) -> dict[str, Any]:
        return {
            "call": self.text,
            "line": self.line,
            "reason": self.reason,
            **({"library": self.library} if self.library else {}),
        }


@dataclass(frozen=True)
class LibraryCall:
    """A call from first-party code into a library: what the coverage report checks for I/O."""

    function: "Function"
    symbol: str  # `httpx.AsyncClient.post`, `org.springframework.kafka.core.KafkaTemplate#send`
    library: str  # `httpx`, `org.springframework.kafka`
    method: str
    line: int
    end_line: int


class Function(Protocol):
    """A first-party function or method, in any language."""

    @property
    def symbol(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def container(self) -> str: ...  # its class, or its module
    @property
    def path(self) -> Path: ...
    @property
    def span(self) -> tuple[int, int]: ...
    @property
    def params(self) -> dict[str, Any]: ...
    @property
    def is_async(self) -> bool: ...
    @property
    def doc(self) -> str | None: ...
    @property
    def body_hash(self) -> str: ...
    @property
    def language(self) -> str: ...


class Resolver(Protocol):
    language: str
    extensions: tuple[str, ...]
    root: Path
    errors: list[tuple[Path, str]]
    properties: dict[str, Any]
    return_hints: dict[tuple[str, str], str]

    # Reading a match
    def expression(self, path: Path, node: SgNode) -> Any | None: ...
    def scope_at(self, path: Path, line: int) -> tuple[Any, Function | None, str | None]: ...
    def names_code(self, node: SgNode) -> bool: ...

    # What is this?
    def symbol_of(self, expr: Any, scope: Any) -> str | None: ...
    def canonical(self, symbol: str) -> str: ...
    def class_of(self, expr: Any, scope: Any) -> str | None: ...
    def type_of(self, expr: Any, scope: Any) -> str | None: ...
    def value_of(self, expr: Any, scope: Any) -> Any: ...
    def template_of(self, expr: Any, scope: Any) -> str | None: ...
    def is_subclass(self, qualname: str | None, targets: list[str]) -> bool: ...
    def entity_of(self, expr: Any, scope: Any) -> str | None: ...
    def parameter_of(self, expr: Any, scope: Any) -> str | None: ...

    # The code
    def functions(self) -> Iterator[Function]: ...
    def function(self, symbol: str) -> Function | None: ...
    def invocations(self, fn: Any, self_type: str | None = None) -> list[Invocation]: ...
    def unresolved_calls(self, fn: Any) -> list[UnresolvedCall]: ...
    def receiver_calls(self) -> Iterator[tuple[Function, str, str, int]]: ...
    def functions_passed(self, fn: Any, name: str) -> list[tuple[Any, str, int]]: ...
    def library_calls(self) -> Iterator[LibraryCall]: ...
    def imported_libraries(self) -> dict[str, list[str]]: ...
    def stats(self) -> dict[str, Any]: ...


class Resolvers:
    """The resolvers for a repository's languages, and one config property map they share."""

    def __init__(self, root: Path, resolvers: list[Resolver]):
        self.root = root
        self.properties: dict[str, Any] = {}
        self.by_language = {r.language: r for r in resolvers}
        for r in resolvers:
            r.properties = self.properties
        self._functions: dict[str, Function] | None = None

    def __bool__(self) -> bool:
        return bool(self.by_language)

    def __iter__(self) -> Iterator[Resolver]:
        return iter(self.by_language.values())

    def for_path(self, path: Path) -> Resolver | None:
        return next((r for r in self if path.suffix in r.extensions), None)

    def for_function(self, fn: Function) -> Resolver:
        return self.by_language[fn.language]

    @property
    def errors(self) -> list[tuple[Path, str]]:
        return [e for r in self for e in r.errors]

    def functions(self) -> Iterator[Function]:
        for r in self:
            yield from r.functions()

    def function(self, symbol: str | None) -> Function | None:
        if not symbol:
            return None
        if self._functions is None:
            self._functions = {f.symbol: f for f in self.functions()}
        return self._functions.get(symbol)

    def invocations(self, fn: Function, self_type: str | None = None) -> list[Invocation]:
        return self.for_function(fn).invocations(fn, self_type)

    def unresolved_calls(self, fn: Function) -> list[UnresolvedCall]:
        return self.for_function(fn).unresolved_calls(fn)

    def entry_function(self, symbol: str) -> str:
        """The function a flow starts in: the implementation when the entry point is declared
        on an interface method, else the entry point's own function."""
        fn = self.function(symbol)
        if fn is None:
            return symbol
        pick = getattr(self.for_function(fn), "entry_function", None)
        return pick(symbol) if pick else symbol

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def stats(self) -> dict[str, Any]:
        return {r.language: r.stats() for r in self}
