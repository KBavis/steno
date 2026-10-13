"""The map-layer ledger (D101, option D): everything that can create an entry point, an
integration point, or a join is either explained, known to be harmless, or a visible gap.

Steno doesn't model what code does inside a function; it models the map: what starts work,
what work touches, and the route between. Two things can change the map without a rule
noticing, so both are accounted for:

- **Library calls that can do I/O.** Which libraries can is declared (core lists here, plus
  each pack's `library.io`), never guessed from a method's name. A call into one that no rule
  explains is a gap on its function's trace. Calls into anything else are code detail.
- **Framework hooks.** A class extending or implementing a framework type, or a framework
  annotation or decorator, means the framework runs that code. A rule matching the
  declaration explains it; a pack's `library.harmless` says it can't change the map; anything
  else is an unknown hook, listed on its application and in the coverage report, and the
  methods the framework calls start flows of their own (framework callbacks).
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from steno.extraction.facts import Extraction
from steno.resolvers.base import Function, Hook, LibraryCall, Resolvers, UnresolvedCall

# Libraries whose calls can reach the network, a store, or a broker
IO_LIBRARIES = {
    "aiohttp", "aiokafka", "anthropic", "asyncpg", "atlassian", "boto3", "botocore", "chromadb",
    "confluent_kafka", "elasticsearch", "ftplib", "github", "google.cloud", "grpc", "httpx",
    "jira", "kafka", "langchain", "llama_index.llms", "llama_index.embeddings",
    "llama_index.vector_stores", "llama_index.storage", "llama_index.core.storage", "motor",
    "neo4j", "ollama", "openai",
    "paramiko", "pika", "psycopg", "psycopg2", "pymongo", "redis", "requests", "slack_sdk",
    "smtplib", "socket", "sqlalchemy", "urllib.request", "urllib3", "websockets", "http.client",
    "sqlite3", "subprocess",
    # Java, by package
    "com.amazonaws", "com.datastax.oss", "com.rabbitmq", "feign", "io.grpc", "io.lettuce",
    "okhttp3", "org.apache.kafka", "org.apache.http", "org.apache.hc", "org.mongodb",
    "org.springframework.amqp", "org.springframework.data", "org.springframework.jdbc",
    "org.springframework.jms", "org.springframework.kafka", "org.springframework.web.client",
    "org.springframework.web.reactive.function.client", "redis.clients", "software.amazon",
    "java.net.http", "java.net.HttpURLConnection", "java.sql",
    # Files: model artifacts, uploads, exports are stores too
    "builtins.open", "pathlib.Path.open", "pathlib.Path.read_text", "pathlib.Path.write_text",
    "pathlib.Path.read_bytes", "pathlib.Path.write_bytes", "shutil", "pickle.load",
    "pickle.dump", "joblib.load", "joblib.dump", "onnxruntime.InferenceSession", "torch.load",
    "torch.save", "numpy.load", "numpy.save",
    "java.nio.file.Files", "java.io.FileInputStream", "java.io.FileOutputStream",
    "java.io.FileReader", "java.io.FileWriter", "java.io.RandomAccessFile",
}  # fmt: skip
# Never an entry point or an integration point on their own
HARMLESS = {
    "java.", "javax.annotation", "lombok", "com.fasterxml.jackson", "org.mapstruct",
    "org.slf4j", "org.apache.logging", "org.apache.commons.lang3",
    "typing", "dataclasses", "enum", "abc", "functools", "contextlib", "pydantic",
    "jakarta.validation.ConstraintValidator#initialize",
    "javax.validation.ConstraintValidator#initialize",
    "jakarta.transaction.Transactional", "javax.transaction.Transactional",
    "torch.nn", "pandas.core", "sklearn",
}  # fmt: skip


@dataclass
class Gap:
    function: str
    file: str
    line: int
    detail: str
    library: str | None = None


class Ledger:
    def __init__(self, x: Extraction, rs: Resolvers | None, packs: list[Any] | None = None):
        packs = packs or []
        self.rs = rs
        self.io = IO_LIBRARIES | {i for p in packs for i in p.io}
        self.harmless = HARMLESS | {h for p in packs for h in p.harmless}
        self.origins: dict[str, set[int]] = defaultdict(set)  # file → lines a rule matched
        self.pack_lines: dict[str, list[tuple[int, str]]] = defaultdict(list)  # (line, pack)
        # What each pack is about: a call into one of these is explained by any fact the
        # pack recorded in the same function (`newCall` after the OkHttp request it built)
        self.covers = {p.name: [*p.io, *(p.enabled_when or {}).get("imports", [])] for p in packs}
        for o in [
            *(n.origin for n in x.nodes),
            *(e.origin for e in x.edges),
            *(c.origin for c in x.clues),
            *(ep.origin for ep in x.entry_points),
            *(o for o, _ in x.dropped),
        ]:
            self.origins[o.file].add(o.line)
            self.pack_lines[o.file].append((o.line, o.pack))
        self.calls: list[tuple[LibraryCall, bool]] = []  # I/O-capable calls, explained?
        self.hooks: list[tuple[Hook, bool]] = []  # framework hooks, explained?
        self._marks: dict[str, list[str]] = defaultdict(list)
        if rs:
            for r in rs:
                for call in r.library_calls():
                    exact_io = any(call.symbol.split("#", 1)[0] == i for i in self.io)
                    if _exception(call.symbol) or (_constructor(call.symbol) and not exact_io):
                        continue  # reading an exception's message, building a client: not I/O
                    if self.is_io(call.symbol) and not self.is_harmless(call.symbol):
                        self.calls.append((call, self._explained(call)))
                for hook in getattr(r, "hook_candidates", lambda: [])():
                    for symbol in hook.entries:
                        if hook.target not in self._marks[symbol]:
                            self._marks[symbol].append(hook.target)
                    if not self.is_harmless(hook.target):
                        self.hooks.append((hook, self._declared(hook)))
        self._by_function: dict[str, list[LibraryCall]] = defaultdict(list)
        for call, explained in self.calls:
            if not explained:
                self._by_function[call.function.symbol].append(call)

    # ------------------------------------------------------------ classification

    def is_io(self, symbol: str | None) -> bool:
        return bool(symbol) and any(_within(symbol, lib) for lib in self.io)  # type: ignore[arg-type]

    def is_harmless(self, symbol: str | None) -> bool:
        return bool(symbol) and any(
            _within(symbol, h) or (h.endswith(".") and symbol.startswith(h))  # type: ignore[union-attr]
            for h in self.harmless
        )

    def _explained(self, call: LibraryCall) -> bool:
        """A rule matched on the call's lines (the call became a fact)."""
        assert self.rs
        rel = self.rs.rel(call.function.path)
        lines = self.origins.get(rel, set())
        if any(line in lines for line in range(call.line, call.end_line + 1)):
            return True
        start, end = call.function.span
        return any(
            start <= line <= end and any(_within(call.symbol, c) for c in self.covers.get(pack, []))
            for line, pack in self.pack_lines.get(rel, [])
        )

    def _declared(self, hook: Hook) -> bool:
        """A rule matched the declaration itself (a mapping, an entity, a listener)."""
        lines = self.origins.get(hook.file, set())
        return any(hook.header[0] <= line <= hook.header[1] for line in lines)

    # ------------------------------------------------------------------- queries

    def unexplained_calls(self, fn: Function) -> list[UnresolvedCall]:
        """I/O-capable library calls in `fn` that no rule explains: the flow may touch
        something Steno doesn't show."""
        return [
            UnresolvedCall(
                c.line,
                0,
                c.symbol.replace("#", ".").rsplit(".", 2)[-2] + "." + c.method + "(…)",
                c.method,
                "I/O call no rule explains",
                c.library,
            )
            for c in self._by_function.get(fn.symbol, [])
        ]

    def keeps(self, gap: UnresolvedCall) -> bool:
        """Whether a call the resolver couldn't follow can change the map. A chain through a
        library type matters only when that library can do I/O; anything else is code detail."""
        # A chain through a library type (`findById(id).orElseThrow()`) is accounted for at the
        # library call it starts from, which the ledger checks; the rest of the chain is detail
        return gap.reason != "library type"

    def unknown_hooks(self) -> list[Hook]:
        return [h for h, declared in self.hooks if not declared]

    def callback_entries(self) -> dict[str, Hook]:
        """Functions a framework calls through an unknown hook: they start flows of their own."""
        out: dict[str, Hook] = {}
        for hook in self.unknown_hooks():
            for symbol in hook.entries:
                method = symbol.split("#", 1)[-1].split("(", 1)[0].rsplit(".", 1)[-1]
                if not self.is_harmless(f"{hook.target}#{method}"):  # `initialize()` and such
                    out.setdefault(symbol, hook)
        return out

    def marks(self, symbol: str) -> list[str]:
        """The framework annotations, decorators, and base types on a function (harmless ones
        included): how a flow it starts is labeled."""
        return self._marks.get(symbol, [])

    def metrics(self) -> dict[str, Any]:
        explained = sum(1 for _, e in self.calls if e)
        hooks_ok = sum(1 for _, e in self.hooks if e)
        return {
            "io_calls": len(self.calls),
            "io_calls_explained": explained,
            "hooks": len(self.hooks),
            "hooks_explained": hooks_ok,
        }


def _constructor(symbol: str) -> bool:
    """`httpx.AsyncClient(...)`, `new OkHttpClient()`: making a client isn't calling it."""
    last = symbol.split("#", 1)[-1] if "#" in symbol else symbol.rsplit(".", 1)[-1]
    return last == "<init>" or last[:1].isupper()


def _exception(symbol: str) -> bool:
    owner = symbol.split("#", 1)[0] if "#" in symbol else symbol.rsplit(".", 1)[0]
    return owner.rsplit(".", 1)[-1].endswith(("Exception", "Error"))


def _within(symbol: str, prefix: str) -> bool:
    """`org.springframework.data.jpa.repository.JpaRepository#save` is within
    `org.springframework.data`, `…JpaRepository`, and `…JpaRepository#save`."""
    if not symbol.startswith(prefix):
        return False
    rest = symbol[len(prefix) :]
    return not rest or rest[0] in ".#(" or prefix.endswith(".")


def short(target: str) -> str:
    return target.replace("#", ".").rsplit(".", 1)[-1]


__all__ = ["HARMLESS", "IO_LIBRARIES", "Gap", "Ledger", "short"]
