"""The authoring kit (D124): the deterministic tools a rule drafter uses.

Plain functions, internal to Steno (D129): the `state_machine` drafter calls them directly, the
Claude Agent SDK gets them as in-process tools, and a local command can serve them to the
`rule-pack-author` skill. None of them is on the public MCP server. None of them calls an LLM.

docs/how-it-works/ingestion/rule-curation.md §5.
"""

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from steno.claims.types import CLAIM_TYPES
from steno.extraction.engine import CLUE_FIELDS
from steno.extraction.facts import Origin
from steno.graph.schema import ARCHITECTURE_LABELS, CODE_LABELS
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import Pack, is_enabled, load_all_packs, load_pack
from steno.rule_packs.testing import CaseResult, run_pack_tests

# What a rule may emit (rules-and-packs.md §3, §11; D103)
EMITS = {
    "node": "a thing: an endpoint, a topic, a table, an entity",
    "edge": "a connection between two things, from the relationships below",
    "clue": "a partial fact an assembler combines with others (types below)",
    "entry_point": "this function is started by this trigger",
    "interceptor": "code a framework runs around entry points (filter, validation, …)",
    "claim": "a statement another witness makes (types below), compared with the code",
}
# The node types a rule emits, with the properties that make up each one's identity
# (rules-and-packs.md §3, "Identity: what each node needs")
NODE_IDENTITY = {
    "HttpEndpoint": ["method", "path"],
    "GrpcMethod": ["service", "method"],
    "KafkaTopic": ["name"],
    "Queue": ["name"],
    "Schedule": ["kind", "expression"],
    "Entity": ["name"],
    "Table": ["name"],
    "DataStore": ["vendor", "host", "database"],
    "ExternalSystem": ["host"],
    "Function": ["symbol"],
}
# Relationships a rule may emit (graph-reference.md §4: the extracted ones)
RELATIONSHIPS = {
    "EXPOSES": ("Application", "Interface", []),
    "CALLS": ("Function", "Interface | ExternalSystem", ["transport"]),
    "PRODUCES": ("Function", "KafkaTopic | Queue | org-defined Interface", []),
    "CONSUMES": ("Function", "KafkaTopic | Queue | org-defined Interface", ["consumer_group"]),
    "READS_FROM": ("Function", "Table | DataStore", ["operation"]),
    "WRITES_TO": ("Function", "Table | DataStore", ["operation"]),
    "STARTS": ("Interface | Schedule", "Flow (through entry_point)", []),
    "MAPS_TO": ("Entity", "Table", []),
    "INVOKES": ("Function", "Function", ["async"]),
}
ANCHORS = {
    "@function": "the function the match is in",
    "@class": "the class the match is in",
    "@file": "the file",
    "@app": "the application being ingested",
}


def vocabulary() -> dict[str, Any]:
    """Everything a rule may say, read from the code, so it's always current."""
    return {
        "emits": EMITS,
        "labels": {"architecture": list(ARCHITECTURE_LABELS), "code": list(CODE_LABELS)},
        "node_identity": NODE_IDENTITY,
        "relationships": {
            k: {"from": f, "to": t, "properties": p} for k, (f, t, p) in RELATIONSHIPS.items()
        },
        "clues": {k: sorted(v) for k, v in CLUE_FIELDS.items()},
        "claims": {
            k: {"facet": t.facet, "required": list(t.required), "optional": list(t.optional)}
            for k, t in CLAIM_TYPES.items()
        },
        "anchors": ANCHORS,
        "format": "docs/how-it-works/ingestion/rules-and-packs.md",
    }


# ---------------------------------------------------------------- existing rules


@dataclass
class RuleSummary:
    pack: str
    rule: str
    language: str
    kind: str  # code | config
    description: str
    # Names the rule looks for: annotations, types, methods, config keys (from its match
    # and where), so "is there already a rule for @AcmeHandler?" is a lookup
    mentions: list[str] = field(default_factory=list)


