"""`steno rules test`: run each rule's test cases and compare with expected.yaml.

Conventions (docs/extractor-rules.md §7): a case lists only the output of the rule it's filed
under; every pack enabled for the case's input runs, so clues from other rules are available.
A section the case omits isn't checked; a listed one (even `[]`) must match exactly.
An expected item matches an actual one when every property it lists is equal (extra actual
properties are fine), and each side must be fully matched.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from steno.extractors.engine import extract
from steno.extractors.facts import AppRef, Extraction, HttpRef, NodeFact, NodeRef
from steno.extractors.packs import Pack, is_enabled

SECTIONS = ("nodes", "edges", "clues", "entry_points")


@dataclass
class CaseResult:
    pack: str
    rule: str
    case: str
    missing: dict[str, list[Any]] = field(default_factory=dict)
    unexpected: dict[str, list[Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (any(self.missing.values()) or any(self.unexpected.values()) or self.errors)


def run_pack_tests(pack: Pack, all_packs: list[Pack]) -> list[CaseResult]:
    results = []
    for rule_dir in sorted((pack.path / "tests").glob("*/")):
        for case_dir in sorted(p for p in rule_dir.iterdir() if (p / "expected.yaml").exists()):
            results.append(run_case(pack, rule_dir.name, case_dir, all_packs))
    return results


def run_case(pack: Pack, rule_id: str, case_dir: Path, all_packs: list[Pack]) -> CaseResult:
    result = CaseResult(pack.name, rule_id, case_dir.name)
    if rule_id not in {r.id for r in pack.rules}:
        result.errors.append(f"no rule {rule_id!r} in {pack.name}")
        return result
    repo = case_dir / "input"
    packs = [p for p in all_packs if p.name == pack.name or is_enabled(p, repo)]
    extraction = extract(repo, packs)
    result.errors += extraction.errors
    actual = facts_of_rule(extraction, rule_id)
    expected = yaml.safe_load((case_dir / "expected.yaml").read_text()) or {}
    for section in SECTIONS:
        if section not in expected:  # an omitted section isn't checked; `edges: []` asserts none
            continue
        missing, unexpected = _compare(expected.get(section) or [], actual[section])
        result.missing[section], result.unexpected[section] = missing, unexpected
    return result


def facts_of_rule(x: Extraction, rule_id: str) -> dict[str, list[Any]]:
    return {
        "nodes": [node_repr(n) for n in x.nodes if n.origin.rule == rule_id],
        "edges": [edge_repr(e) for e in x.edges if e.origin.rule == rule_id],
        "clues": [
            {c.kind: {k: _field(v) for k, v in c.fields.items()}}
            for c in x.clues
            if c.origin.rule == rule_id
        ],
        "entry_points": [
            {"trigger": ref_repr(ep.trigger), "function": ep.function}
            for ep in x.entry_points
            if ep.origin.rule == rule_id
        ],
    }


# ------------------------------------------------------------ representations


def node_repr(n: NodeFact) -> dict[str, Any]:
    return {n.label: {**n.props, "labels": n.labels}}


def edge_repr(e: Any) -> dict[str, Any]:
    return {e.type: {"from": ref_repr(e.src), "to": ref_repr(e.dst), **e.props}}


def ref_repr(ref: Any) -> Any:
    """A reference as written in expected.yaml: a symbol, "POST /path", a table, or {Label: …}."""
    if isinstance(ref, NodeFact):
        ref = ref.ref()
    if isinstance(ref, AppRef):
        return "@app"
    if isinstance(ref, HttpRef):
        return {"http": {"method": ref.method, "url": ref.url}}
    if isinstance(ref, NodeRef):
        props = ref.as_dict
        if ref.label == "Function":
            return props.get("symbol")
        if ref.label == "HttpEndpoint":
            return f"{props.get('method')} {props.get('path')}"
        if ref.label in ("Table", "Entity") and set(props) == {"name"}:
            return props["name"]
        return {ref.label: props}
    return repr(ref)


def _field(value: Any) -> Any:
    return ref_repr(value) if isinstance(value, (NodeFact, NodeRef, AppRef, HttpRef)) else value


# ----------------------------------------------------------------- comparison


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _matches(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(map(_matches, expected, actual))
        )
    return expected == actual


def _compare(expected: list[Any], actual: list[Any]) -> tuple[list[Any], list[Any]]:
    remaining = list(actual)
    missing = []
    for item in expected:
        hit = next((a for a in remaining if _matches(item, a)), None)
        if hit is None:
            missing.append(item)
        else:
            remaining.remove(hit)
    return missing, remaining
