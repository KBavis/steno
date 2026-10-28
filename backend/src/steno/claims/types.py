"""The claim types: a fixed set owned by Steno, like clue types
(docs/how-it-works/ingestion/claims-and-reconcile.md §3).

A claim is one witness's statement about the system. Each type knows its facet, its fields,
the subjects it's about (so every claim about one topic or one application can be found), and,
in reconcile, what in the code it's compared with. Packs emit claims; a new type is a core
change, because reconcile has to know how to compare it.
"""

import re
from dataclasses import dataclass
from typing import Any

from steno.graph import ids

WITNESS_TYPES = ("CODE", "PLATFORM", "DECLARED", "ADMIN")

# The name ladder's slots, highest priority first (docs/concepts/identity.md §2)
SLOTS = ("admin", "deploy", "manifest", "build_artifact", "runtime", "path")


@dataclass(frozen=True)
class ClaimType:
    name: str
    facet: str
    kind: str  # evidence | authority
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


CLAIM_TYPES: dict[str, ClaimType] = {
    t.name: t
    for t in (
        ClaimType("channel", "Messaging", "evidence", ("channel",), ("cluster", "attributes")),
        ClaimType("may_produce", "Messaging", "evidence", ("application", "channel")),
        ClaimType("may_consume", "Messaging", "evidence", ("application", "channel"), ("group",)),
        ClaimType(
            "deployable", "Components", "evidence", ("application",), ("kind", "display_name")
        ),
        ClaimType("name", "Components", "authority", ("application", "name", "slot")),
        ClaimType(
            "schedule", "Schedules", "evidence", ("application",), ("cron", "every", "command")
        ),
        # A gateway's route rule (D132): requests matching `path` (and `methods`) go to
        # `target` (`lb://vets-service`, `http://orders:8080`) after the rewrite. `gateway` is
        # the application (or, for infrastructure, the router's name) that holds it; without
        # it, the application whose module holds the claim's file
        ClaimType(
            "route",
            "Routing",
            "evidence",
            ("path", "target"),
            ("gateway", "id", "methods", "strip_prefix", "rewrite", "prefix", "router"),
        ),
    )
}


# Fields whose `${...}` is the value's own syntax, not a config placeholder left unresolved
# (a gateway's `RewritePath=/shop/(?<rest>.*), /${rest}` refers to its own regex group)
TEMPLATE_FIELDS = {"rewrite"}


class InvalidClaim(ValueError):
    pass


def validate(claim_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The fields, checked against the type: required ones present, no unknown ones, a slot
    from the ladder, a channel written as `{Label: name}`. Raises InvalidClaim."""
    t = CLAIM_TYPES.get(claim_type)
    if t is None:
        raise InvalidClaim(f"unknown claim type {claim_type!r}")
    missing = [f for f in t.required if fields.get(f) in (None, "", [], {})]
    if missing:
        raise InvalidClaim(f"{claim_type}: missing {', '.join(missing)}")
    unknown = set(fields) - set(t.required) - set(t.optional)
    if unknown:
        raise InvalidClaim(f"{claim_type}: unknown fields {', '.join(sorted(unknown))}")
    if "slot" in fields and fields["slot"] not in SLOTS:
        raise InvalidClaim(f"{claim_type}: slot must be one of {', '.join(SLOTS)}")
    if "channel" in fields:
        channel_key(fields["channel"])
    for f in ("application", "name"):
        if f in fields and not isinstance(fields[f], str):
            raise InvalidClaim(f"{claim_type}: {f} must be text")
    return fields


def channel_key(channel: Any) -> str:
    """`{KafkaTopic: payment.settled}` → `kafkatopic:payment.settled`, the graph's channel ID
    (D61), so a claim and a code fact about one topic share a key."""
    if not isinstance(channel, dict) or len(channel) != 1:
        raise InvalidClaim(f"a channel is written {{Label: name}}, not {channel!r}")
    ((label, name),) = channel.items()
    if not isinstance(name, str) or not name:
        raise InvalidClaim(f"channel {label} has no name")
    return ids.channel_id(str(label), name)


def subjects(claim_type: str, fields: dict[str, Any]) -> list[str]:
    """What the claim is about, as keys: `channel:kafkatopic:payment.settled`, and
    `application:ledger-svc-prod` (an application by the name the witness uses; resolved to a
    Steno ID at reconcile time, so a name linked later still matches)."""
    out = []
    if "channel" in fields:
        out.append(f"channel:{channel_key(fields['channel'])}")
    if "application" in fields:
        out.append(f"application:{fields['application']}")
    if claim_type == "name":
        out.append(f"application:{fields['name']}")
    if claim_type == "route":
        if fields.get("gateway"):
            out.append(f"application:{fields['gateway']}")
        if (name := route_target(str(fields.get("target") or ""))) is not None:
            out.append(f"application:{name}")
    return sorted(set(out))


def route_target(target: str) -> str | None:
    """The application a route's target names (D132): `lb://vets-service` (a service
    registry name), or a bare cluster host (`http://orders:8080`, `orders.shop.svc`); None for
    an outside host (`https://api.example.com`), which is no application."""
    m = re.match(r"^(?:(lb|https?|wss?)://)?([^/:?#]+)", target.strip())
    if not m or not m.group(2):
        return None
    scheme, host = m.group(1), m.group(2)
    if scheme == "lb":
        return host
    if host.endswith((".svc", ".svc.cluster.local")):
        return host.split(".", 1)[0]
    return host if "." not in host and host != "localhost" else None
