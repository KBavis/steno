"""Read config files as flat key → value maps, and match key paths (extractor-rules.md §5)."""

import re
from pathlib import Path

import yaml

DEFAULT_GLOBS = ["**/*.yml", "**/*.yaml", "**/*.properties", "**/.env*"]


def key_regex(pattern: str) -> re.Pattern[str]:
    """`app.kafka.topics.{NAME}` → a regex; {X} captures one segment, * one segment, ** several."""
    out = ""
    for token in re.findall(r"\{\w+\}|\*\*|\*|[^{*]+", pattern):
        if token == "**":
            out += r".+"
        elif token == "*":
            out += r"[^.]+"
        elif token.startswith("{"):
            out += rf"(?P<{token[1:-1]}>[^.]+)"
        else:
            out += re.escape(token)
    return re.compile(rf"^{out}$")


def read(path: Path) -> dict[str, str]:
    text = path.read_text(errors="replace")
    if path.suffix in (".yml", ".yaml"):
        flat: dict[str, str] = {}
        for doc in yaml.safe_load_all(text):  # Spring files can hold several documents
            flat |= _flatten(doc)
        return flat
    pairs = {}
    for line in text.splitlines():  # .properties and .env
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        if m := re.match(r"^(?:export\s+)?([^=:\s]+)\s*[=:]\s*(.*)$", line):
            pairs[m[1]] = m[2].strip().strip("\"'")
    return pairs


def _flatten(data: object, prefix: str = "") -> dict[str, str]:
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
