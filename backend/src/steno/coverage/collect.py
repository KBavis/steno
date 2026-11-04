"""The coverage report's signals: what no rule explained.

See docs/how-it-works/ingestion/coverage-report.md.

Each signal is a place a fact could hide. Everything here is deterministic and read from the
run that just happened: the engine's facts and dropped matches, the symbol resolver's view of
the code, and the graph plan's notes (effects no flow reaches, calls whose host is unknown).

Items are keyed by (kind, target), so the same gap in another run, or another repository,
is the same item: that's how triage carries over and how the org-wide report adds up (D62).
"""

import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from steno.accounting import FILE_IO, Ledger, _within
from steno.extraction.facts import Extraction
from steno.graph.build import GraphPlan
from steno.resolvers.base import Function, Resolvers
from steno.resolvers.java import _DOMAINS
from steno.rule_packs.packs import Pack, Rule
from steno.rule_packs.versions import Dependency, in_range, versions_of
from steno.source.files import is_test_file

SAMPLES = 5

# Libraries whose calls usually reach the network, a store, or a broker. With IO_VERBS, the
# stand-in for Jev I8 ("does this call look like I/O?"): a library call named like I/O
# (`session.execute`, `client.post`) is worth a rule if no rule explains it. Constructors and
# query builders (`httpx.AsyncClient()`, `select(...)`) aren't I/O themselves.
IO_PACKAGES = {
    "aiohttp", "aiokafka", "anthropic", "asyncpg", "atlassian", "boto3", "botocore", "chromadb",
    "confluent_kafka", "elasticsearch", "ftplib", "github", "google", "grpc", "httpx", "jira",
    "kafka", "langchain", "llama_index", "motor", "neo4j", "ollama", "openai", "paramiko",
    "pika", "psycopg", "psycopg2", "pymongo", "redis", "requests", "slack_sdk", "smtplib",
    "socket", "sqlalchemy", "urllib", "urllib3", "websockets",
    # Java, by package (resolvers/java.py, library_of)
    "com.amazonaws", "com.datastax.oss", "com.rabbitmq", "feign", "io.grpc", "io.lettuce",
    "jakarta.jms", "javax.jms",
    "okhttp3", "org.apache.kafka", "org.apache.http", "org.mongodb", "org.springframework.amqp",
    "org.springframework.data", "org.springframework.jdbc", "org.springframework.jms",
    "org.springframework.kafka", "org.springframework.web", "redis.clients", "software.amazon",
}  # fmt: skip
# Drops that are rules working as designed, not gaps: a broad match that turned out not to be
# about a table (`table_of`), or a query built outside any function
EXPECTED_DROPS = ("table_of: not a mapped entity", "@function outside a function")

# Signals kept as patterns but left out of the report (D146): unread files and settings are
# input for a rule author who asks for them; untested or unknown library versions belong to
# rule health. Everything else in the report points at the surface.
QUIET = {"unread_file", "version_untested", "version_unknown"}
# A config key that names a connection: unread under any prefix, it's still an item (D146)
COMMUNICATION_KEY = re.compile(
    r"(^|[.\-_])(urls?|uris?|hosts?|endpoints?|base-?url|topics?|queues?|exchanges?|channels?"
    r"|destinations?|bootstrap-servers|brokers?|bindings?|datasource)($|[.\-_])"
    r"|^spring\.cloud\.stream\.",
    re.IGNORECASE,
)
# A framework hook whose name says the framework starts work through it: ranked first (D146)
STARTS_WORK = re.compile(
    r"(Listener|Handler|Consumer|Subscriber|Receiver|Scheduled|Schedule|Job|Task|Filter|"
    r"Interceptor|Controller|Endpoint|Mapping|Route|Command|Tool|Worker|Processor|Poller)$"
)
# What a rule emits that's on the surface (D136): a dropped match of such a rule lost a fact
SURFACE_NODES = {
    "Application", "Interface", "HttpEndpoint", "GrpcMethod", "KafkaTopic", "Queue", "Table",
    "DataStore", "ExternalSystem", "Route", "Gateway", "Schedule",
}  # fmt: skip
SURFACE_EDGES = {"CALLS", "PRODUCES", "CONSUMES", "READS_FROM", "WRITES_TO", "STARTS", "EXPOSES"}


