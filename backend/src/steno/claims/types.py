"""The claim types: a fixed set owned by Steno, like clue types
(docs/how-it-works/ingestion/claims-and-reconcile.md §3).

A claim is one witness's statement about the system. Each type knows its facet, its fields,
the subjects it's about (so every claim about one topic or one application can be found), and,
in reconcile, what in the code it's compared with. Packs emit claims; a new type is a core
change, because reconcile has to know how to compare it.
"""

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
    )
}


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
    return sorted(set(out))
