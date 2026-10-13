"""What the rule engine produces: raw facts, before they're written to the graph.

docs/how-it-works/ingestion/rules-and-packs.md §3 defines the vocabulary; these are its
in-memory form.
"""

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from steno.resolvers.base import UNRESOLVED

UNRESOLVED_TEXT = "<unresolved>"

# Identity properties per label (rules-and-packs.md §3); the most specific label wins
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

    expr: Any  # the resolver's own expression and scope
    scope: Any
    language: str = "python"


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
    # Config keys a fact needed that no config file in the repository sets (D101): set where
    # the app runs (environment, deploy files, a config server)
    unset_config: list[tuple[Origin, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def split_url(value: Any) -> tuple[str | None, str]:
    """A URL, or a URL template, split into its host (None when unknown) and path:
    `https://{domain}/rest/api/content/{id}` → (None, `/rest/api/content/{id}`);
    `{base_url}/rest/api/2/search` → (None, `/rest/api/2/search`);
    `${suggestion.base-url}suggestion` (a base URL from config) → (None, `/suggestion`)."""
    if not isinstance(value, str) or value == UNRESOLVED_TEXT:
        return None, ""
    if "://" in value:
        host, _, path = value.split("://", 1)[1].partition("/")
        path = "/" + path
    elif value.startswith(("{", "${")):
        rest = value[value.find("}") + 1 :] if "}" in value else ""
        host, path = "", (rest if rest.startswith("/") or not rest else "/" + rest)
    elif value.startswith("/"):
        host, path = "", value
    else:
        host, _, path = value.partition("/")
        path = "/" + path
    host = host.rsplit("@", 1)[-1]
    return (host if host and "{" not in host else None), path


CONFIG_KEY = re.compile(r"\$\{([^}:]+)(?::[^}]*)?\}")


def config_keys(value: Any) -> list[str]:
    """The config keys a URL template's runtime parts come from: `${plaid.api.baseUrl}/x` →
    [`plaid.api.baseUrl`]."""
    return sorted(set(CONFIG_KEY.findall(value))) if isinstance(value, str) else []


def database_of(url: Any) -> dict[str, str] | None:
    """A database connection URL as a DataStore's identity: vendor, host (with port), and
    database. `jdbc:postgresql://db.internal:5432/orders` → postgresql, db.internal:5432,
    orders. Credentials in the URL are dropped. None when it isn't a database URL."""
    if not isinstance(url, str) or url == UNRESOLVED_TEXT:
        return None
    text = url.strip().removeprefix("jdbc:")
    if text.startswith("oracle:"):  # jdbc:oracle:thin:@//host:1521/service or @host:1521:SID
        rest = text.split("@", 1)[-1].lstrip("/")
        host, _, db = rest.partition("/") if "/" in rest else rest.rpartition(":")
        return {"vendor": "oracle", "host": host, "database": db}
    if text.startswith("sqlserver://"):  # jdbc:sqlserver://host:1433;databaseName=orders
        host, _, params = text.removeprefix("sqlserver://").partition(";")
        opts = dict(p.split("=", 1) for p in params.split(";") if "=" in p)
        db = opts.get("databaseName") or opts.get("database") or ""
        return {"vendor": "sqlserver", "host": host, "database": db}
    if text.startswith("h2:"):
        return {"vendor": "h2", "host": "embedded", "database": text.split(":")[-1].split(";")[0]}
    vendor, sep, rest = text.partition("://")
    if not sep:
        return None
    vendor = vendor.split("+")[0].split(":")[-1]  # postgresql+psycopg, mysql:loadbalance
    rest = rest.split("?")[0]
    authority, _, path = rest.partition("/")
    host = authority.rsplit("@", 1)[-1]
    return {"vendor": vendor, "host": host, "database": path.split("/")[0]}


def host_of(value: Any) -> str | None:
    """`https://api.example.com/v1` → `api.example.com`; `localhost:11434` stays as is."""
    if not isinstance(value, str) or value is UNRESOLVED or value == UNRESOLVED_TEXT:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.netloc or None
