"""What the rule engine produces: raw facts, before they're written to the graph.

docs/rule-packs.md §3 defines the vocabulary; these are its in-memory form.
"""

import ast
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from steno.resolvers.python import UNRESOLVED, Scope

UNRESOLVED_TEXT = "<unresolved>"

# Identity properties per label (rule-packs.md §3); the most specific label wins
IDENTITY = {
    "HttpEndpoint": ("method", "path"),
    "GrpcMethod": ("service", "method"),
    "KafkaTopic": ("name",),
    "Queue": ("name",),
    "Schedule": ("kind", "expression"),
    "Entity": ("name",),
    "Table": ("name",),
    "DataStore": ("vendor", "host", "database"),
    "ExternalSystem": ("host",),
    "Function": ("symbol",),
}


@dataclass(frozen=True)
class Origin:
    rule: str
    pack: str
    file: str
    line: int


# ---------------------------------------------------------------- references


@dataclass(frozen=True)
class AppRef:
    """@app: the application being ingested."""


@dataclass(frozen=True)
class NodeRef:
    """A node by label and (possibly partial) identity, e.g. {DataStore: {vendor: chroma}}."""

    label: str
    props: tuple[tuple[str, Any], ...]

    @classmethod
    def of(cls, label: str, props: dict[str, Any]) -> "NodeRef":
        return cls(label, tuple(sorted(props.items(), key=lambda kv: kv[0])))

    @property
    def as_dict(self) -> dict[str, Any]:
        return dict(self.props)


@dataclass(frozen=True)
class HttpRef:
    """An outbound HTTP target; later resolved to another app's endpoint or an ExternalSystem."""

    method: str
    url: str


@dataclass
class TableOfRef:
    """The table an entity maps to; filled in when assembling, dropped if it isn't an entity."""

    expr: ast.expr
    scope: Scope


Ref = AppRef | NodeRef | HttpRef | TableOfRef


# ---------------------------------------------------------------------- facts


@dataclass
class NodeFact:
    labels: list[str]
    props: dict[str, Any]
    origin: Origin
    # Completed by an assembler, e.g. prefixed_by → the router whose prefix chain applies
    pending: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """The most specific label with an identity: HttpEndpoint for [Interface, HttpEndpoint],
        DataStore for [DataStore, Vector] (a category label, not a type)."""
        return next((lbl for lbl in reversed(self.labels) if lbl in IDENTITY), self.labels[-1])

    def ref(self) -> NodeRef:
        # An org-defined Interface label (a channel, D61) is identified by its name, like a topic
        keys = IDENTITY.get(self.label) or (("name",) if "Interface" in self.labels else ())
        return NodeRef.of(self.label, {k: self.props[k] for k in keys if k in self.props})


@dataclass
class EdgeFact:
    type: str
    src: Ref | NodeFact
    dst: Ref | NodeFact
    props: dict[str, Any]
    origin: Origin


@dataclass
class ClueFact:
    kind: str
    fields: dict[str, Any]
    origin: Origin


@dataclass
class EntryPointFact:
    trigger: NodeFact | NodeRef
    function: str
    origin: Origin


@dataclass
class Extraction:
    nodes: list[NodeFact] = field(default_factory=list)
    edges: list[EdgeFact] = field(default_factory=list)
    clues: list[ClueFact] = field(default_factory=list)
    entry_points: list[EntryPointFact] = field(default_factory=list)
    # Facts a rule matched but couldn't complete: (origin, reason)
    dropped: list[tuple[Origin, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def host_of(value: Any) -> str | None:
    """`https://api.example.com/v1` → `api.example.com`; `localhost:11434` stays as is."""
    if not isinstance(value, str) or value is UNRESOLVED or value == UNRESOLVED_TEXT:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.netloc or None
