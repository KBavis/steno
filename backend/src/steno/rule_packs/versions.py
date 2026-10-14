"""The libraries a repository declares, with their versions where the build says them.

See docs/how-it-works/ingestion/rules-and-packs.md §6 (`versions`) and the coverage report.

Read from every build file the build loads, not just the main one: Gradle scripts applied from
others (`dependencies.gradle`), version catalogs (`libs.versions.toml`), Gradle properties,
Maven POMs (parent, properties), and Python requirements, pyproject files, and lock files. A
version written as a variable (`${springBootVersion}`, `versions.boot`, `version.ref`) is looked
up in the same files. Nothing is built or downloaded: a version only the build tool would work
out (a BOM's managed versions, a range) is left unknown, and the coverage report says so.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from steno.source.files import is_test_file, source_files


@dataclass(frozen=True)
class Dependency:
    name: str  # `group:artifact` (Java), a plugin id, or a Python distribution name
    version: str | None  # None when the build doesn't say it
    file: str  # repository-relative


GRADLE = (".gradle", ".gradle.kts")
# `'group:artifact:version'`, `"group:artifact"`
# (a `${…}` version may quote inside: `${property('x')}`)
_COORDS = re.compile(r"""['"]([\w.\-]+):([\w.\-]+)(?::(\$\{[^}]*\}|[^'":@]+))?(?:@\w+)?['"]""")
# `group: 'g', name: 'a', version: 'v'` (and `group = "g", name = "a"` in Kotlin)
_NAMED = re.compile(
    r"""group\s*[:=]\s*['"]([\w.\-]+)['"]\s*,\s*name\s*[:=]\s*['"]([\w.\-]+)['"]"""
    r"""(?:\s*,\s*version\s*[:=]\s*['"]([^'"]+)['"])?"""
)
# `id 'org.springframework.boot' version '3.2.2'`, `id("x") version "1.0"`
_PLUGIN = re.compile(
    r"""\bid\s*\(?\s*['"]([\w.\-]+)['"]\s*\)?\s*version\s*\(?\s*['"]([^'"]+)['"]"""
)
# Variables a version can name: `springBootVersion = '2.7.18'`, `ext.x = "1"`, `boot: '3.2.0'`
# (a map entry in `ext.versions = [boot: '3.2.0']`), `set('x', '1')`, `extra["x"] = "1"`
_VARIABLE = re.compile(
    r"""(?:^|[\s,(\[{])(?:def\s+|val\s+|var\s+)?([A-Za-z_][\w.]*)\s*[=:]\s*['"]([^'"$]+)['"]"""
)
_SET = re.compile(r"""(?:set|extra\[)\s*\(?\s*['"]([\w.]+)['"]\s*(?:,|\]\s*=)\s*['"]([^'"]+)['"]""")
# `${springBootVersion}`, `$x`, `${project.x}`, `${property('x')}`, `${findProperty("x")}`
_REFERENCE = re.compile(
    r"""^\$\{?(?:(?:project\.)?(?:findProperty|property)\(\s*['"])?([\w.]+)(?:['"]\s*\))?\}?$"""
)


def dependencies(repo: Path) -> list[Dependency]:
    """Every dependency (and build plugin) the repository's build files declare."""
    # Test code's libraries (a test-only Kafka container, a fixture's imports) aren't the app's
    files = [f for f in source_files(repo) if not is_test_file(f.relative_to(repo))]
    variables = _gradle_variables(repo, files)
    out: list[Dependency] = []
    for f in files:
        rel = f.relative_to(repo).as_posix()
        if f.name.endswith(GRADLE):
            out += _gradle(f.read_text(errors="replace"), rel, variables)
        elif f.name.endswith(".versions.toml"):
            out += _catalog(f, rel)
        elif f.name == "pom.xml":
            out += _maven(f.read_text(errors="replace"), rel)
        elif re.fullmatch(r"requirements.*\.txt", f.name):
            out += _requirements(f.read_text(errors="replace").splitlines(), rel)
        elif f.name == "pyproject.toml":
            out += _pyproject(f, rel)
        elif f.name in ("uv.lock", "poetry.lock"):
            out += _lock(f, rel)
    return out


# ------------------------------------------------------------------- Gradle


def _gradle_variables(repo: Path, files: list[Path]) -> dict[str, str]:
    found: dict[str, str] = {}
    for f in files:
        if f.name.endswith(GRADLE):
            text = f.read_text(errors="replace")
            for pattern in (_VARIABLE, _SET):
                for name, value in pattern.findall(text):
                    found.setdefault(name.removeprefix("project.").removeprefix("ext."), value)
        elif f.name == "gradle.properties":
            for line in f.read_text(errors="replace").splitlines():
                name, sep, value = line.partition("=")
                if sep and not line.lstrip().startswith("#"):
                    found.setdefault(name.strip(), value.strip())
    return found


def _resolve(version: str | None, variables: dict[str, str]) -> str | None:
    """`${versions.boot}` → the value of `versions.boot`, or of `boot` (a map entry)."""
    if not version:
        return None
    ref = _REFERENCE.match(version.strip())
    if not ref:
        return None if "$" in version else version
    name = ref.group(1)
    return variables.get(name) or variables.get(name.rsplit(".", 1)[-1])


def _gradle(text: str, rel: str, variables: dict[str, str]) -> list[Dependency]:
    out = [Dependency(pid, _resolve(v, variables), rel) for pid, v in _PLUGIN.findall(text)]
    for g, a, v in _COORDS.findall(text):
        out.append(Dependency(f"{g}:{a}", _resolve(v, variables), rel))
    for g, a, v in _NAMED.findall(text):
        out.append(Dependency(f"{g}:{a}", _resolve(v, variables), rel))
    return out


