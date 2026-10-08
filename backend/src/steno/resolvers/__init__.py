"""Symbol resolvers, one per language, behind one interface (D70; base.py)."""

from pathlib import Path

from steno.resolvers.base import Resolver, Resolvers

LANGUAGES = ("python", "java")


def build_resolvers(root: Path, files: list[Path], languages: set[str]) -> Resolvers:
    """A resolver for each language the enabled rule packs read."""
    resolvers: list[Resolver] = []
    if "python" in languages:
        from steno.resolvers.python import PythonResolver

        resolvers.append(PythonResolver(root, files))
    if "java" in languages:
        from steno.resolvers.java import JavaResolver

        resolvers.append(JavaResolver(root, files))
    return Resolvers(root, resolvers)