@dataclass
class Item:
    kind: str  # CoverageKind value
    signal: str  # finer than kind: unknown_host, dropped_match, …
    target: str  # the key: a symbol, library, class, rule, or file
    label: str  # how it reads in the report
    occurrences: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)
    applications: dict[str, int] = field(default_factory=dict)  # application → occurrences
    # Every place it occurs: (file, enclosing symbol, application). The symbol, not the line,
    # so an edit that moves lines isn't new code (D121)
    keys: set[tuple[str, str, str]] = field(default_factory=set)
    # Why it may matter beyond its reach (D122), e.g. "referenced_by_code"
    evidence: set[str] = field(default_factory=set)
    # Config values seen (unread config), for evidence that they name things in the graph
    values: set[str] = field(default_factory=set)
    # Kept as a pattern but left out of the report (D146): see QUIET
    quiet: bool = False

    @property
    def fingerprint(self) -> str:
        """What it is, across the organization: the pattern it belongs to (D121)."""
        return f"{self.signal}:{self.target}"

    def add(self, sample: dict[str, Any]) -> None:
        self.occurrences += 1
        app = sample.get("application")
        if app:
            self.applications[app] = self.applications.get(app, 0) + 1
        symbol = sample.get("function") or sample.get("detail") or ""
        self.keys.add((sample.get("file") or "", str(symbol), app or ""))
        if len(self.samples) < SAMPLES:
            self.samples.append(sample)


@dataclass
class Report:
    items: list[Item]
    # Completeness numbers for the repository (D62)
    metrics: dict[str, Any]


# Source code in a language no pack reads: one pattern per language, wherever it sits
CODE_SUFFIXES = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".go", ".kt", ".kts", ".scala", ".rb",
    ".cs", ".php", ".rs", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".dart", ".ex",
    ".exs", ".clj", ".groovy", ".py", ".java",
}  # fmt: skip
NOISE_DIRS = {".github", ".gitlab", ".idea", ".vscode", ".mvn", "gradle", ".circleci"}
NOISE_NAMES = {"LICENSE", "NOTICE", "gradlew", "mvnw", "Makefile", "Dockerfile", "Jenkinsfile"}
# Build files: read for dependencies and versions, not by rules
BUILD_NAMES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "gradle.properties", "libs.versions.toml", "pyproject.toml", "setup.py", "setup.cfg",
    "requirements.txt", "package.json",
}  # fmt: skip


def collect(
    x: Extraction,
    resolver: Resolvers | None,
    plan: GraphPlan,
    all_packs: list[Pack],
    app_of: Callable[[str | None], str] | None = None,
    enabled: list[Pack] | None = None,
    deps: list[Dependency] | None = None,
    disagreements: list[dict[str, Any]] | None = None,
    unmatched: list[dict[str, Any]] | None = None,
    files: list[str] | None = None,
    read: set[str] | None = None,
    root: Path | None = None,
    config_used: set[str] | None = None,
    in_library: Callable[[str], bool] | None = None,
    ignored_files: set[tuple[str, str]] | None = None,
    internal_prefixes: list[str] | None = None,
    repository: str | None = None,
    library_modules: int | None = None,
) -> Report:
    """`app_of`: the application a file belongs to (its service module's), so items and
    numbers can be shown per application, not just per repository. `enabled`: the packs that
    ran, checked against `deps`, the versions the build files declare. `internal_prefixes`:
    the organization's own code (D146). `repository` and `library_modules`: for telling
    whether the whole repository produced nothing (a silent repository)."""
    items: dict[tuple[str, str, str], Item] = {}
    where = app_of or (lambda _file: "")

    class _Tagged(Item):
        def add(self, sample: dict[str, Any]) -> None:
            super().add({**sample, "application": where(sample.get("file"))})

    def item(kind: str, signal: str, target: str, label: str) -> Item:
        key = (kind, signal, target)
        if key not in items:
            items[key] = _Tagged(kind, signal, target, label)
        return items[key]

    # Seconds per step, in the stage's metrics: what to make faster on a large repository
    timings: dict[str, float] = {}

    @contextmanager
    def timed(name: str) -> Iterator[None]:
        started = time.perf_counter()
        yield
        timings[name] = round(time.perf_counter() - started, 2)

    rules = {(r.pack, r.id): r for p in all_packs for r in p.rules}
    with timed("entries"):
        _unknown_hosts(plan, resolver, item)
        _inferred(plan, item)
        _kindless(plan, item)
        _unreachable(plan, resolver, item)
        dropped = _dropped(x, item, rules)
        unparsed = _unparsed(x)
    no_io: dict[str, Any] = {"total": 0, "explained": 0, "by_app": {}}
    with timed("external_calls"):
        ledger = (plan.ledger or Ledger(x, resolver, all_packs)) if resolver else None
        io = _external_calls(x, resolver, all_packs, item, where, ledger) if resolver else no_io
        if resolver and ledger is not None:
            _library_gaps(ledger, resolver, all_packs, item, internal_prefixes or [])
    with timed("hooks_config_libraries"):
        _hooks(plan, item)
        _config(x, item)
        if resolver:
            _libraries(resolver, all_packs, items, item)
        _versions(enabled or [], deps or [], item)
        _agreement(disagreements or [], unmatched or [], item)
        _silent_witnesses(x, enabled or [], item)
    if files is not None:
        noise = [g for p in all_packs for g in p.noise_files]
        with timed("unread_files"):
            _unread_files(files, read or set(), item, where, noise, ignored_files)
        with timed("referenced"):
            _referenced(items, root, read or set())
    if config_used is not None:
        prefixes = [k for p in all_packs for k in p.noise_config]
        with timed("unread_config"):
            _unread_config(x, config_used, prefixes, item, where)
    with timed("names_known"):
        _names_known(items, plan, root)
    if in_library is not None:
        with timed("in_library"):
            _in_library(items, in_library)
    if repository is not None and library_modules is not None and files is not None:
        _silent_repository(x, plan, repository, library_modules, files, read or set(), item)
    _quiet(items)
    gaps = _unresolved_types(resolver) if resolver else 0

    stats = plan.stats
    reachable, total = stats.get("functions_reachable", 0), stats.get("functions", 0)
    ranked = sorted(
        items.values(),
        key=lambda i: (
            i.quiet,
            ORDER.index(i.signal) if i.signal in ORDER else len(ORDER),
            "can_start_work" not in i.evidence,
            -i.occurrences,
        ),
    )
    return Report(
        ranked,
        {
            "timings": timings,
            "functions": total,
            "functions_reachable": reachable,
            "reachable_pct": round(100 * reachable / total, 1) if total else None,
            "io_calls": io["total"],
            "io_calls_explained": io["explained"],
            "io_explained_pct": round(100 * io["explained"] / io["total"], 1)
            if io["total"]
            else None,
            # Calls that ran through a library type no JAR was available for (D69)
            "unresolved_library_calls": gaps,
            "unknown_hooks": len(plan.stats.get("unknown_hooks", [])),
            "config_unset": len({key for _, key in x.unset_config}),
            # Numbers, not items (D146): files Steno couldn't read, and rule matches it couldn't
            # complete that wouldn't have stated a surface fact
            "unparsed_files": unparsed,
            "dropped_matches": dropped,
            "items": sum(1 for i in ranked if not i.quiet),
            "quiet_items": sum(1 for i in ranked if i.quiet),
            "by_signal": _count(i.signal for i in ranked),
            "applications": _per_application(resolver, plan, io["by_app"], ranked, where),
        },
    )


