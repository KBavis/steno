import os
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
    """Files under `root`, skipping dependency and build directories, in a stable order.

    Skipped directories are never entered: a working copy's node_modules or .venv can hold
    tens of thousands of files.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if (suffixes is None or path.suffix in suffixes) and path.is_file():
                yield path


def is_test_file(rel: Path) -> bool:
    """Test code is excluded from flows (`:Test` modules, docs/knowledge-graph.md §3)."""
    name = rel.name
    return (
        bool({"tests", "test"} & set(rel.parts[:-1]))
        or name.startswith("test_")
        or name.endswith(("_test.py", "Test.java", "Tests.java"))
        or name == "conftest.py"
    )