_NAME = re.compile(r"@?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


def rules_index(packs: list[Pack] | None = None) -> list[RuleSummary]:
    """Every loaded rule, with the names it mentions."""
    out = []
    for pack in packs if packs is not None else load_all_packs():
        for rule in pack.rules:
            words: set[str] = set()
            for text in _strings({"match": rule.match, "where": rule.where}):
                for w in _NAME.findall(text):
                    if w.isupper():  # an ast-grep variable ($TOPIC), not a name
                        continue
                    if (len(w) > 2 and w[0].isupper()) or "." in w or w.startswith("@"):
                        words.add(w)
            out.append(
                RuleSummary(
                    pack.name,
                    rule.id,
                    rule.language,
                    rule.kind,
                    _description(rule.path),
                    sorted(words),
                )  # fmt: skip
            )
    return out


def _description(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return str((yaml.safe_load(path.read_text()) or {}).get("description") or "")


def find_rules(query: str, packs: list[Pack] | None = None) -> list[RuleSummary]:
    """Rules whose id, description, or mentioned names contain `query` (any case)."""
    q = query.lower().lstrip("@")
    return [
        r
        for r in rules_index(packs)
        if q in r.rule.lower()
        or q in r.description.lower()
        or any(q in m.lower().lstrip("@") for m in r.mentions)
    ]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (_strings(k) + _strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


# ---------------------------------------------------------------- trying code


def match_existing(files: dict[str, str], packs: list[Pack] | None = None) -> list[dict[str, Any]]:
    """Which existing rules match these sample files, enabled for them or not. A match means
    the fix may be "enable the pack" or "widen its version range", not a new rule (D124).
    `files`: relative path → text, written to a scratch folder like a small repository."""
    every = packs if packs is not None else load_all_packs()
    with tempfile.TemporaryDirectory(prefix="steno-match-") as tmp:
        root = Path(tmp)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        x = run_folder(root, every).out
        enabled = {p.name for p in every if is_enabled(p, root)}
    seen: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    origins: list[Origin] = [
        *(n.origin for n in x.nodes),
        *(e.origin for e in x.edges),
        *(c.origin for c in x.clues),
        *(e.origin for e in x.entry_points),
        *(i.origin for i in x.interceptors),
        *(c.origin for c in x.claims),
        *(o for o, _ in x.dropped),
    ]
    for o in origins:
        if o.rule.startswith("<"):  # built-in, not a rule (`<config>`)
            continue
        seen.setdefault(
            (o.pack, o.rule, o.file, o.line),
            {"pack": o.pack, "rule": o.rule, "file": o.file, "line": o.line,
             "enabled_for_these_files": o.pack in enabled},
        )  # fmt: skip
    return sorted(seen.values(), key=lambda m: (m["pack"], m["rule"], m["file"], m["line"]))


def test_rule(
    rule: str | dict[str, Any],
    cases: dict[str, dict[str, Any]],
    pack: dict[str, Any] | None = None,
    packs: list[Pack] | None = None,
) -> list[CaseResult]:
    """Run a drafted rule against its test cases, the way `steno rules test` does. `rule`:
    the rule's YAML (or its parsed form). `cases`: case name → {"files": {path: text},
    "expected": {nodes, edges, entry_points, clues, claims}}. `pack`: the draft pack's
    `pack.yaml` fields (language at least); the other loaded packs run beside it, so the
    clues they emit are available, as in a real run."""
    parsed = yaml.safe_load(rule) if isinstance(rule, str) else rule
    meta = {"name": "steno-draft", "version": "0.0.0", "language": "java", "source": "org",
            "enabled_when": {"always": True}, **(pack or {})}  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="steno-draft-") as tmp:
        root = Path(tmp) / meta["name"]
        (root / "rules").mkdir(parents=True)
        (root / "pack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
        rule_file = root / "rules" / f"{parsed['id']}.yaml"
        rule_file.write_text(yaml.safe_dump(parsed, sort_keys=False))
        for name, case in cases.items():
            case_dir = root / "tests" / parsed["id"] / name
            for rel, text in (case.get("files") or {}).items():
                path = case_dir / "input" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (case_dir / "input").mkdir(parents=True, exist_ok=True)
            (case_dir / "expected.yaml").write_text(
                yaml.safe_dump(case.get("expected") or {}, sort_keys=False)
            )
        draft = load_pack(root)
        others = [p for p in (packs if packs is not None else load_all_packs())
                  if p.name != draft.name]  # fmt: skip
        return run_pack_tests(draft, [*others, draft])
