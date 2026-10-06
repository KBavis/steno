# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Run a Steno rule's `match` half against real code and show what it matches.

    uv run check_match.py <rule.yaml> <path> [<path> ...] [--limit N]

Code rules run through ast-grep (via `uvx --from ast-grep-cli`); config rules match key
paths in YAML, .properties, and .env files. This checks *where* a rule matches and what
it captures. It doesn't check `where:` conditions (they need symbol resolution) or the
emitted facts; `steno rules test` does that once the rule engine exists.
"""

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import yaml

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", "target"}


def pack_language(rule_path: Path) -> str | None:
    for parent in rule_path.resolve().parents:
        pack = parent / "pack.yaml"
        if pack.exists():
            return yaml.safe_load(pack.read_text()).get("language")
    return None


# ----------------------------------------------------------------- code rules


def run_code_rule(rule: dict, language: str, paths: list[str]) -> list[dict]:
    match = rule.get("match") or {}
    if "rule" not in match:
        sys.exit("error: a code rule needs match.rule (ast-grep syntax)")
    config = {"id": rule.get("id", "rule"), "language": language, "rule": match["rule"]}
    for key in ("constraints", "utils", "transform"):
        if key in match:
            config[key] = match[key]

    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as tmp:
        yaml.safe_dump(config, tmp)
    cmd = [
        "uvx",
        "--from",
        "ast-grep-cli",
        "ast-grep",
        "scan",
        "-r",
        tmp.name,
        "--json=compact",
        *paths,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    Path(tmp.name).unlink()
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        sys.exit(f"error: ast-grep failed:\n{proc.stderr.strip()}")

    results = []
    for m in json.loads(proc.stdout):
        meta = m.get("metaVariables", {})
        captures = {k: v["text"] for k, v in meta.get("single", {}).items() if k != "_"}
        captures |= {
            k: " ".join(x["text"] for x in v)
            for k, v in meta.get("multi", {}).items()
            if k != "secondary"
        }
        results.append(
            {
                "file": m["file"],
                "line": m["range"]["start"]["line"] + 1,
                "captures": captures,
                "text": m["lines"].strip().splitlines()[0][:100],
            }
        )
    return results


# --------------------------------------------------------------- config rules


def key_regex(pattern: str) -> re.Pattern[str]:
    """app.kafka.topics.{NAME} → regex; {X} captures one segment, * one segment, ** several."""
    out = ""
    for token in re.findall(r"\{\w+\}|\*\*|\*|[^{*]+", pattern):
        if token == "**":
            out += r".+"
        elif token == "*":
            out += r"[^.]+"
        elif token.startswith("{"):
            out += rf"(?P<{token[1:-1]}>[^.]+)"
        else:
            out += re.escape(token)
    return re.compile(rf"^{out}$")


def flatten(data: object, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            flat |= flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(data, list) and all(not isinstance(x, (dict, list)) for x in data):
        flat[prefix] = ",".join(str(x) for x in data)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            flat |= flatten(v, f"{prefix}[{i}]")
    elif data is not None:
        flat[prefix] = str(data)
    return flat


def read_config(path: Path) -> dict[str, str]:
    text = path.read_text(errors="replace")
    if path.suffix in (".yml", ".yaml"):
        flat: dict[str, str] = {}
        for doc in yaml.safe_load_all(text):  # Spring files can hold several documents
            flat |= flatten(doc)
        return flat
    pairs = {}
    for line in text.splitlines():  # .properties and .env
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        if m := re.match(r"^(?:export\s+)?([^=:\s]+)\s*[=:]\s*(.*)$", line):
            pairs[m[1]] = m[2].strip().strip("\"'")
    return pairs


def run_config_rule(rule: dict, paths: list[str]) -> list[dict]:
    globs = rule.get("files") or ["**/*.yml", "**/*.yaml", "**/*.properties", "**/.env*"]
    pattern = (rule.get("match") or {}).get("key")
    if not pattern:
        sys.exit("error: a config rule needs match.key")
    regex = key_regex(pattern)

    results = []
    for root in map(Path, paths):
        files = [root] if root.is_file() else (p for p in root.rglob("*") if p.is_file())
        for f in files:
            if SKIP_DIRS & set(f.parts):
                continue
            rel = f.as_posix()
            if not any(
                fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(f.name, g.split("/")[-1]) for g in globs
            ):
                continue
            try:
                config = read_config(f)
            except Exception as exc:  # unparsable file: report, keep going
                print(f"  skipped {f}: {exc}", file=sys.stderr)
                continue
            for key, value in config.items():
                if m := regex.match(key):
                    results.append(
                        {
                            "file": str(f),
                            "line": None,
                            "captures": {"KEY": key, "VALUE": value, **m.groupdict()},
                            "text": f"{key} = {value}"[:100],
                        }
                    )
    return results


# ------------------------------------------------------------------------ main


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("rule", type=Path)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--limit", type=int, default=25, help="matches to print (default 25)")
    ap.add_argument("--json", action="store_true", help="print all matches as JSON")
    args = ap.parse_args()

    try:
        rule = yaml.safe_load(args.rule.read_text())
    except yaml.YAMLError as exc:
        sys.exit(f"error: {args.rule} is not valid YAML (quote values that contain ': '):\n{exc}")
    kind = rule.get("kind", "code")
    if kind == "config":
        results = run_config_rule(rule, args.paths)
    else:
        language = rule.get("language") or pack_language(args.rule)
        if not language:
            sys.exit("error: no language: set it in pack.yaml or on the rule")
        results = run_code_rule(rule, language, args.paths)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    files = Counter(r["file"] for r in results)
    print(
        f"{rule.get('id', args.rule.stem)} ({kind}): {len(results)} matches in {len(files)} files"
    )
    for r in results[: args.limit]:
        where = f"{r['file']}:{r['line']}" if r["line"] else r["file"]
        caps = "  ".join(f"${k}={v}" for k, v in r["captures"].items())
        print(f"  {where}\n      {r['text']}\n      {caps}")
    if len(results) > args.limit:
        print(f"  … {len(results) - args.limit} more (--limit to show more, --json for all)")
    if rule.get("where"):
        print("note: `where:` conditions aren't checked here; they need symbol resolution.")


if __name__ == "__main__":
    main()
