"""The names an application goes by in its own repository (the CODE rungs of the name ladder,
docs/concepts/identity.md §2): the build artifact name its build declares, and the path-derived
name every deployable has. The runtime self-name (`spring.application.name`) comes from packs
as a CODE `name` claim.

Only names a build file states count: a Gradle subproject's default name is just its folder,
which says no more than the path does.
"""

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from steno.claims.types import SLOTS
from steno.graph.structure import Module, Structure

ROOT_PROJECT = re.compile(r"""rootProject\.name\s*=\s*['"]([^'"]+)['"]""")


@dataclass(frozen=True)
class Name:
    name: str
    slot: str  # a rung of the name ladder (claims.types.SLOTS)
    witness_type: str
    # The witness and citation: {witness, file, line}
    source: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def rank(self) -> int:
        return SLOTS.index(self.slot)


def build_name(repo: Path, module: Module) -> Name | None:
    """The artifact name the module's build file declares, if it declares one."""
    base = repo / module.path if module.path != "." else repo
    rel = "" if module.path == "." else f"{module.path}/"
    for settings in ("settings.gradle", "settings.gradle.kts"):
        path = base / settings
        if path.is_file():
            m = ROOT_PROJECT.search(path.read_text(errors="replace"))
            if m:
                return _name(m[1], f"{rel}{settings}", path, m.start())
    pom = base / "pom.xml"
    if pom.is_file():
        try:
            root = ET.parse(pom).getroot()
        except ET.ParseError:
            root = None
        artifact = next(
            (c.text for c in (root if root is not None else []) if c.tag.endswith("artifactId")),
            None,
        )
        if artifact and "${" not in artifact:
            return _name(artifact.strip(), f"{rel}pom.xml", pom, None, artifact.strip())
    pyproject = base / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(errors="replace"))
        except tomllib.TOMLDecodeError:
            data = {}
        name = data.get("project", {}).get("name") or (
            data.get("tool", {}).get("poetry", {}).get("name")
        )
        if isinstance(name, str) and name:
            return _name(name, f"{rel}pyproject.toml", pyproject, None, name)
    setup_cfg = base / "setup.cfg"
    if setup_cfg.is_file():
        cfg = ConfigParser()
        try:
            cfg.read_string(setup_cfg.read_text(errors="replace"))
            name = cfg.get("metadata", "name", fallback=None)
        except Exception:
            name = None
        if name:
            return _name(name, f"{rel}setup.cfg", setup_cfg, None, name)
    package = base / "package.json"
    if package.is_file():
        try:
            name = json.loads(package.read_text(errors="replace")).get("name")
        except (json.JSONDecodeError, AttributeError):
            name = None
        if isinstance(name, str) and name:
            return _name(name, f"{rel}package.json", package, None, '"name"')
    return None


def path_name(repo_name: str, structure: Structure, module: Module) -> Name:
    """The provisional name every deployable has: the repository, plus the module's folder."""
    return Name(
        structure.application_name(repo_name, module),
        "path",
        "CODE",
        {"witness": "structure", "file": module.path},
    )


def _name(name: str, rel: str, path: Path, offset: int | None, needle: str | None = None) -> Name:
    text = path.read_text(errors="replace")
    if offset is None and needle:
        offset = text.find(needle)
    line = text.count("\n", 0, offset) + 1 if offset is not None and offset >= 0 else None
    return Name(name, "build_artifact", "CODE", {"witness": "build", "file": rel, "line": line})
