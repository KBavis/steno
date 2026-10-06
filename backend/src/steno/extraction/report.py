"""A JSON view of an extraction, for review before anything is written to the graph."""

from typing import Any

from steno.extraction.facts import Extraction
from steno.rule_packs.testing import edge_repr, node_repr, ref_repr


def to_json(x: Extraction) -> dict[str, Any]:
    def where(o: Any) -> str:
        return f"{o.file}:{o.line} ({o.rule})"

    return {
        "nodes": [{**node_repr(n), "source": where(n.origin)} for n in x.nodes],
        "edges": [{**edge_repr(e), "source": where(e.origin)} for e in x.edges],
        "clues": [
            {
                c.kind: {
                    k: ref_repr(v) if not isinstance(v, (str, int, float, dict, list)) else v
                    for k, v in c.fields.items()
                },
                "source": where(c.origin),
            }
            for c in x.clues
        ],
        "entry_points": [
            {"trigger": ref_repr(ep.trigger), "function": ep.function, "source": where(ep.origin)}
            for ep in x.entry_points
        ],
        "dropped": [{"source": where(o), "reason": reason} for o, reason in x.dropped],
        "errors": x.errors,
    }
