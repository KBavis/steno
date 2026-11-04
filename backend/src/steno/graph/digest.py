"""Each flow's digest (D143): a short, deterministic text of what starts the flow and what it
does across its application's edge, for keyword search and routing (Jev's Q4) to read.

No LLM. Built from the plan once every flow, step, and rollup edge is in it:

    POST /owners · customers-service
    TRIGGER   http POST /owners (public)
    WRITES    owners (insert)
    READS     owners (select)
    CALLS     GET /vets (vets-service) · Stripe
    PRODUCES  owners.created
    ENTITIES  Owner, CreateOwnerRequest (request)
    FUNCTIONS OwnerResource.createOwner, OwnerService.create, OwnerRepository.save
    NOTE      2 calls Steno couldn't follow: the flow may do more

Bounded by construction, however many functions the flow runs (docs/plan/later/jev.md §4):
at most LIMIT targets per line and FUNCTIONS functions, the significant ones (its steps) first,
each line saying how many more there are. Lines with nothing to say are left out.
"""

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from steno.graph import trace as trace_codec

if TYPE_CHECKING:
    from steno.graph.build import GraphEdge, GraphPlan

# At most this many targets per line, and this many function names
LIMIT = 8
FUNCTIONS = 12
# A name longer than this is cut (a URL template with a long query, a generated class name)
NAME_CHARS = 80
# The effect lines, in the order they're written: what the flow changes before what it reads,
# then what crosses to other applications (D136's surface)
LINES = (
    ("WRITES_TO", "WRITES"),
    ("READS_FROM", "READS"),
    ("CALLS", "CALLS"),
    ("PRODUCES", "PRODUCES"),
    ("CONSUMES", "CONSUMES"),
)
WIDTH = max(len(w) for w in ("TRIGGER", "ENTITIES", "FUNCTIONS", "NOTE", *(w for _, w in LINES)))


def write_digests(plan: "GraphPlan") -> None:
    """Set `digest` on every Flow in the plan."""
    index = _Index(plan)
    for node in plan.nodes.values():
        if "Flow" in node.labels:
            node.props["digest"] = flow_digest(plan, node.id, index)


def flow_digest(plan: "GraphPlan", flow: str, index: "_Index | None" = None) -> str:
    index = index or _Index(plan)
    props = plan.nodes[flow].props
    out = defaultdict(list)  # edge type → rollup edges, in the plan's order
    for e in index.out.get(flow, []):
        if e.props.get("rollup"):
            out[e.type].append(e)

    app = next(
        (
            plan.nodes[e.dst].props.get("name")
            for e in index.out.get(flow, [])
            if e.type == "BELONGS_TO" and "Application" in plan.nodes[e.dst].labels
        ),
        None,
    )
    lines = [" · ".join(x for x in (_cut(props.get("name", flow)), app) if x)]
    lines.append(_line("TRIGGER", [_trigger(props)]))
    for edge_type, word in LINES:
        targets = sorted(out.get(edge_type, []), key=lambda e: _rank(plan, e))
        lines.append(_line(word, [_target(plan, index, e) for e in targets]))
    lines.append(_line("ENTITIES", _entities(plan, index, flow, out)))
    lines.append(_line("FUNCTIONS", _functions(props), limit=FUNCTIONS))
    lines.append(_line("NOTE", _notes(props), sep="; "))
    return "\n".join(x for x in lines if x)


class _Index:
    """The plan's edges by source and by target, built once for every flow."""

    def __init__(self, plan: "GraphPlan") -> None:
        self.out: dict[str, list[GraphEdge]] = defaultdict(list)
        self.into: dict[str, list[GraphEdge]] = defaultdict(list)
        for e in plan.edges.values():
            self.out[e.src].append(e)
            self.into[e.dst].append(e)


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


def _functions(props: dict[str, Any]) -> list[str]:
    """Its steps' functions in order (the ones that lead to an effect), then the rest."""
    trace = trace_codec.decode(props.get("trace")) if props.get("trace") else []
    steps = [_short(t) for t in trace if t.get("step")]
    rest = [_short(t) for t in trace if not t.get("step")]
    return [*steps, *rest]


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