# The report's order (D146): connections between applications first, then ways in, then
# libraries (internal before external), then the rest of the surface
ORDER = [
    "silent_repository",
    "silent_witness",
    "unknown_host",
    "disagreement",
    "unmatched_name",
    "unknown_hook",
    "internal_library",
    "external_call",
    "config_unset",
    "inferred_entry",
    "no_trigger_application",
    "library",
    "external_library",
    "unreachable_effect",
    "unread_config",
    "file_call",
    "dropped_match",
    "unread_file",
    "version_untested",
    "version_unknown",
]


def _per_application(
    r: Resolvers | None,
    plan: GraphPlan,
    io_by_app: dict[str, dict[str, int]],
    items: list[Item],
    where: Callable[[str | None], str],
) -> dict[str, dict[str, int]]:
    """The completeness numbers for each application in the repository."""
    out: dict[str, dict[str, int]] = defaultdict(
        lambda: {"functions": 0, "functions_reachable": 0, "io_calls": 0, "io_calls_explained": 0}
    )
    for fn in r.functions() if r else []:
        app = out[where(_rel(r, fn))]
        app["functions"] += 1
        app["functions_reachable"] += int(fn.symbol in plan.reached)
    for name, io in io_by_app.items():
        out[name]["io_calls"] += io["total"]
        out[name]["io_calls_explained"] += io["explained"]
    for it in items:
        for name in it.applications:
            out[name]["items"] = out[name].get("items", 0) + 1
    return {name: dict(v) for name, v in out.items() if name}


# ---------------------------------------------------------------- the signals


def _unknown_hosts(plan: GraphPlan, r: Resolvers | None, item: Any) -> None:
    """HTTP calls whose host is runtime data (D64): one item per calling class, like the
    graph's `Unknown host · …` node."""
    for u in plan.stats.get("unmet_joins", []):
        if u.get("kind") != "http_host":
            continue
        fn = _function(r, u.get("function"))
        owner = fn.container if fn else u.get("function") or ""
        file = (u.get("file") or "").rsplit("/", 1)[-1]
        label = f"Unknown host · {owner.rsplit('.', 1)[-1]}" + (f" ({file})" if file else "")
        item("unmet_join", "unknown_host", owner, label).add(
            {
                "file": u.get("file"),
                "line": u.get("line"),
                "function": u.get("function"),
                "detail": u.get("url"),
            }
        )


