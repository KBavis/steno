"""Run a question set against Claude Code with Steno and without it (docs/plan/roadmap.md §4).

    uv run --project backend python eval/run.py eval/questions/budgeting-app.yaml
        [--modes steno,baseline] [--only ba-01,ba-04] [--model claude-opus-5-5]

- **steno**: Claude Code with only Steno's MCP tools (no file tools), from an empty folder: the
  answer has to come from Steno, which reads code from the git host when it needs to.
- **baseline**: Claude Code with the repositories checked out at the commit Steno ingested, and
  Read, Grep, and Glob; no MCP.

Each answer is recorded with its tool calls, turns, cost, and time, in
`eval/results/<target>/<timestamp>.json` and a Markdown report to score by hand: correct (2 all
of `must_mention`, 1 some, 0 none or wrong), and whether it said what it couldn't see. The
report marks which `must_mention` facts appear, as a hint, not a score. Needs the API running
(`make api`) for Steno's MCP server and the repositories' ingested commits.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

EVAL = Path(__file__).resolve().parent
API = "http://localhost:8000"
SYSTEM = (
    "You are answering a question about a software system for an engineer. Answer concisely and "
    "specifically: name the services, endpoints, classes, tables, and files involved, and cite "
    "files where you can. If something can't be determined from what you can see, say so."
)
TOOLS = {
    "steno": ["--tools", "", "--allowedTools", "mcp__steno__*"],
    "baseline": ["--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob"],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions", type=Path)
    ap.add_argument("--modes", default="steno,baseline")
    ap.add_argument("--only", default="")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--max-turns", default="40")
    args = ap.parse_args()

    spec = yaml.safe_load(args.questions.read_text())
    questions = [
        q for q in spec["questions"] if not args.only or q["id"] in args.only.split(",")
    ]
    modes = args.modes.split(",")
    workdir = _checkout(spec) if "baseline" in modes else None
    mcp = EVAL / "mcp-steno.json"
    mcp.write_text(
        json.dumps({"mcpServers": {"steno": {"type": "http", "url": f"{API}/mcp"}}})
    )

    results = []
    for q in questions:
        for mode in modes:
            print(f"{q['id']} [{mode}] {q['question']}", file=sys.stderr)
            if mode == "steno":
                with tempfile.TemporaryDirectory(prefix="steno-eval-") as empty:
                    run = _ask(q["question"], Path(empty), TOOLS[mode] + ["--mcp-config", str(mcp)],
                               args)  # fmt: skip
            else:
                assert workdir is not None
                run = _ask(q["question"], workdir, TOOLS[mode], args)
            run["hits"] = [
                m for m in q.get("must_mention", []) if _mentions(run["answer"], m)
            ]
            results.append({"id": q["id"], "mode": mode, **run})
            print(f"  {run['tool_calls']} tool calls, ${run['cost_usd']:.2f}, "
                  f"{len(run['hits'])}/{len(q.get('must_mention', []))} key facts", file=sys.stderr)  # fmt: skip

    out = EVAL / "results" / spec["target"]
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    record = {
        "target": spec["target"],
        "model": args.model,
        "modes": modes,
        "results": results,
    }
    (out / f"{stamp}.json").write_text(json.dumps(record, indent=1))
    (out / f"{stamp}.md").write_text(_report(spec, questions, results, args.model))
    print(out / f"{stamp}.md")


def _checkout(spec: dict[str, Any]) -> Path:
    """The repositories at the commits Steno ingested, so both modes see the same code."""
    with urllib.request.urlopen(f"{API}/api/repositories") as r:
        ingested = {
            repo["name"]: repo.get("last_ingested_sha") for repo in json.load(r)
        }
    root = EVAL / "workdir" / spec["target"]
    for repo in spec["repositories"]:
        path, sha = root / repo["name"], ingested.get(repo["name"])
        if not (path / ".git").exists():
            path.mkdir(parents=True, exist_ok=True)
            _git(path, "init", "-q")
            _git(path, "remote", "add", "origin", repo["clone_url"])
        ref = sha or "HEAD"
        _git(path, "fetch", "-q", "--depth", "1", "origin", ref)
        _git(path, "checkout", "-q", "--force", "FETCH_HEAD")
    return root


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True)


def _ask(
    question: str, cwd: Path, tools: list[str], args: argparse.Namespace
) -> dict[str, Any]:
    cmd = [
        "claude", "-p", question, "--output-format", "stream-json", "--verbose",
        "--model", args.model, "--max-turns", args.max_turns, "--no-session-persistence",
        "--strict-mcp-config", "--append-system-prompt", SYSTEM, *tools,
    ]  # fmt: skip
    started = time.monotonic()
    done = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=1800, check=False
    )
    calls: list[str] = []
    final: dict[str, Any] = {}
    for line in done.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "assistant":
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    calls.append(block.get("name", "?"))
        elif msg.get("type") == "result":
            final = msg
    return {
        "answer": final.get("result") or done.stderr.strip()[-2000:],
        "error": final.get("is_error", done.returncode != 0),
        "tool_calls": len(calls),
        "tools": calls,
        "turns": final.get("num_turns"),
        "cost_usd": final.get("total_cost_usd") or 0.0,
        "seconds": round(time.monotonic() - started, 1),
    }


def _mentions(answer: str, fact: str) -> bool:
    """A rough hint: every word of the fact appears in the answer."""
    text = answer.lower()
    return all(w in text for w in str(fact).lower().split())


def _report(
    spec: dict[str, Any],
    questions: list[dict[str, Any]],
    results: list[dict[str, Any]],
    model: str,
) -> str:
    by = {(r["id"], r["mode"]): r for r in results}
    modes = sorted({r["mode"] for r in results}, key=lambda m: m != "steno")
    intro = f"Model: `{model}`. Score each answer: **correct** 2 / 1 / 0, and **gaps stated** yes / no."
    lines = [f"# {spec['target']}: question set", "", intro, ""]
    head = (
        "| Question | " + " | ".join(f"{m}: calls, $, key facts" for m in modes) + " |"
    )
    lines += [head, "|---|" + "---|" * len(modes)]
    for q in questions:
        row = [q["id"]]
        for m in modes:
            r = by.get((q["id"], m))
            row.append(f"{r['tool_calls']}, ${r['cost_usd']:.2f}, "
                       f"{len(r['hits'])}/{len(q.get('must_mention', []))}" if r else "-")  # fmt: skip
        lines.append("| " + " | ".join(row) + " |")
    for q in questions:
        lines += ["", f"## {q['id']}: {q['question']}", "", f"**Expected:** {q['expected'].strip()}",
                  "", f"**Key facts:** {', '.join(map(str, q.get('must_mention', [])))}"]  # fmt: skip
        for m in modes:
            r = by.get((q["id"], m))
            if not r:
                continue
            missed = [f for f in q.get("must_mention", []) if f not in r["hits"]]
            title = (
                f"### {m}: {r['tool_calls']} tool calls, {r['turns']} turns, "
                f"${r['cost_usd']:.2f}, {r['seconds']} s"
            )
            lines += [
                "", title, "", f"Key facts missing: {', '.join(map(str, missed)) or 'none'}",
                "", r["answer"].strip(), "", "Score: correct __ / 2 · gaps stated __",
            ]  # fmt: skip
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
