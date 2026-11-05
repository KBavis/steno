#!/usr/bin/env python3
"""Clone only what a rule needs into a temporary folder, and get rid of it (D147).

    scratch_clone.py clone <url> [folder ...]   clone (sparse: only these folders), print its path
    scratch_clone.py clean [path]               delete one scratch clone, or all of them

Uses git and the person's own credentials. Anything left behind by a session that ended early
is removed by the next clone, once it's a day old. Standard library only.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_AGE_S = 24 * 3600


def root() -> Path:
    return Path(tempfile.gettempdir()) / "steno-scratch"


def sweep(now: float | None = None) -> list[Path]:
    """Delete scratch clones more than a day old."""
    now = time.time() if now is None else now
    removed = []
    for path in root().glob("*") if root().is_dir() else []:
        if now - path.stat().st_mtime > MAX_AGE_S:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed


def clone(url: str, folders: list[str]) -> Path:
    sweep()
    root().mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "repo"
    target = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=root()))
    command = ["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none"]
    command += ["--sparse", url, str(target)]
    try:
        subprocess.run(command, check=True)
        if folders:
            subprocess.run(["git", "-C", str(target), "sparse-checkout", "set", *folders], check=True)
    except subprocess.CalledProcessError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def clean(path: str | None) -> list[Path]:
    """Delete one scratch clone, or all. Refuses anything outside the scratch folder."""
    if path is None:
        found = list(root().glob("*")) if root().is_dir() else []
    else:
        one = Path(path).resolve()
        if one.parent != root().resolve():
            raise SystemExit(f"{path} isn't a scratch clone (they live in {root()})")
        found = [one]
    for item in found:
        shutil.rmtree(item, ignore_errors=True)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser("clone")
    c.add_argument("url")
    c.add_argument("folders", nargs="*")
    d = commands.add_parser("clean")
    d.add_argument("path", nargs="?")
    args = parser.parse_args()
    if args.command == "clone":
        try:
            print(clone(args.url, args.folders))
        except subprocess.CalledProcessError as exc:
            sys.exit(f"clone failed ({exc.returncode}): check the URL and your git access")
    else:
        print(f"removed {len(clean(args.path))}")


if __name__ == "__main__":
    main()
