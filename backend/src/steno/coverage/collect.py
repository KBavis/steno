"""The coverage report's signals: what no rule explained.

See docs/how-it-works/ingestion/coverage-report.md.

Each signal is a place a fact could hide. Everything here is deterministic and read from the
run that just happened: the engine's facts and dropped matches, the symbol resolver's view of
the code, and the graph plan's notes (effects no flow reaches, calls whose host is unknown).

Items are keyed by (kind, target), so the same gap in another run, or another repository,
is the same item: that's how triage carries over and how the org-wide report adds up (D62).
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from steno.accounting import Ledger
from steno.extraction.facts import Extraction
from steno.graph.build import GraphPlan
from steno.resolvers.base import Function, Resolvers
from steno.rule_packs.packs import Pack
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
) -> Report:
    """`app_of`: the application a file belongs to (its service module's), so items and
    numbers can be shown per application, not just per repository. `enabled`: the packs that
    ran, checked against `deps`, the versions the build files declare."""
    items: dict[tuple[str, str], Item] = {}
    where = app_of or (lambda _file: "")

    class _Tagged(Item):
        def add(self, sample: dict[str, Any]) -> None:
            super().add({**sample, "application": where(sample.get("file"))})

    def item(kind: str, signal: str, target: str, label: str) -> Item:
        key = (kind, target)
        if key not in items:
            items[key] = _Tagged(kind, signal, target, label)
        return items[key]

    _unknown_hosts(plan, resolver, item)
    _inferred(plan, item)
    _unreachable(plan, resolver, item)
    _dropped(x, item)
    _unparsed(x, item)
    no_io: dict[str, Any] = {"total": 0, "explained": 0, "by_app": {}}
    io = _external_calls(x, resolver, all_packs, item, where, plan.ledger) if resolver else no_io
    _hooks(plan, item)
    _config(x, item)
    if resolver:
        _libraries(resolver, all_packs, items, item)
    _versions(enabled or [], deps or [], item)
    _agreement(disagreements or [], unmatched or [], item)
    if files is not None:
        noise = [g for p in all_packs for g in p.noise_files]
        _unread_files(files, read or set(), item, where, noise)
        _referenced(items, root, read or set())
    if config_used is not None:
        prefixes = [k for p in all_packs for k in p.noise_config]
        _unread_config(x, config_used, prefixes, item, where)
    _names_known(items, plan, root)
    if in_library is not None:
        _in_library(items, in_library)
    gaps = _unresolved_types(resolver) if resolver else 0

    stats = plan.stats
    reachable, total = stats.get("functions_reachable", 0), stats.get("functions", 0)
    order = [
        "unknown_hook",
        "unknown_host",
        "disagreement",
        "unmatched_name",
        "external_call",
        "config_unset",
        "library",
        "version_untested",
        "version_unknown",
        "unresolved_type",
        "inferred_entry",
        "unreachable_effect",
        "dropped_match",
    ]
    ranked = sorted(
        items.values(),
        key=lambda i: (order.index(i.signal) if i.signal in order else 9, -i.occurrences),
    )
    return Report(
        ranked,
        {
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
            "items": len(ranked),
            "by_signal": _count(i.signal for i in ranked),
            "applications": _per_application(resolver, plan, io["by_app"], ranked, where),
        },
    )


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


def _dropped(x: Extraction, item: Any) -> None:
    """Matches a rule found but couldn't complete because Steno couldn't tell: a condition it
    couldn't check, a name it couldn't resolve. Drops that are the rule working as designed
    (EXPECTED_DROPS) aren't gaps."""
    for origin, reason in x.dropped:
        if reason in EXPECTED_DROPS:
            continue
        why = reason.split(":", 1)[0] if reason.startswith("where") else reason
        target = f"{origin.rule}: {why}"
        item("unmet_join", "dropped_match", target, target).add(
            {"file": origin.file, "line": origin.line, "detail": reason}
        )


def _unparsed(x: Extraction, item: Any) -> None:
    """Files Steno couldn't read at all: nothing in them is in the graph."""
    for error in x.errors:
        file, _, message = error.partition(": ")
        if "syntax error" in message:
            item("unparsed", "unparsed", file, file).add({"file": file, "detail": message})


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
        item("external_call", "external_call", call.symbol, call.symbol).add(
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
        label = f"Framework hook no rule explains · {target.replace('#', '.').rsplit('.', 1)[-1]}"
        item("unmet_join", "unknown_hook", target, label).add(
            {"file": h["file"], "line": h["line"], "function": h["symbol"],
             "detail": f"{h['kind']} {target}"}
        )  # fmt: skip


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
        for (kind, _), i in items.items()
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


def _unread_files(
    files: list[str],
    read: set[str],
    item: Any,
    where: Callable[[str | None], str],
    noise: list[str] | None = None,
) -> None:
    """Files no rule looked at (D122), grouped by extension and where they sit, so 40
    repositories' `*.xyz under resources/flows/` is one pattern. Noise and build files aren't
    listed; everything else is, ranked low until something says it matters."""
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
        item("config", "unread_file", target, label).add(
            {"file": rel, "function": "", "application": where(rel)}
        )


def _referenced(items: dict[Any, Item], root: Path | None, read: set[str]) -> None:
    """An unread file whose name the code mentions (`getResource("flows/order.xyz")`) is
    probably wiring, not noise: evidence that ranks its pattern higher (D122)."""
    unread = [i for (_, _), i in items.items() if i.signal == "unread_file"]
    if root is None or not unread:
        return
    names: dict[str, list[Item]] = defaultdict(list)
    for it in unread:
        for file, _, _ in it.keys:
            name = PurePosixPath(file).name
            if len(name) > 4:  # too short to tell apart from ordinary words
                names[name].append(it)
    if not names:
        return
    pattern = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))
    for rel in sorted(read):
        if PurePosixPath(rel).suffix not in CODE_SUFFIXES:
            continue
        try:
            text = (root / rel).read_text(errors="replace")
        except OSError:
            continue
        for m in set(pattern.findall(text)):
            for it in names.pop(m, []):
                it.evidence.add("referenced_by_code")
        if not names:
            return
        pattern = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))


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
