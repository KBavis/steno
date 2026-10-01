from collections.abc import Iterator
from pathlib import Path

# Never part of the application's own code
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    "target",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "site-packages",
}


def source_files(root: Path, suffixes: tuple[str, ...] | None = None) -> Iterator[Path]:
    """Files under `root`, skipping dependency and build directories, in a stable order."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        if suffixes is None or path.suffix in suffixes:
            yield path