def _kindless(plan: GraphPlan, item: Any) -> None:
    """Applications none of whose flows has a known trigger (D85): no kind is guessed. A rule
    for whatever starts it (a framework's annotation, a listener) gives it one."""
    for a in plan.stats.get("applications_without_kind", []):
        item("unreachable", "no_trigger_application", a["id"],
             f"No known trigger · {a['application']}").add(
            {"file": "", "function": "", "application": a["application"],
             "detail": "nothing found starts its flows, so its kind is unknown"}
        )  # fmt: skip


def _inferred(plan: GraphPlan, item: Any) -> None:
    """Flows Steno started itself because nothing in the code starts them (D74): a worker loop,
    a CLI command, a framework callback. Each is a flow already; naming its trigger takes a
    rule (an entry point), which also makes the flow certain instead of inferred."""
    for u in plan.stats.get("inferred_entries", []):
        short = ".".join(u["function"].replace("#", ".").split("(")[0].split(".")[-2:])
        it = item("unreachable", "inferred_entry", u["function"], f"No known trigger · {short}")
        it.add(
            {
                "file": u.get("file"),
                "line": u.get("line"),
                "function": u["function"],
                "detail": "started by something outside the code; flow inferred",
            }
        )


def _unreachable(plan: GraphPlan, r: Resolvers | None, item: Any) -> None:
    """Code with effects that no entry point reaches: an entry point (a receiver, a schedule)
    is probably missing, or the call graph can't see how it's reached (runtime dispatch)."""
    for u in plan.stats.get("unreached_effects", []):
        fn = _function(r, u["function"])
        it = item("unreachable", "unreachable_effect", u["function"], u["function"])
        it.add(
            {
                "file": _rel(r, fn),
                "line": fn.span[0] if fn else None,
                "function": u["function"],
                "detail": ", ".join(sorted(set(u["effects"]))),
            }
        )


def _dropped(x: Extraction, item: Any, rules: dict[tuple[str, str], Rule]) -> int:
    """Matches a rule found but couldn't complete because Steno couldn't tell: a condition it
    couldn't check, a name it couldn't resolve. An item only when the rule states a surface
    fact (D146); the rest are counted. Drops that are the rule working as designed
    (EXPECTED_DROPS) aren't gaps. Returns how many were counted, not listed."""
    counted = 0
    for origin, reason in x.dropped:
        if reason in EXPECTED_DROPS:
            continue
        rule = rules.get((origin.pack, origin.rule))
        if rule is not None and not _on_surface(rule):
            counted += 1
            continue
        why = reason.split(":", 1)[0] if reason.startswith("where") else reason
        target = f"{origin.rule}: {why}"
        item("unmet_join", "dropped_match", target, target).add(
            {"file": origin.file, "line": origin.line, "detail": reason}
        )
    return counted


def _on_surface(rule: Rule) -> bool:
    """Whether a rule states something on the surface: an entry point, a claim, or a node or
    edge an application's surface is made of."""
    for e in rule.emit:
        if not isinstance(e, dict):
            continue
        if {"entry_point", "claim", "interceptor"} & set(e):
            return True
        if _names(e.get("node")) & SURFACE_NODES or _names(e.get("edge")) & SURFACE_EDGES:
            return True
        if _names(e.get("to")) & SURFACE_NODES:
            return True
    return False


def _names(value: Any) -> set[str]:
    """The type names an emit field holds: `KafkaTopic`, `[Table, View]`, `{KafkaTopic: {…}}`."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {str(k) for k in value}
    if isinstance(value, list):
        return {n for v in value for n in _names(v)}
    return set()


def _unparsed(x: Extraction) -> int:
    """Files Steno couldn't read at all, and build modules the Java resolver couldn't resolve
    when it read a repository module by module (D127): a number on the application (D146)."""
    return sum(
        1
        for error in x.errors
        if "syntax error" in (m := error.partition(": ")[2]) or m.startswith("Java resolver failed")
    )


def _external_calls(
    x: Extraction,
    r: Resolvers,
    packs: list[Pack],
    item: Any,
    where: Callable[[str | None], str],
    ledger: Any = None,
) -> dict[str, Any]:
    """Library calls that can do I/O (declared by core lists and packs, D101) that no rule
    explains, grouped by the library function called (`com.x.Publisher#send`). Explained: a
    rule matched on the call's lines, or the library's pack recorded something in the same
    function (`stmt = select(Job)` is the read; `session.execute(stmt)` runs it)."""
    ledger = ledger or Ledger(x, r, packs)
    total = covered = 0
    by_app: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "explained": 0})
    for call, explained in ledger.calls:
        rel = r.rel(call.function.path)
        counts = by_app[where(rel)]
        total += 1
        counts["total"] += 1
        if explained:
            covered += 1
            counts["explained"] += 1
            continue
        # Local files are still a gap, ranked below the network, messaging, and databases
        signal = "file_call" if any(_within(call.symbol, f) for f in FILE_IO) else "external_call"
        item("external_call", signal, call.symbol, call.symbol).add(
            {"file": rel, "line": call.line, "function": call.function.symbol,
             "library": call.library}
        )  # fmt: skip
    return {"total": total, "explained": covered, "by_app": dict(by_app)}


def _hooks(plan: GraphPlan, item: Any) -> None:
    """Framework hooks no rule explains (D101): code a framework runs (a request filter, a
    validator, a CLI command) that may start work or touch something the graph doesn't show.
    One item per framework type, annotation, or decorator, so one rule fixes them all."""
    for h in plan.stats.get("unknown_hooks", []):
        target = h["target"]
        name = target.replace("#", ".").rsplit(".", 1)[-1]
        label = f"Framework hook no rule explains · {name}"
        it = item("unmet_join", "unknown_hook", target, label)
        it.add(
            {"file": h["file"], "line": h["line"], "function": h["symbol"],
             "detail": f"{h['kind']} {target}"}
        )  # fmt: skip
        if STARTS_WORK.search(name.split("(", 1)[0]):
            it.evidence.add("can_start_work")


def _config(x: Extraction, item: Any) -> None:
    """Config keys a fact needs that no config file in the repository sets (D101): set where
    the app runs. The fact keeps the key; its value isn't known."""
    for origin, key in x.unset_config:
        item("config", "config_unset", key, f"Config set outside the repository · {key}").add(
            {"file": origin.file, "line": origin.line, "detail": f"read by rule {origin.rule}"}
        )


