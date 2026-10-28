"""Routing a request through a gateway (D132), worked out when asked, not stored per endpoint.

A route is stored once per route rule: its path pattern (Spring's: `**` any rest of the path,
`*` one segment, `{name}` one segment), the methods it takes, and its rewrite: strip a number
of leading segments, rewrite the path with a regex, or add a prefix. Here, a request's outside
path becomes the path the target application sees, and an endpoint's path becomes the outside
path that reaches it (when the rewrite can be reversed: strip and prefix can, a regex can't).
"""

import re
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=512)
def _pattern(pattern: str) -> re.Pattern[str]:
    out = []
    for part in re.split(r"(\*\*|\*|\{[^/}]+\})", pattern):
        if part == "**":
            out.append(".*")
        elif part == "*" or (part.startswith("{") and part.endswith("}")):
            out.append("[^/]+")
        else:
            out.append(re.escape(part))
    regex = "".join(out)
    # `/api/vet/**` also matches `/api/vet` itself
    regex = regex.replace(r"/.*", r"(?:/.*)?") if regex.endswith(r"/.*") else regex
    return re.compile(f"^{regex}$")


def matches(pattern: str, path: str) -> bool:
    """Whether a path (concrete, or a template like `/owners/{id}`) fits a path pattern."""
    return bool(_pattern(pattern).match(path))


def takes(route: dict[str, Any], method: str | None) -> bool:
    methods = [m.strip().upper() for m in str(route.get("methods") or "").split(",") if m.strip()]
    return not methods or method is None or method.upper() in methods


def inside_path(route: dict[str, Any], path: str) -> str | None:
    """The path the target application sees for a request on `path`, or None when the route
    doesn't match it."""
    if not matches(str(route["path"]), path):
        return None
    out = path
    if strip := int(route.get("strip_prefix") or 0):
        out = "/" + "/".join(out.strip("/").split("/")[strip:])
    if rewrite := route.get("rewrite"):
        regex, _, replacement = str(rewrite).partition(",")
        # Java's named groups and `${name}` references, in Python's syntax
        regex = re.sub(r"\(\?<(?![=!])", "(?P<", regex.strip())
        replacement = re.sub(r"\$\{(\w+)\}", r"\\g<\1>", replacement.strip())
        out = re.sub(regex, replacement, out)
    if prefix := route.get("prefix"):
        out = "/" + str(prefix).strip("/") + ("" if out == "/" else out)
    return out or "/"


def outside_path(route: dict[str, Any], inside: str) -> str | None:
    """The path that reaches `inside` (an endpoint's path) through the route, or None when
    the rewrite can't be reversed (a regex) or no request through the route gets there."""
    if route.get("rewrite"):
        return None
    out = inside
    if prefix := route.get("prefix"):
        prefix = "/" + str(prefix).strip("/")
        if not (out == prefix or out.startswith(prefix + "/")):
            return None
        out = out[len(prefix) :] or "/"
    if strip := int(route.get("strip_prefix") or 0):
        lead = str(route["path"]).strip("/").split("/")[:strip]
        if len(lead) < strip or any(re.search(r"[*{]", s) for s in lead):
            return None  # the stripped segments aren't literal: which ones isn't known
        out = "/" + "/".join(lead) + ("" if out == "/" else out)
    return out if matches(str(route["path"]), out) else None


def serves(endpoint_path: str, path: str) -> bool:
    """Whether an endpoint's path template (`/owners/{ownerId}`) serves a concrete path."""
    return matches(endpoint_path, path) or endpoint_path == path
