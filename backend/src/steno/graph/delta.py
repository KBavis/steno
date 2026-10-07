"""What a run changes: the plan compared with what the graph already holds (D59).

Only differences are written, so a run over unchanged code writes nothing, and a refactor
that changes no behavior writes traces but no architecture nodes. The same comparison
produces the change history (`fact_change`, D18). Pure: the writer reads the graph, this
module decides.

Two kinds of property don't count as a change:
- **Provenance** (the job, commit, and source location that last wrote a fact).
- **Code pointers** (a step's file and lines, a trace's line numbers), as long as the code
  they point to is the same. A fact that isn't rewritten keeps the commit its pointers were
  read at, so its citations stay exact.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from steno.graph.build import POINTERS, SHARED, GraphPlan

VOLATILE = {
    "ingestion_job",
    "last_seen",
    "first_seen",
    "commit",
    "source_file",
    "source_line",
}
# Pointers into the code on Steps and Flows (a trace's are its entries' POINTERS)
NODE_POINTERS = {"file", *POINTERS, "entry_file", "entry_start_line", "entry_end_line"}
# Written by later stages (cards), never part of a plan
LATER = ("card",)
TRACE_FIELDS = {"trace", "trace_files", "trace_symbols"}

EdgeKey = tuple[str, str, str]


@dataclass
class Existing:
    """What the graph holds for this repository, plus any plan node another one wrote."""

    nodes: dict[str, tuple[list[str], dict[str, Any]]] = field(default_factory=dict)
    edges: dict[EdgeKey, dict[str, Any]] = field(default_factory=dict)
    repo_nodes: set[str] = field(default_factory=set)  # ids whose `repo` is this repository


@dataclass
class Delta:
    # (id, before props or None, after props or None)
    nodes: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = field(
        default_factory=list
    )
    edges: list[tuple[EdgeKey, dict[str, Any] | None, dict[str, Any] | None]] = field(
        default_factory=list
    )
    labels: dict[str, list[str]] = field(default_factory=dict)  # node id → labels

    def nodes_to_write(self) -> set[str]:
        return {nid for nid, _, after in self.nodes if after is not None}

    def edges_to_write(self) -> set[EdgeKey]:
        return {key for key, _, after in self.edges if after is not None}

    def nodes_to_remove(self) -> list[str]:
        return [nid for nid, _, after in self.nodes if after is None]

    def edges_to_remove(self) -> list[EdgeKey]:
        return [key for key, _, after in self.edges if after is None]

    def counts(self) -> dict[str, dict[str, int]]:
        def tally(rows: list[Any]) -> dict[str, int]:
            out = {"added": 0, "modified": 0, "removed": 0}
            for _, before, after in rows:
                out["added" if before is None else "removed" if after is None else "modified"] += 1
            return out

        return {"nodes": tally(self.nodes), "edges": tally(self.edges)}


def diff(plan: GraphPlan, existing: Existing) -> Delta:
    delta = Delta()
    for nid, node in plan.nodes.items():
        delta.labels[nid] = node.labels
        old = existing.nodes.get(nid)
        if old is None:
            delta.nodes.append((nid, None, node.props))
        elif _comparable(old[1]) != _comparable(node.props) or not set(node.labels) <= set(old[0]):
            delta.nodes.append((nid, old[1], node.props))
    for nid in sorted(existing.repo_nodes - set(plan.nodes)):
        labels, props = existing.nodes[nid]
        if SHARED & set(labels) or props.get("shared"):
            continue  # removed only when nothing references it any more (orphan cleanup)
        delta.labels[nid] = labels
        delta.nodes.append((nid, props, None))

    for key, edge in plan.edges.items():
        old_props = existing.edges.get(key)
        if old_props is None:
            delta.edges.append((key, None, edge.props))
        elif _comparable(old_props) != _comparable(edge.props):
            delta.edges.append((key, old_props, edge.props))
    removed_nodes = set(delta.nodes_to_remove())
    for key in sorted(set(existing.edges) - set(plan.edges)):
        if key[1] in removed_nodes or key[2] in removed_nodes:
            continue  # goes with its node
        delta.edges.append((key, existing.edges[key], None))
    return delta


def nulls_for(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, None]:
    """Properties a rewrite should drop: set before, gone now. Never first_seen or cards."""
    if before is None:
        return {}
    return {
        k: None for k in before if k not in after and k != "first_seen" and not k.startswith(LATER)
    }


def _comparable(props: dict[str, Any]) -> dict[str, Any]:
    out = {
        k: v
        for k, v in props.items()
        if k not in VOLATILE and k not in NODE_POINTERS and not k.startswith(LATER)
    }
    if isinstance(out.get("trace"), str):
        out["trace"] = [_strip_pointers(e) for e in json.loads(out["trace"])]
    return out


def _strip_pointers(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in POINTERS}


# ---------------------------------------------------------------- change history


def changes(delta: Delta) -> list[dict[str, Any]]:
    """`fact_change` rows (D18): one per node or edge added, removed, or modified, without
    traces, plus one per flow whose trace changed, listing the functions added, removed,
    and modified (by body hash)."""
    rows: list[dict[str, Any]] = []
    for nid, before, after in delta.nodes:
        labels = delta.labels.get(nid, [])
        kind = labels[0] if labels else "Node"
        b, a = _without_trace(before), _without_trace(after)
        if before is None or after is None or _comparable(b or {}) != _comparable(a or {}):
            rows.append(_row(nid, kind, before, after, b, a))
        if "Flow" in labels:
            trace = _trace_change(before, after)
            if trace:
                rows.append(
                    {
                        "fact_id": nid,
                        "fact_type": "Trace",
                        "change": _kind(before, after),
                        "before": None,
                        "after": trace,
                    }
                )
    for (etype, src, dst), before, after in delta.edges:
        rows.append(
            _row(f"{etype}|{src}|{dst}", etype, before, after, _clean(before), _clean(after))
        )
    return rows


def _row(
    fact_id: str,
    fact_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    b: dict[str, Any] | None,
    a: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "change": _kind(before, after),
        "before": _clean(b),
        "after": _clean(a),
    }


def _kind(before: Any, after: Any) -> str:
    return "added" if before is None else "removed" if after is None else "modified"


def _without_trace(props: dict[str, Any] | None) -> dict[str, Any] | None:
    if props is None:
        return None
    return {k: v for k, v in props.items() if k not in TRACE_FIELDS}


def _clean(props: dict[str, Any] | None) -> dict[str, Any] | None:
    if props is None:
        return None
    return {k: v for k, v in props.items() if k not in VOLATILE and not k.startswith(LATER)}


def _trace_change(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any] | None:
    old, new = _trace_of(before), _trace_of(after)
    if [_strip_pointers(e) for e in old] == [_strip_pointers(e) for e in new]:
        return None

    def bodies(trace: list[dict[str, Any]]) -> dict[str, str]:
        return {_fn(e): e.get("body_hash", "") for e in trace}

    o, n = bodies(old), bodies(new)
    out: dict[str, Any] = {
        "added": sorted(set(n) - set(o)),
        "removed": sorted(set(o) - set(n)),
        "modified": sorted(k for k in set(o) & set(n) if o[k] != n[k]),
    }
    out = {k: v for k, v in out.items() if v}
    return out or {"reordered": True}


def _trace_of(props: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (props or {}).get("trace")
    return json.loads(raw) if isinstance(raw, str) else []


def _fn(entry: dict[str, Any]) -> str:
    bound = entry.get("bound_to")
    return f"{entry['symbol']}@{bound}" if bound else entry["symbol"]