def _libraries(r: Resolvers, packs: list[Pack], items: dict[Any, Item], item: Any) -> None:
    """Libraries the code imports that no rule pack covers, when they're I/O libraries or
    have unexplained I/O calls: each is a pack (or an org rule) worth writing."""
    covered = set().union(*(_libs(p) for p in packs)) if packs else set()
    with_calls = {
        i.samples[0].get("library") or i.target.split(".")[0]
        for (kind, _, _), i in items.items()
        if kind == "external_call" and i.samples
    }
    files: dict[str, list[str]] = defaultdict(list)
    for resolver in r:
        for lib, found in resolver.imported_libraries().items():
            files[lib].extend(found)
    for lib, where in sorted(files.items()):
        if _covers(covered, _norm(lib)) or (lib not in IO_PACKAGES and lib not in with_calls):
            continue
        it = item("library", "library", lib, lib)
        for f in sorted(where):
            it.add({"file": f, "detail": f"imports {lib}"})


def _library_gaps(
    ledger: Ledger, r: Resolvers, packs: list[Pack], item: Any, prefixes: list[str]
) -> None:
    """Calls into a library no rule explains, that isn't declared I/O or harmless, and that no
    pack covers (D146): one item per library. *Internal* (the organization's code, by
    `prefixes` or the repository's own package roots): Steno hasn't read it, and it may wrap a
    call to another application. *External*: a public or vendor library no list knows."""
    covered = set().union(*(_libs(p) for p in packs)) if packs else set()
    roots = _own_roots(r)
    for call, explained in ledger.others:
        library = call.library
        if explained or _covers(covered, _norm(library)) or _standard(library):
            continue
        internal = is_internal(call.symbol, library, prefixes, roots)
        signal = "internal_library" if internal else "external_library"
        label = (
            f"Internal library not followed · {library}"
            if internal
            else f"External library no rule explains · {library}"
        )
        item("library", signal, library, label).add(
            {"file": r.rel(call.function.path), "line": call.line,
             "function": call.function.symbol, "detail": call.symbol}
        )  # fmt: skip


def _standard(library: str) -> bool:
    """The language's own library (`urllib.parse`, `javax.crypto`): its I/O is declared in the
    core lists, and the rest can't reach the surface."""
    return library.split(".")[0] in sys.stdlib_module_names or library.startswith(
        ("java.", "javax.")
    )


def is_internal(symbol: str, library: str, prefixes: list[str], roots: set[str]) -> bool:
    """Whether a library is the organization's own code: under one of its prefixes (a Java
    package `com.acme`, a coordinate `com.acme:*`, a Python package prefix `acme_`), or under
    one of the repository's own package roots (`com.acme` for code in `com.acme.billing`)."""
    for raw in [*prefixes, *roots]:
        p = raw.strip().removesuffix(".*").removesuffix("*")
        if ":" in p:
            p = p.split(":", 1)[0]  # a coordinate's group is its package
        if not p:
            continue
        if p.endswith(("_", "-", ".")):
            if library.startswith(p) or symbol.startswith(p):
                return True
        elif any(n == p or n.startswith(p + ".") for n in (library, symbol)):
            return True
    return False


