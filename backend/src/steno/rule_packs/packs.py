"""Load rule packs from `rule-packs/` and decide which ones apply to a repository.

The format is defined in docs/how-it-works/ingestion/rules-and-packs.md §6; a pack is enabled
when any of its `enabled_when` conditions holds (D49).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from steno.rule_packs.versions import dependencies
from steno.source.files import is_test_file, source_files

# Language → file extensions the pack's code rules run on
EXTENSIONS = {"python": (".py",), "java": (".java",)}


@dataclass
class Rule:
    id: str
    pack: str
    language: str
    kind: str  # code | config
    match: dict[str, Any]
    emit: list[dict[str, Any]]
    where: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)  # config rules only
    path: Path | None = None


@dataclass
class HttpSystem:
    """A service recognized by the hosts it's called at: `api.github.com`, with `*` wildcards.

    See docs/how-it-works/ingestion/rules-and-packs.md, HTTP signatures.
    """

    name: str
    hosts: list[str] = field(default_factory=list)


@dataclass
class Pack:
    name: str
    version: str
    language: str
    source: str
    description: str
    enabled_when: dict[str, Any]
    path: Path
    rules: list[Rule]
    http_systems: list[HttpSystem] = field(default_factory=list)
    # The map-layer ledger (D101): library types, packages, or `Type#method`s that can do I/O
    # (an unexplained call into one is a gap), and ones that never create an entry point or an
    # integration point (never a gap)
    io: list[str] = field(default_factory=list)
    harmless: list[str] = field(default_factory=list)
    # The library versions its rules are written and tested for, as ranges
    # (`{"org.springframework.boot": ">=2.7, <4"}`). A repository on another version, or one
    # whose version isn't known, is a coverage item; the rules still run
    versions: dict[str, str] = field(default_factory=dict)


def load_pack(path: Path) -> Pack:
    meta = yaml.safe_load((path / "pack.yaml").read_text())
    language = meta["language"]
    rules = []
    for rule_file in sorted((path / "rules").glob("*.yaml")):
        raw = yaml.safe_load(rule_file.read_text())
        if raw.get("id") != rule_file.stem:
            raise ValueError(f"{rule_file}: id must match the file name ({rule_file.stem})")
        rules.append(
            Rule(
                id=raw["id"],
                pack=meta["name"],
                language=raw.get("language", language),
                kind=raw.get("kind", "code"),
                match=raw.get("match") or {},
                emit=raw.get("emit") or [],
                where=raw.get("where") or {},
                files=raw.get("files") or [],
                path=rule_file,
            )
        )
    return Pack(
        name=meta["name"],
        version=str(meta["version"]),
        language=language,
        source=meta.get("source", "core"),
        description=meta.get("description", ""),
        enabled_when=meta.get("enabled_when") or {},
        path=path,
        rules=rules,
        http_systems=_http_systems(path / "http.yaml"),
        io=list((meta.get("library") or {}).get("io") or []),
        harmless=list((meta.get("library") or {}).get("harmless") or []),
        versions={str(k): str(v) for k, v in (meta.get("versions") or {}).items()},
    )


def _http_systems(file: Path) -> list[HttpSystem]:
    if not file.exists():
        return []
    raw = yaml.safe_load(file.read_text()) or {}
    return [HttpSystem(s["name"], list(s.get("hosts") or [])) for s in raw.get("systems") or []]


def load_packs(root: Path) -> list[Pack]:
    return [load_pack(p.parent) for p in sorted(root.glob("*/*/pack.yaml"))]


# ------------------------------------------------------------- enabled_when


def is_enabled(pack: Pack, repo: Path) -> bool:
    cond = pack.enabled_when
    if cond.get("always"):
        return _has_language(pack.language, repo)
    deps = {_norm(d) for d in cond.get("dependencies", [])}
    if deps & declared_dependencies(repo):
        return True
    imports = cond.get("imports", [])
    return bool(imports) and _imports_any(pack.language, repo, imports)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_dependencies(repo: Path) -> set[str]:
    """Dependency names the repository's build files declare (rule_packs/versions.py): Gradle
    scripts and catalogs, Maven POMs, Python requirements and pyproject files. Java dependencies
    are `group:artifact`. Lock files are left out: they list what other libraries pull in too."""
    return {_norm(d.name) for d in dependencies(repo) if not d.file.endswith(".lock")}


def _has_language(language: str, repo: Path) -> bool:
    if language == "any":  # e.g. HTTP signatures: they read URLs, not one language's code
        return True
    exts = EXTENSIONS.get(language, ())
    return any(f.suffix in exts for f in source_files(repo))


def _imports_any(language: str, repo: Path, modules: list[str]) -> bool:
    names = "|".join(re.escape(m) for m in modules)
    if language == "python":
        pattern = re.compile(rf"^\s*(?:from|import)\s+(?:{names})(?:[.\s]|$)", re.MULTILINE)
    elif language == "java":
        pattern = re.compile(rf"^\s*import\s+(?:static\s+)?(?:{names})[.;]", re.MULTILINE)
    else:
        return False
    exts = EXTENSIONS.get(language, ())
    return any(
        pattern.search(f.read_text(errors="replace"))
        for f in source_files(repo, exts)
        if not is_test_file(f.relative_to(repo))
    )
