"""How a repository is built: its modules (build units) and the applications they produce.

docs/knowledge-graph.md §3, Module roles. A Module is the ecosystem's build unit (a Python
project, an npm package, a Maven/Gradle module); a `:Service` module produces a deployable
and gets an Application. Phase 1 detection (Proposed; DESIGN_DOC §21 lists the signals as
Open): a module is a Service when the extractors found entry points in it.
"""

from dataclasses import dataclass, field
from pathlib import Path

from steno.extractors.files import SKIP_DIRS

# Build file → (build tool, language)
BUILD_FILES = {
    "pyproject.toml": ("python", "python"),
    "setup.py": ("python", "python"),
    "setup.cfg": ("python", "python"),
    "package.json": ("npm", "typescript"),
    "pom.xml": ("maven", "java"),
    "build.gradle": ("gradle", "java"),
    "build.gradle.kts": ("gradle", "java"),
}


@dataclass
class Module:
    path: str  # relative to the repository root; "." for the root
    build_tool: str
    language: str
    roles: set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return Path(self.path).name if self.path != "." else ""


@dataclass
class Structure:
    modules: list[Module]

    def module_of(self, rel_file: str) -> Module | None:
        """The innermost module containing a file."""
        best = None
        for m in self.modules:
            if m.path == "." or rel_file == m.path or rel_file.startswith(m.path + "/"):
                if best is None or len(m.path) > len(best.path):
                    best = m
        return best

    def application_name(self, repo: str, module: Module) -> str:
        """`my-service` when the repository root is the service; in a monorepo, the
        service's folder is appended: `my-service-backend`."""
        return repo if module.path == "." else f"{repo}-{module.name}"


def detect(repo: Path, entry_files: set[str]) -> Structure:
    """Find build units. `entry_files` are files where the extractors found entry points."""
    modules: dict[str, Module] = {}
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo)
        if not path.is_file() or SKIP_DIRS & set(rel.parts):
            continue
        name = path.name
        if name in BUILD_FILES or (name.startswith("requirements") and name.endswith(".txt")):
            tool, language = BUILD_FILES.get(name, ("pip", "python"))
            key = rel.parent.as_posix()
            modules.setdefault(key, Module(key, tool, language))
    if "." not in modules:  # files outside every build unit belong to the repository itself
        modules["."] = Module(".", "none", "mixed")
    structure = Structure(sorted(modules.values(), key=lambda m: m.path))
    for f in entry_files:
        m = structure.module_of(f)
        if m is not None:
            m.roles.add("Service")
    return structure