def _catalog(f: Path, rel: str) -> list[Dependency]:
    """A Gradle version catalog: `[libraries]` and `[plugins]`, versions from `[versions]`."""
    try:
        data = tomllib.loads(f.read_text())
    except tomllib.TOMLDecodeError:
        return []
    versions = {k: v for k, v in (data.get("versions") or {}).items() if isinstance(v, str)}

    def version(entry: dict) -> str | None:
        v = entry.get("version")
        if isinstance(v, dict):
            return versions.get(v.get("ref", "")) or v.get("strictly") or v.get("require")
        return v if isinstance(v, str) else None

    out = []
    for entry in (data.get("libraries") or {}).values():
        if isinstance(entry, str):
            g, _, rest = entry.partition(":")
            a, _, v = rest.partition(":")
            out.append(Dependency(f"{g}:{a}", v or None, rel))
        elif isinstance(entry, dict):
            module = entry.get("module") or f"{entry.get('group')}:{entry.get('name')}"
            out.append(Dependency(module, version(entry), rel))
    for entry in (data.get("plugins") or {}).values():
        if isinstance(entry, str):
            pid, _, v = entry.partition(":")
            out.append(Dependency(pid, v or None, rel))
        elif isinstance(entry, dict) and entry.get("id"):
            out.append(Dependency(entry["id"], version(entry), rel))
    return out


# -------------------------------------------------------------------- Maven


def _maven(text: str, rel: str) -> list[Dependency]:
    """`<parent>`, `<dependency>`, and `<plugin>` blocks; `${x}` from `<properties>`."""
    props = {}
    for block in re.findall(r"<properties>(.*?)</properties>", text, re.DOTALL):
        props |= dict(re.findall(r"<([\w.\-]+)>\s*([^<$]+?)\s*</\1>", block))

    def tag(block: str, name: str) -> str | None:
        m = re.search(rf"<{name}>\s*([^<]+?)\s*</{name}>", block)
        return m.group(1) if m else None

    out = []
    for kind in ("parent", "dependency", "plugin"):
        for block in re.findall(rf"<{kind}>(.*?)</{kind}>", text, re.DOTALL):
            g = tag(block, "groupId") or ("org.apache.maven.plugins" if kind == "plugin" else None)
            a = tag(block, "artifactId")
            if not g or not a:
                continue
            v = tag(block, "version")
            if v and v.startswith("${"):
                v = props.get(v[2:-1])
                if v and v.startswith("${"):  # `${project.version}` and the like
                    v = None
            out.append(Dependency(f"{g}:{a}", v, rel))
            if kind == "parent" and props:
                props.setdefault("project.parent.version", v or "")
    return out


# ------------------------------------------------------------------- Python


def _requirement(spec: str, rel: str) -> Dependency | None:
    """`fastapi==0.115.12` → 0.115.12. A range (`>=0.100`) isn't a version: unknown."""
    spec = spec.split("#", 1)[0].split(";", 1)[0].strip()
    if not spec or spec.startswith("-"):
        return None
    name = re.split(r"[\s<>=!~\[]", spec, maxsplit=1)[0]
    pinned = re.search(r"===?\s*([\w.\-+]+)", spec)
    return Dependency(name, pinned.group(1) if pinned else None, rel)


def _requirements(lines: list[str], rel: str) -> list[Dependency]:
    return [d for line in lines if (d := _requirement(line, rel))]


def _pyproject(f: Path, rel: str) -> list[Dependency]:
    try:
        data = tomllib.loads(f.read_text())
    except tomllib.TOMLDecodeError:
        return []
    specs = list(data.get("project", {}).get("dependencies", []))
    for group in (data.get("project", {}).get("optional-dependencies") or {}).values():
        specs += group
    out = _requirements(specs, rel)
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies") or {}
    out += [Dependency(n, None, rel) for n in poetry if n != "python"]
    return out


def _lock(f: Path, rel: str) -> list[Dependency]:
    """`uv.lock` and `poetry.lock`: the exact versions installed."""
    try:
        data = tomllib.loads(f.read_text())
    except tomllib.TOMLDecodeError:
        return []
    return [
        Dependency(p["name"], str(p["version"]), rel)
        for p in data.get("package", [])
        if p.get("name") and p.get("version")
    ]


# ------------------------------------------------------------------- ranges


def parse_version(v: str) -> tuple[int, ...]:
    """`3.2.0.RELEASE` → (3, 2, 0); `1.0.0-RC1` → (1, 0, 0): release numbers only."""
    nums = []
    for part in re.split(r"[.]", v.split("-", 1)[0].split("+", 1)[0]):
        m = re.match(r"\d+", part)
        if not m:
            break
        nums.append(int(m.group()))
    return tuple(nums)


def in_range(version: str, spec: str) -> bool:
    """`3.2.2` in `>=2.7, <4`. Each comparison is on release numbers, padded with zeros."""
    have = parse_version(version)
    for op, want in re.findall(r"(>=|<=|==|!=|>|<)?\s*([\w.\-]+)", spec):
        w = parse_version(want)
        width = max(len(have), len(w))
        a, b = have + (0,) * (width - len(have)), w + (0,) * (width - len(w))
        ok = {
            ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b,
            "!=": a != b, "==": a == b, "": a == b,
        }[op]  # fmt: skip
        if not ok:
            return False
    return True


def versions_of(deps: list[Dependency], key: str) -> list[Dependency]:
    """The declared versions of `key`: a `group:artifact`, a distribution name, or a group
    or plugin id (`org.springframework.boot`: the Boot plugin, parent POM, or BOM)."""
    want = _key(key)
    out = []
    for d in deps:
        if d.version is None:
            continue
        name = _key(d.name)
        if name == want or (":" not in want and name.split(":", 1)[0] == want):
            out.append(d)
    return out


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()