def _own_roots(r: Resolvers) -> set[str]:
    """The organization-level package roots of the repository's own Java code: `com.acme`
    for `com.acme.billing.Invoice`. Python packages have no such root, so it's prefixes only."""
    roots: set[str] = set()
    for fn in r.functions():
        if "#" not in fn.symbol:
            continue
        parts = [p for p in fn.symbol.split("#", 1)[0].split(".") if p and not p[0].isupper()]
        n = 2 if parts and parts[0] in _DOMAINS else 1
        if len(parts) > n:  # a root that's the whole package would match nothing else
            roots.add(".".join(parts[:n]))
    return roots


def _silent_witnesses(x: Extraction, enabled: list[Pack], item: Any) -> None:
    """A PLATFORM or DECLARED pack that ran (its files are here) but stated nothing: the
    witness is broken, not missing (D146)."""
    stated = {
        o.pack
        for o in [
            *(c.origin for c in x.claims),
            *(n.origin for n in x.nodes),
            *(e.origin for e in x.edges),
        ]
    }
    for pack in enabled:
        if pack.witness_type == "CODE" or pack.name in stated or not pack.rules:
            continue
        it = item(
            "unmet_join", "silent_witness", pack.name, f"Witness stated nothing · {pack.name}"
        )
        it.add(
            {
                "file": "",
                "detail": f"{pack.witness_type} pack ran on this repository and "
                "stated nothing: its files changed shape, or its rules don't match them",
            }
        )


def _silent_repository(
    x: Extraction,
    plan: GraphPlan,
    repository: str,
    library_modules: int,
    files: list[str],
    read: set[str],
    item: Any,
) -> None:
    """A repository that produced nothing (D146): no applications, no claims, no library
    modules. Usually a witness no pack reads (a config, deploy, or ACL repository), a library
    that isn't linked yet, or a repository with nothing to say. The item lists what it holds."""
    apps = [n for n in plan.nodes.values() if "Application" in n.labels and not n.props.get("stub")]
    if apps or x.claims or library_modules:
        return
    unread = [f for f in files if f not in read and not is_test_file(Path(f))]
    kinds: dict[str, int] = defaultdict(int)
    for rel in unread:
        p = PurePosixPath(rel)
        kinds[f"*{p.suffix.lower() or p.name} under {convention(rel)}"] += 1
    holds = sorted(kinds.items(), key=lambda kv: -kv[1])[:6]
    it = item("unmet_join", "silent_repository", repository,
              f"Repository that produced nothing · {repository}")  # fmt: skip
    it.add({"file": "", "detail": "holds " + ", ".join(f"{n} {k}" for k, n in holds)
            if holds else "holds no files Steno reads"})  # fmt: skip


def _quiet(items: dict[Any, Item]) -> None:
    """Mark what's kept as a pattern but left out of the report (D146)."""
    for it in items.values():
        if it.signal in QUIET:
            it.quiet = True
        elif it.signal == "unread_config":
            keys = [str(s.get("function") or "") for s in it.samples] + [it.target]
            keys += [sym for _, sym, _ in it.keys]
            it.quiet = not any(COMMUNICATION_KEY.search(k) for k in keys)


def _unread_files(
    files: list[str],
    read: set[str],
    item: Any,
    where: Callable[[str | None], str],
    noise: list[str] | None = None,
    ignored: set[tuple[str, str]] | None = None,
) -> None:
    """Files no rule looked at (D122), grouped by extension and where they sit, so 40
    repositories' `*.xyz under resources/flows/` is one pattern. Noise and build files aren't
    listed, nor file names someone ignored within their pattern (`ignored`: fingerprint and
    name); everything else is, ranked low until something says it matters."""
    for rel in files:
        if rel in read:
            continue
        p = PurePosixPath(rel)
        if p.name in BUILD_NAMES or is_test_file(Path(rel)) or is_noise(rel, noise or []):
            continue
        kind = p.suffix.lower() or p.name
        if kind in CODE_SUFFIXES:
            target, label = f"{kind}@*", f"Code in a language no pack reads · *{kind}"
        else:
            place = convention(rel)
            target = f"{kind}@{place}"
            label = (
                f"Unread files · *{kind} under {place}"
                if p.suffix
                else f"Unread · {p.name} in {place}"
            )
        if ignored and (f"unread_file:{target}", p.name) in ignored:
            continue
        item("config", "unread_file", target, label).add(
            {"file": rel, "function": "", "application": where(rel)}
        )


FILE_TOKEN = re.compile(r"[\w][\w.-]*")  # a file name, as code writes it in a path or string


