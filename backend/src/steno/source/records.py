"""Read structured files (YAML, XML) as trees that remember their line numbers, and walk them
by path, for config rules that match records (docs/how-it-works/ingestion/claims-and-reconcile.md
§4).

Platform and declared files are usually lists of records ("topics: each with a name and its
consumers"). A rule's `each: "topics[*]"` walks the tree to every record; its emits read the
record's fields with `$.name`, `$.consumers[*]`, the key it was found under with `$.@key`, and
the whole document with `$doc.metadata.name`. Every value keeps its line, so a claim cites the
line it came from.

Paths are dotted segments; `[*]` after a segment iterates a list's items (or a mapping's
values, remembering each key); `*` is every child structure (in XML, every child element,
whose tag is `@tag`: XL Deploy's `jee.War` has a dot, so it's filtered, not named). `$` alone
is the document itself.
"""

import re
import xml.parsers.expat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

XML_SUFFIXES = (".xml",)
YAML_SUFFIXES = (".yml", ".yaml")
# The files a record rule reads when it doesn't name its own
DEFAULT_GLOBS = ["**/*.yml", "**/*.yaml", "**/*.xml"]


@dataclass
class Node:
    """A value in a structured file: a mapping (dict of Nodes), a list of Nodes, or a scalar."""

    value: Any
    line: int  # 1-based
    key: str | None = None  # the mapping key it was found under, when iterated with [*]
    parent: "Node | None" = field(default=None, repr=False)


def load(path: Path) -> list[Node]:
    """Every document in a YAML file (several when separated by ---), or an XML file's root."""
    text = path.read_text(errors="replace")
    if path.suffix in XML_SUFFIXES:
        return [_xml(text)]
    loader = yaml.SafeLoader(text)
    docs = []
    try:
        while loader.check_node():
            node = loader.get_node()
            if node is not None:
                docs.append(_yaml(node, loader))
    finally:
        loader.dispose()  # type: ignore[no-untyped-call]
    return docs


def plain(node: Node | None) -> Any:
    """The node as plain Python values."""
    if node is None:
        return None
    if isinstance(node.value, dict):
        return {k: plain(v) for k, v in node.value.items()}
    if isinstance(node.value, list):
        return [plain(v) for v in node.value]
    return node.value


def walk(node: Node, path: str) -> list[Node]:
    """The nodes a path reaches: `topics[*]`, `spec.authorization.acls[*]`, `metadata.name`."""
    if path in ("", "$"):
        return [node]
    nodes = [node]
    for segment in path.split("."):
        name, many = (segment[:-3], True) if segment.endswith("[*]") else (segment, False)
        step: list[Node] = []
        for n in nodes:
            if name == "*":  # every child that's a structure (XML: every child element)
                children = _structures(n)
            else:
                child = n if name == "" else _child(n, name)
                children = [child] if child is not None else []
            for child in children:
                step += _items(child) if many else [child]
        nodes = step
    return nodes


def _structures(node: Node) -> list[Node]:
    if not isinstance(node.value, dict):
        return []
    return [v for k, v in node.value.items() if isinstance(v.value, (dict, list)) and k != "@tag"]


def _child(node: Node, name: str) -> Node | None:
    if not isinstance(node.value, dict):
        return None
    if name == "@key":
        return Node(node.key, node.line) if node.key is not None else None
    return node.value.get(name)


def _items(node: Node) -> list[Node]:
    if isinstance(node.value, list):
        return list(node.value)
    if isinstance(node.value, dict) and "@tag" in node.value:  # an XML element found once
        return [node]
    if isinstance(node.value, dict):  # `topics: {payment.settled: {...}}`: each value, with its key
        return [Node(v.value, v.line, key=k, parent=node) for k, v in node.value.items()]
    return [node]


def flat_lines(node: Node, prefix: str = "") -> dict[str, int]:
    """The line of every dotted key, the way config_files flattens the file: nested keys
    joined by dots, a list of objects indexed (`servers[0].host`), a list of plain values one
    key."""
    out: dict[str, int] = {}
    if isinstance(node.value, dict):
        for k, v in node.value.items():
            out |= flat_lines(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node.value, list) and any(
        isinstance(v.value, (dict, list)) for v in node.value
    ):
        for i, v in enumerate(node.value):
            out |= flat_lines(v, f"{prefix}[{i}]")
    elif prefix:
        out[prefix] = node.line
    return out


# ---------------------------------------------------------------- reading


def _yaml(node: yaml.Node, loader: yaml.SafeLoader) -> Node:
    line = node.start_mark.line + 1
    if isinstance(node, yaml.MappingNode):
        out: dict[str, Node] = {}
        for k, v in node.value:
            child = _yaml(v, loader)
            child.key = str(loader.construct_object(k))  # type: ignore[no-untyped-call]
            out[child.key] = child
        return Node(out, line)
    if isinstance(node, yaml.SequenceNode):
        return Node([_yaml(v, loader) for v in node.value], line)
    return Node(loader.construct_object(node), line)  # type: ignore[no-untyped-call]


def _xml(text: str) -> Node:
    """An element is a mapping: its attributes, its child elements by tag (a list when a tag
    repeats), its own text under `text`, and its tag under `@tag`. A leaf with only text is
    that text."""
    parser = xml.parsers.expat.ParserCreate()
    stack: list[tuple[str, dict[str, Any], list[str], int]] = [("", {}, [], 1)]

    def start(tag: str, attrs: dict[str, str]) -> None:
        line = parser.CurrentLineNumber
        fields: dict[str, Any] = {k: Node(v, line, key=k) for k, v in attrs.items()}
        stack.append((_local(tag), fields, [], line))

    def end(tag: str) -> None:
        name, fields, text_parts, line = stack.pop()
        text = "".join(text_parts).strip()
        if not fields and text:
            node = Node(text, line, key=name)
        else:
            if text:
                fields["text"] = Node(text, line, key="text")
            fields["@tag"] = Node(name, line, key="@tag")
            node = Node(fields, line, key=name)
        parent = stack[-1][1]
        if name in parent:
            existing = parent[name]
            if isinstance(existing.value, list) and existing.key == f"{name}[]":
                existing.value.append(node)
            else:
                parent[name] = Node([existing, node], existing.line, key=f"{name}[]")
        else:
            parent[name] = node

    def chars(data: str) -> None:
        stack[-1][2].append(data)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.Parse(text, True)
    root = stack[0][1]
    return next(iter(root.values())) if len(root) == 1 else Node(root, 1)


def _local(tag: str) -> str:
    """`{namespace}tag` and `ns:tag` → `tag`."""
    return re.sub(r"^(\{[^}]*\}|[\w-]+:)", "", tag)
