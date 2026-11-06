"""Each flow's digest (D143): a short, deterministic text of what starts the flow and what it
does across its application's edge, for keyword search and routing (Jev's Q4) to read.

No LLM. Built from the plan once every flow, step, and rollup edge is in it:

    POST /owners · customers-service
    TRIGGER   http POST /owners (public)
    DOC       Registers a new owner. · Checks the owner isn't already registered.
    WRITES    owners (insert)
    READS     owners (select)
    CALLS     GET /vets (vets-service) · Stripe
    PRODUCES  owners.created
    ENTITIES  Owner, CreateOwnerRequest (request)
    UNIQUE    owners.created, OwnerResource.createOwner
    FUNCTIONS OwnerResource.createOwner, OwnerService.create, OwnerRepository.save
    NOTE      2 calls Steno couldn't follow: the flow may do more

DOC is what the code says about itself: the first sentence of the docstring or Javadoc of the
entry function and the functions closest to it. Interceptors that run in front of the handler
(security filters) are left out of DOC, UNIQUE, and FUNCTIONS: they say nothing about the flow.
UNIQUE is what no other flow in the same application touches or runs, so flows that share
most of their code can still be told apart.

Bounded by construction, however many functions the flow runs (docs/plan/later/jev.md §4):
at most LIMIT targets per line and FUNCTIONS functions, nearest the entry first, each line
saying how many more there are. Lines with nothing to say are left out.
"""

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from steno.graph import trace as trace_codec
from steno.graph.query import split_identifiers

if TYPE_CHECKING:
    from steno.graph.build import GraphEdge, GraphPlan

# At most this many targets per line, and this many function names
LIMIT = 8
FUNCTIONS = 12
# A name longer than this is cut (a URL template with a long query, a generated class name)
NAME_CHARS = 80
# The DOC line: at most this many docs, from functions at most this deep (1 is the entry)
DOCS = 3
DOC_DEPTH = 2
DOC_CHARS = 160
# The effect lines, in the order they're written: what the flow changes before what it reads,
# then what crosses to other applications (D136's surface)
LINES = (
    ("WRITES_TO", "WRITES"),
    ("READS_FROM", "READS"),
    ("CALLS", "CALLS"),
    ("PRODUCES", "PRODUCES"),
    ("CONSUMES", "CONSUMES"),
)
WIDTH = max(
    len(w)
    for w in ("TRIGGER", "DOC", "ENTITIES", "UNIQUE", "FUNCTIONS", "NOTE", *(w for _, w in LINES))
)


def write_digests(plan: "GraphPlan", docs: dict[str, str] | None = None) -> None:
    """Set `digest` on every Flow in the plan, and `name_words` on every searchable node: its
    name's (and a flow's digest's) identifiers split into words, so keyword search finds
    `git_commit` from `git commit` (`graph.query.split_identifiers`). `docs`: each function's
    doc, by symbol."""
    index = _Index(plan, docs)
    for node in plan.nodes.values():
        if "Flow" in node.labels:
            node.props["digest"] = flow_digest(plan, node.id, index)
        if "Searchable" in node.labels:
            text = f"{node.props.get('name', '')}\n{node.props.get('digest', '')}"
            node.props["name_words"] = split_identifiers(text) or None


def flow_digest(plan: "GraphPlan", flow: str, index: "_Index | None" = None) -> str:
    index = index or _Index(plan)
    props = plan.nodes[flow].props
    out = index.rollups(flow)
    trace = index.trace(flow)
    app = index.app_of.get(flow)
    lines = [" · ".join(x for x in (_cut(props.get("name", flow)), _name(plan, app)) if x)]
    lines.append(_line("TRIGGER", [_trigger(props)]))
    lines.append(_line("DOC", _docs(trace, index.docs), limit=DOCS, sep=" · "))
    for edge_type, word in LINES:
        targets = sorted(out.get(edge_type, []), key=lambda e: _rank(plan, e))
        lines.append(_line(word, [_target(plan, index, e) for e in targets]))
    lines.append(_line("ENTITIES", _entities(plan, index, flow, out)))
    lines.append(_line("UNIQUE", _unique(plan, index, flow, app)))
    lines.append(_line("FUNCTIONS", [_short(t) for t in _nearest_first(trace)], limit=FUNCTIONS))
    lines.append(_line("NOTE", _notes(props), sep="; "))
    return "\n".join(x for x in lines if x)