def _referenced(items: dict[Any, Item], root: Path | None, read: set[str]) -> None:
    """An unread file whose name the code mentions (`getResource("flows/order.xyz")`) is
    probably wiring, not noise: evidence that ranks its pattern higher (D122)."""
    unread = [i for i in items.values() if i.signal == "unread_file"]
    if root is None or not unread:
        return
    names: dict[str, list[Item]] = defaultdict(list)
    for it in unread:
        for file, _, _ in it.keys:
            name = PurePosixPath(file).name
            if len(name) > 4:  # too short to tell apart from ordinary words
                names[name].append(it)
    # Each file is read once and its file-name-shaped tokens looked up, so the cost follows
    # the size of the code, not names × code (one alternation of every name took 134 s on
    # Keycloak). A whole token must match: `myorder.xyz` doesn't mention `order.xyz`.
    for rel in sorted(read):
        if not names:
            return
        if PurePosixPath(rel).suffix not in CODE_SUFFIXES:
            continue
        try:
            text = (root / rel).read_text(errors="replace")
        except OSError:
            continue
        for token in set(FILE_TOKEN.findall(text)) & names.keys():
            for it in names.pop(token):
                it.evidence.add("referenced_by_code")


def _unread_config(
    x: Extraction,
    used: set[str],
    noise: list[str],
    item: Any,
    where: Callable[[str | None], str],
) -> None:
    """Config keys set in the repository that nothing read (D122): no rule stated a fact from
    them and no `${placeholder}` or settings lookup used them. Grouped by prefix
    (`acme.messaging.*`), so one framework's settings are one pattern. Noise prefixes from the
    packs (`logging`, `management`, …) aren't listed."""
    for clue in x.clues:
        key = clue.fields.get("key") if clue.kind == "property" else None
        if not isinstance(key, str) or key in used or _under(key, noise):
            continue
        prefix = config_prefix(key)
        it = item("config", "unread_config", prefix, f"Config no rule read · {prefix}.*")
        it.add({"file": clue.origin.file, "function": key, "line": clue.origin.line,
                "detail": key, "application": where(clue.origin.file)})  # fmt: skip
        value = clue.fields.get("value")
        if isinstance(value, str):
            it.values.add(value)


MAX_READ = 512 * 1024  # bytes of an unread file looked at for names
FILES_PER_ITEM = 50


def known_names(plan: GraphPlan) -> set[str]:
    """Names in this run's graph worth recognizing elsewhere: topics, queues, and org channels,
    endpoint paths (their constant part), tables, and applications. Short plain words are left
    out, so ordinary words don't count."""
    out: set[str] = set()
    for node in plan.nodes.values():
        labels = set(node.labels)
        if "HttpEndpoint" in labels:
            path = str(node.props.get("path") or "").split("{", 1)[0].rstrip("/")
            name = path if path.count("/") >= 2 else ""
        elif labels & {"Table", "Application"} or (
            "Interface" in labels and "GrpcMethod" not in labels
        ):
            name = str(node.props.get("name") or "")
        else:
            continue
        # A plain short word (`users`, `orders`) is too ordinary to be evidence of anything
        if len(name) >= 8 or (len(name) >= 5 and re.search(r"[._/-]", name)):
            out.add(name)
    return out


def _names_known(items: dict[Any, Item], plan: GraphPlan, root: Path | None) -> None:
    """An unread file or config value that names a topic, endpoint, table, or application
    already in the graph is probably wiring (D122): evidence that ranks it higher."""
    unread = [i for i in items.values() if i.signal in ("unread_file", "unread_config")]
    names = known_names(plan)
    if not unread or not names:
        return
    pattern = re.compile(
        r"(?<![\w.-])(?:"
        + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        + r")(?![\w-]|\.\w)"  # `orders.created.v2` is another name
    )
    for it in unread:
        texts = list(it.values)
        if it.signal == "unread_file" and root is not None:
            for file, _, _ in sorted(it.keys)[:FILES_PER_ITEM]:
                try:
                    with (root / file).open("rb") as fh:
                        texts.append(fh.read(MAX_READ).decode(errors="replace"))
                except OSError:
                    continue
        if any(pattern.search(t) for t in texts):
            it.evidence.add("names_known_things")


def _in_library(items: dict[Any, Item], in_library: Callable[[str], bool]) -> None:
    """A pattern that occurs inside one of the organization's own library modules is likely
    part of an internal framework (D122): evidence that ranks it higher."""
    for it in items.values():
        if any(file and in_library(file) for file, _, _ in it.keys):
            it.evidence.add("in_internal_library")


def config_prefix(key: str) -> str:
    """`acme.messaging.topics.orders` → `acme.messaging`; a one-segment key is its own."""
    parts = key.split(".")
    return ".".join(parts[:2]) if len(parts) > 1 else key


def _under(key: str, prefixes: list[str]) -> bool:
    return any(key == p or key.startswith(p + ".") for p in prefixes)


def is_noise(rel: str, globs: list[str]) -> bool:
    """Whether a file matches a noise glob (from the packs' `noise.files`), by its name or by
    its path from any folder down (`.github/**` matches `svc/.github/ci.yml`)."""
    parts = PurePosixPath(rel).parts
    tails = ["/".join(parts[i:]) for i in range(len(parts))]
    return any(fnmatch(t, g) for g in globs for t in tails)


