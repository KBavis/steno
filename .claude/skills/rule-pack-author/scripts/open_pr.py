#!/usr/bin/env python3
"""Open a pull request on the host a checkout's `origin` points at (D147, D148).

    open_pr.py --title T --body-file F --head BRANCH --base main [--dir PATH] [--dry-run]

GitHub (through `gh`, naming the repository, since a fork's default is its upstream), Bitbucket
Cloud, or Bitbucket Server / Data Center (their REST APIs, with BITBUCKET_TOKEN). The branch must
already be pushed. Prints the pull request's URL. Standard library only.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Remote:
    kind: str  # github | bitbucket_cloud | bitbucket_server
    host: str
    owner: str  # GitHub owner, Bitbucket workspace, or Server project
    repo: str
    scheme: str = "https"
    context: str = ""  # a Server's context path: https://acme.com/bitbucket/scm/...


def parse_remote(url: str) -> Remote:
    """`git@bitbucket.org:acme/rules.git`, `ssh://git@b.acme.com:7999/arch/rules.git`,
    `https://b.acme.com/scm/arch/rules.git`, `https://github.com/acme/rules.git`."""
    text = url.strip()
    if m := re.match(r"^[\w.-]+@([\w.-]+):(.+)$", text):  # scp form
        host, path, scheme = m.group(1), m.group(2), "https"
    elif m := re.match(r"^(ssh|https?)://(?:[^@/]+@)?([\w.-]+)(?::\d+)?/(.+)$", text):
        scheme = "https" if m.group(1) == "ssh" else m.group(1)
        host, path = m.group(2), m.group(3)
    else:
        raise SystemExit(f"can't read the remote {url!r}")
    parts = path.removesuffix(".git").strip("/").split("/")
    if len(parts) < 2:
        raise SystemExit(f"can't read the repository in {url!r}")
    owner, repo = parts[-2], parts[-1]
    if "github" in host:
        return Remote("github", host, owner, repo, scheme)
    if host.endswith("bitbucket.org"):
        return Remote("bitbucket_cloud", host, owner, repo, scheme)
    if "scm" in parts or "bitbucket" in host or text.startswith("ssh://"):
        context = "/" + "/".join(parts[: parts.index("scm")]) if "scm" in parts[1:] else ""
        return Remote("bitbucket_server", host, owner, repo, scheme, context)
    raise SystemExit(f"{host} isn't a host this script knows (GitHub or Bitbucket)")


def api_call(r: Remote, title: str, body: str, head: str, base: str) -> tuple[str, dict]:
    """The Bitbucket API address and payload for a new pull request."""
    if r.kind == "bitbucket_cloud":
        return (
            f"https://api.bitbucket.org/2.0/repositories/{r.owner}/{r.repo}/pullrequests",
            {"title": title, "description": body, "source": {"branch": {"name": head}},
             "destination": {"branch": {"name": base}}},
        )  # fmt: skip
    return (
        f"{r.scheme}://{r.host}{r.context}/rest/api/1.0/projects/{r.owner}/repos/{r.repo}"
        "/pull-requests",
        {"title": title, "description": body, "fromRef": {"id": f"refs/heads/{head}"},
         "toRef": {"id": f"refs/heads/{base}"}},
    )  # fmt: skip


def url_of(answer: dict) -> str:
    links = answer.get("links") or {}
    if isinstance(links.get("html"), dict):  # Cloud
        return links["html"]["href"]
    return links["self"][0]["href"]  # Server


def open_bitbucket(r: Remote, title: str, body: str, head: str, base: str) -> str:
    token = os.environ.get("BITBUCKET_TOKEN")
    if not token:
        sys.exit("set BITBUCKET_TOKEN to a token that may open pull requests on this repository")
    api, payload = api_call(r, title, body, head, base)
    request = urllib.request.Request(
        api, json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json"},
    )  # fmt: skip
    with urllib.request.urlopen(request, timeout=30) as response:
        return url_of(json.load(response))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--dir", default=".", help="A checkout of the destination repository")
    parser.add_argument("--dry-run", action="store_true", help="Say where it would go, and stop")
    args = parser.parse_args()
    origin = subprocess.run(
        ["git", "-C", args.dir, "remote", "get-url", "origin"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    r = parse_remote(origin)
    if args.dry_run:
        print(f"{r.kind}: {r.host} {r.owner}/{r.repo}, {args.head} into {args.base}")
        return
    body = Path(args.body_file).read_text()
    if r.kind == "github":
        subprocess.run(
            ["gh", "pr", "create", "--repo", f"{r.owner}/{r.repo}", "--base", args.base,
             "--head", args.head, "--title", args.title, "--body-file", args.body_file],
            check=True,
        )  # fmt: skip
    else:
        print(open_bitbucket(r, args.title, body, args.head, args.base))


if __name__ == "__main__":
    main()