class _Index:
    """What every flow's digest reads, built once: the plan's edges by source and by target,
    each flow's application, and how many of an application's flows touch each target or run
    each function (for UNIQUE). Only one flow's decoded trace is held at a time: all of them
    together are the largest thing in the stage, and each is read one flow after another."""

    def __init__(self, plan: "GraphPlan", docs: dict[str, str] | None = None) -> None:
        self.plan = plan
        self.docs = docs or {}
        self.out: dict[str, list[GraphEdge]] = defaultdict(list)
        self.into: dict[str, list[GraphEdge]] = defaultdict(list)
        for e in plan.edges.values():
            self.out[e.src].append(e)
            self.into[e.dst].append(e)
        self.app_of: dict[str, str] = {
            e.src: e.dst
            for e in plan.edges.values()
            if e.type == "BELONGS_TO"
            and "Flow" in plan.nodes[e.src].labels
            and "Application" in plan.nodes[e.dst].labels
        }
        self.flows_in: Counter[str] = Counter(self.app_of.values())
        self._trace_of: tuple[str, list[dict[str, Any]]] | None = None
        self.shared: dict[str | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for node in plan.nodes.values():
            if "Flow" not in node.labels:
                continue
            counts = self.shared[self.app_of.get(node.id)]
            for item in {*self._targets(node.id), *self._symbols(node.id)}:
                counts[item] += 1

    def rollups(self, flow: str) -> dict[str, list["GraphEdge"]]:
        """The flow's rollup edges by type, in the plan's order."""
        out: dict[str, list[GraphEdge]] = defaultdict(list)
        for e in self.out.get(flow, []):
            if e.props.get("rollup"):
                out[e.type].append(e)
        return out

    def trace(self, flow: str) -> list[dict[str, Any]]:
        """The flow's own trace: without the interceptors that run before its handler (security
        filters and the like, D103) and what they call. They run in front of many flows, so
        their docs and functions would crowd out what this flow does; the flow keeps them in
        `interceptors` and its full trace, and what they require is the agent's to read (D138)."""
        if self._trace_of is None or self._trace_of[0] != flow:
            raw = self.plan.nodes[flow].props.get("trace")
            entries = trace_codec.decode(raw) if raw else []
            ahead = {str(t["path"]) for t in entries if t.get("interceptor")}
            kept = [t for t in entries if str(t["path"]).split(".", 1)[0] not in ahead]
            self._trace_of = (flow, kept)
        return self._trace_of[1]

    def _targets(self, flow: str) -> set[str]:
        return {e.dst for es in self.rollups(flow).values() for e in es}

    def _symbols(self, flow: str) -> set[str]:
        return {t["symbol"] for t in self.trace(flow)}


def _line(word: str, items: list[str], limit: int = LIMIT, sep: str = ", ") -> str:
    items = list(dict.fromkeys(i for i in items if i))
    if not items:
        return ""
    shown = items[:limit]
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return f"{word.ljust(WIDTH)} {sep.join(shown)}{more}"


def _cut(text: str) -> str:
    return text if len(text) <= NAME_CHARS else text[: NAME_CHARS - 1] + "…"


def _trigger(props: dict[str, Any]) -> str:
    kind = props.get("trigger_kind") or ""
    if props.get("inferred"):
        via = props.get("framework")
        return f"unknown (inferred entry point{', called by ' + via if via else ''})"
    access = props.get("access")
    return f"{kind} {_cut(str(props.get('trigger', '')))}".strip() + (
        f" ({access})" if access else ""
    )


def _rank(plan: "GraphPlan", e: "GraphEdge") -> tuple[int, str]:
    """Another application's interface first, then named systems, then hosts Steno can't name."""
    target = plan.nodes[e.dst]
    if target.props.get("stub"):
        group = 2
    elif "Interface" in target.labels:
        group = 0
    else:
        group = 1
    return group, str(target.props.get("name", e.dst))


def _target(plan: "GraphPlan", index: _Index, e: "GraphEdge") -> str:
    target = plan.nodes[e.dst]
    name = _cut(str(target.props.get("name", e.dst)))
    if e.type in ("READS_FROM", "WRITES_TO") and e.props.get("operation"):
        return f"{name} ({e.props['operation']})"
    if e.type == "CALLS" and "Interface" in target.labels:
        owner = _owner(plan, index, e.dst)
        return f"{name} ({owner})" if owner else name
    return name


def _owner(plan: "GraphPlan", index: _Index, interface: str) -> str | None:
    """The application that serves an interface, when the plan knows it."""
    for e in index.out.get(interface, []):
        if e.type == "BELONGS_TO" and "Application" in plan.nodes[e.dst].labels:
            return plan.nodes[e.dst].props.get("name")
    for e in index.into.get(interface, []):
        if e.type == "EXPOSES" and e.src in plan.nodes:
            return plan.nodes[e.src].props.get("name")
    return None


def _entities(
    plan: "GraphPlan", index: _Index, flow: str, out: dict[str, list["GraphEdge"]]
) -> list[str]:
    """The classes the flow takes in and hands on (its trigger's and its calls' payloads, D115),
    then those mapped to the tables it touches."""
    found: list[str] = []
    triggers = [e.src for e in index.into.get(flow, []) if e.type == "STARTS"]
    sent = [e.dst for t in ("CALLS", "PRODUCES") for e in out.get(t, [])]
    for interface in [*triggers, *sent]:
        for e in index.out.get(interface, []):
            if e.type == "USES_ENTITY":
                role = e.props.get("role")
                found.append(_entity(plan, e.dst) + (f" ({role})" if role else ""))
    tables = {e.dst for t in ("WRITES_TO", "READS_FROM") for e in out.get(t, [])}
    for table in sorted(tables):
        for e in index.into.get(table, []):
            if e.type == "MAPS_TO":
                found.append(_entity(plan, e.src))
    return found


def _entity(plan: "GraphPlan", entity: str) -> str:
    name = str(plan.nodes[entity].props.get("name", entity)) if entity in plan.nodes else entity
    return _cut(name.rsplit(".", 1)[-1])


def _name(plan: "GraphPlan", node: str | None) -> str | None:
    return plan.nodes[node].props.get("name") if node and node in plan.nodes else None


def _depth(entry: dict[str, Any]) -> int:
    return str(entry["path"]).count(".") + 1


def _nearest_first(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The trace nearest the entry first: what a flow is for shows in the functions it calls
    directly more than in the helpers far below them. Stable, so ties keep the trace's order."""
    return sorted(trace, key=_depth)


def _docs(trace: list[dict[str, Any]], docs: dict[str, str]) -> list[str]:
    """What the code says about itself: the docs of the entry and of the steps closest to it
    (functions that lead to an effect, not helpers such as opening a session), nearest first."""
    near = [
        t
        for t in _nearest_first(trace)
        if _depth(t) == 1 or (_depth(t) <= DOC_DEPTH and t.get("step") and not t.get("repeat"))
    ]
    out = []
    for t in near:
        doc = docs.get(t["symbol"])
        if doc:
            text = " ".join(doc.split())
            out.append(text if len(text) <= DOC_CHARS else text[: DOC_CHARS - 1] + "…")
    return out


def _unique(plan: "GraphPlan", index: _Index, flow: str, app: str | None) -> list[str]:
    """What no other flow in the application touches or runs: its own effects first, then its
    own functions nearest the entry. Nothing when it's the application's only flow."""
    siblings = index.flows_in[app] if app else 0
    if siblings < 2:
        return []
    counts = index.shared[app]
    out = index.rollups(flow)
    targets = sorted(
        {e.dst for es in out.values() for e in es if counts.get(e.dst) == 1},
        key=lambda t: str(plan.nodes[t].props.get("name", t)),
    )
    functions = [t for t in _nearest_first(index.trace(flow)) if counts.get(t["symbol"]) == 1]
    return [
        *(_cut(str(plan.nodes[t].props.get("name", t))) for t in targets),
        *(_short(t) for t in functions),
    ]


def _short(entry: dict[str, Any]) -> str:
    container = (entry.get("container") or "").rsplit(".", 1)[-1]
    name = entry.get("name") or entry["symbol"].rsplit(".", 1)[-1]
    # A module-level function in Python: its container is the module; the name says enough
    if container and container[:1].isupper():
        return _cut(f"{container}.{name}")
    return _cut(name)


def _notes(props: dict[str, Any]) -> list[str]:
    notes = []
    if props.get("unresolved_calls"):
        n = props["unresolved_calls"]
        notes.append(f"{n} call{'s' if n != 1 else ''} Steno couldn't follow: the flow may do more")
    return notes