def convention(rel: str) -> str:
    """Where a file sits, as a convention shared across repositories: the folders after
    `src/main/` or `src/` (`resources/flows/`), else its parent folder's name."""
    parts = PurePosixPath(rel).parts[:-1]
    for marker in (("src", "main"), ("src",)):
        n = len(marker)
        for i in range(len(parts) - n + 1):
            if tuple(parts[i : i + n]) == marker:
                rest = parts[i + n :]
                return "/".join(rest) + "/" if rest else "/"
    return (parts[-1] + "/") if parts else "/"


def _agreement(
    disagreements: list[dict[str, Any]], unmatched: list[dict[str, Any]], item: Any
) -> None:
    """Where the code and another witness disagree (claims-and-reconcile.md §8), and names a
    witness uses that match no application. Each is a "look here": a missing rule, a stale
    declaration, a use nothing grants, or an application not ingested."""
    for d in disagreements:
        role = "produces to" if d["role"] == "produces" else "consumes from"
        why = (
            "code only: no other witness grants or states it"
            if d["agreement"] == "code_only"
            else "declared only: no code doing it was found"
        )
        it = item(
            "inconsistency",
            "disagreement",
            f"{d['agreement']}|{d['app']}|{d['role']}|{d['channel']}",
            f"{d['app_name']} {role} {d['channel'].split(':', 1)[-1]} · {why}",
        )
        for s in d["sources"]:
            it.add(
                {
                    "file": s.get("file"),
                    "line": s.get("line"),
                    "detail": f"{s['witness_type']}: {s['witness']}",
                }
            )
    for u in unmatched:
        it = item(
            "unmet_join",
            "unmatched_name",
            u["name"],
            f"Name no application matches · {u['name']}",
        )
        it.add(
            {
                "file": u.get("file"),
                "line": u.get("line"),
                "detail": f"{u.get('witness_type')}: {u.get('witness')}",
            }
        )


def _versions(packs: list[Pack], deps: list[Dependency], item: Any) -> None:
    """Packs that ran on a library version they aren't tested on, or where no build file
    states the version of any library they're tested against: their rules ran, but nothing
    says the syntax they match is what this version uses."""
    for pack in packs:
        if not pack.versions:
            continue
        found = [
            (key, spec, d) for key, spec in pack.versions.items() for d in versions_of(deps, key)
        ]
        if not found:
            keys = ", ".join(f"{k} {s}" for k, s in pack.versions.items())
            it = item(
                "library",
                "version_unknown",
                f"{pack.name} · version",
                f"Version not stated · {pack.name} is tested on {keys}",
            )
            it.add({"detail": "no build file in the repository states the library's version"})
        for key, spec, d in found:
            if in_range(d.version or "", spec):
                continue
            it = item(
                "library",
                "version_untested",
                f"{pack.name} · {key} {d.version}",
                f"{key} {d.version} · {pack.name} is tested on {spec}",
            )
            it.add({"file": d.file, "detail": f"{d.name} {d.version}"})


# ---------------------------------------------------------------- helpers


def _origins(x: Extraction) -> list[Any]:
    """Where every rule produced something, or tried to (a dropped match is reported on its
    own, not again as an unexplained call)."""
    return [
        *(n.origin for n in x.nodes),
        *(e.origin for e in x.edges),
        *(c.origin for c in x.clues),
        *(ep.origin for ep in x.entry_points),
        *(o for o, _ in x.dropped),
    ]


def _matched_lines(x: Extraction) -> set[tuple[str, int]]:
    return {(o.file, o.line) for o in _origins(x)}


def _unresolved_types(r: Resolvers) -> int:
    """How many calls ran through a library type Steno can't see (no dependency JARs, D69):
    a number for deciding about JARs, not items. A chain through a library type is accounted
    for at the library call it starts from (D101)."""
    return sum(
        1
        for resolver in r
        for fn in resolver.functions()
        for gap in resolver.unresolved_calls(fn)
        if gap.reason == "library type" and gap.library
    )


def _libs(pack: Pack) -> set[str]:
    """The libraries a pack covers: what turns it on. A Maven dependency (`group:artifact`)
    covers its group's packages too."""
    when = pack.enabled_when
    names = [*when.get("imports", []), *when.get("dependencies", [])]
    return {_norm(n) for n in names} | {_norm(n.split(":")[0]) for n in names if ":" in n}


def _covers(libs: set[str], lib: str) -> bool:
    """A pack covers a library it names, or a package inside one it names."""
    return lib in libs or any(lib.startswith(name + "_") for name in libs)


def _function(r: Resolvers | None, symbol: str | None) -> Function | None:
    return r.function(symbol) if r else None


def _rel(r: Resolvers | None, fn: Function | None) -> str | None:
    return r.rel(fn.path) if r and fn else None


def _norm(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(out)


__all__ = ["Item", "Report", "collect"]
