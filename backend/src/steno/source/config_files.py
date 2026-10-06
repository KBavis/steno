"""Read config files as flat key → value maps, and match key paths (rule-packs.md §5).

Config rules don't care whether a setting came from YAML, a .properties file, or a .env file.
Every file is turned into the same shape, a flat map of dotted keys to values, e.g.
`{"app.kafka.topics.orders": "orders-v1"}`, and rules match against those keys.
"""

import re
from pathlib import Path

import yaml

# The files read when a config rule doesn't name its own (** means "in any folder")
DEFAULT_GLOBS = ["**/*.yml", "**/*.yaml", "**/*.properties", "**/.env*"]


def key_regex(pattern: str) -> re.Pattern[str]:
    """Turn a rule's key pattern into a regex that matches flat config keys.

    Rules write patterns in a small wildcard language instead of raw regex:

    - `{NAME}` matches one segment (the text between two dots) and captures it, so the rule
      can use it as `$NAME`
    - `*` matches one segment without capturing it
    - `**` matches one or more segments, dots included
    - anything else must appear exactly as written

    For example, `app.kafka.topics.{NAME}` matches `app.kafka.topics.orders` (NAME = "orders"),
    but not `app.kafka.topics.orders.retries`, because `{NAME}` stops at the next dot.
    """
    out = ""
    # Split the pattern into its pieces: {X} names, ** and * wildcards, and the literal text
    # between them. `app.{NAME}.*` → ["app.", "{NAME}", ".", "*"]
    for token in re.findall(r"\{\w+\}|\*\*|\*|[^{*]+", pattern):
        if token == "**":
            out += r".+"  # any characters, dots included
        elif token == "*":
            out += r"[^.]+"  # any characters except a dot: one segment
        elif token.startswith("{"):
            out += rf"(?P<{token[1:-1]}>[^.]+)"  # one segment, remembered under its name
        else:
            out += re.escape(token)  # literal text; escaped so "." means a dot, not "any char"
    # ^ and $ make the whole key match, not just part of it
    return re.compile(rf"^{out}$")


def read(path: Path) -> dict[str, str]:
    """Read one config file into a flat map of keys to values.

    YAML files are nested, so they're flattened (see `_flatten`). .properties and .env files
    are already flat: one `key=value` (or `key: value`) per line.
    """
    text = path.read_text(errors="replace")
    if path.suffix in (".yml", ".yaml"):
        flat: dict[str, str] = {}
        for doc in yaml.safe_load_all(text):  # Spring files can hold several documents
            flat |= _flatten(doc)
        return flat
    pairs = {}
    for line in text.splitlines():  # .properties and .env
        line = line.strip()
        if not line or line.startswith(("#", "!")):  # blank lines and comments
            continue
        # A setting line: an optional leading `export ` (shell style), then the key, then = or :,
        # then the value. `export DB_HOST = "db"` → key "DB_HOST", value "db"
        if m := re.match(r"^(?:export\s+)?([^=:\s]+)\s*[=:]\s*(.*)$", line):
            pairs[m[1]] = m[2].strip().strip("\"'")  # drop surrounding quotes
    return pairs


def _flatten(data: object, prefix: str = "") -> dict[str, str]:
    """Turn nested YAML into dotted keys, so it matches the same way as a .properties file.

        spring:
          datasource:
            url: jdbc:postgresql://db/orders     →  {"spring.datasource.url": "jdbc:postgresql://db/orders"}
        topics: [orders, payments]               →  {"topics": "orders,payments"}
        servers:
          - host: a                              →  {"servers[0].host": "a"}

    A list of plain values becomes one comma-separated value; a list of objects gets an index
    per item.
    """
    flat: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            flat |= _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(data, list) and all(not isinstance(x, (dict, list)) for x in data):
        flat[prefix] = ",".join(str(x) for x in data)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            flat |= _flatten(v, f"{prefix}[{i}]")
    elif data is not None:
        flat[prefix] = str(data)
    return flat
