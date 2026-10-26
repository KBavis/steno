"""Publishing approved rules (D125, D129, D131).

Approved rules go to the repository their pack lives in: rules for public libraries to Steno's
own `rule-packs/` (D129), rules for an organization's frameworks to its rule-pack repository.
Each destination either collects them on one branch, `steno/rules`, with **one open pull
request** updated as more are approved (the default), or pushes straight to its default
branch (the UI review is then the only review).

Publishing never touches the working checkout anyone is using: Steno keeps its own git
worktree per destination, under the cache folder. A rule takes effect where it's merged: once
the pull request merges (or right away when pushing), the next ingestion that loads that
branch's packs runs it.
"""

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from steno.config import get_settings

log = logging.getLogger(__name__)

BRANCH = "steno/rules"
AUTHOR = "Steno <steno@localhost>"


class PublishError(RuntimeError):
    pass


@dataclass
class Destination:
    name: str  # core | org
    repo: Path  # the checkout that holds the packs (its git top level)
    mode: str  # pr | push
    base: str  # the default branch


@dataclass
class Published:
    destination: str
    mode: str
    branch: str
    commit: str
    pr_url: str | None  # the open pull request, when `gh` could open or find it
    compare_url: str | None  # otherwise, a link that opens one pre-filled


def destination(source: str, pack_dir: Path) -> Destination:
    """Where a pack in `pack_dir` is published: the git repository that holds it."""
    settings = get_settings()
    existing = pack_dir  # a new pack's folder, and maybe its parents, don't exist yet
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    repo = Path(_git(existing, "rev-parse", "--show-toplevel"))
    mode = settings.rules_publish_core if source == "core" else settings.rules_publish_org
    if mode not in ("pr", "push"):
        raise PublishError(f"publish mode is pr or push, not {mode!r}")
    return Destination(source, repo, mode, _default_branch(repo))


def worktree(dest: Destination) -> Path:
    """Steno's own checkout of the destination, on the branch it publishes from: up to date
    with the remote's default branch, plus whatever Steno already published there."""
    path = get_settings().cache_dir / "publish" / f"{dest.repo.name}-{dest.name}"
    _git(dest.repo, "fetch", "--quiet", "origin")
    if not (path / ".git").exists():
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(dest.repo, "worktree", "prune")
        _git(dest.repo, "worktree", "add", "--force", "--detach", str(path), f"origin/{dest.base}")
    if dest.mode == "pr":
        remote_branch = _git(dest.repo, "ls-remote", "--heads", "origin", BRANCH)
        start = f"origin/{BRANCH}" if remote_branch else f"origin/{dest.base}"
        if remote_branch:
            _git(path, "fetch", "--quiet", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
        _git(path, "checkout", "--quiet", "-B", BRANCH, start)
    else:
        _git(path, "checkout", "--quiet", "--detach", f"origin/{dest.base}")
    _git(path, "reset", "--quiet", "--hard")
    _git(path, "clean", "-fdq")
    return path


def publish(dest: Destination, path: Path, files: list[Path], title: str, body: str) -> Published:
    """Commit the files written in the worktree as Steno, and push: to the `steno/rules`
    branch with its pull request, or to the default branch."""
    for f in files:
        _git(path, "add", "--", str(f))
    _git(path, "-c", "user.name=Steno", "-c", "user.email=steno@localhost", "commit", "--quiet",
         "--author", AUTHOR, "-m", f"{title}\n\n{body}")  # fmt: skip
    commit = _git(path, "rev-parse", "HEAD")
    if dest.mode == "push":
        _git(path, "push", "--quiet", "origin", f"HEAD:{dest.base}")
        return Published(dest.name, "push", dest.base, commit, None, None)
    _git(path, "push", "--quiet", "origin", f"{BRANCH}:{BRANCH}")
    pr_url = _pull_request(path, dest.base, title, body)
    return Published(
        dest.name, "pr", BRANCH, commit, pr_url, None if pr_url else _compare_url(path, dest.base)
    )


def _pull_request(path: Path, base: str, title: str, body: str) -> str | None:
    """The open pull request for `steno/rules`, opened if there's none; None without `gh`."""
    if shutil.which("gh") is None:
        return None
    try:
        found = _run(["gh", "pr", "list", "--head", BRANCH, "--state", "open", "--json", "url",
                      "--jq", ".[0].url"], path)  # fmt: skip
        if found:
            _run(["gh", "pr", "comment", found, "--body", f"Approved: {title}\n\n{body}"], path)
            return found
        intro = (
            "Rules Steno drafted and a person approved in Steno. Each is proven by its own "
            "test cases (`make test`)."
        )
        cmd = ["gh", "pr", "create", "--base", base, "--head", BRANCH,
               "--title", "Steno: approved rules", "--body", f"{intro}\n\n- {title}"]  # fmt: skip
        return _run(cmd, path)
    except PublishError as exc:
        log.warning("publish: couldn't open the pull request with gh: %s", exc)
        return None


def _compare_url(path: Path, base: str) -> str | None:
    """A link that opens a pre-filled pull request on GitHub, Bitbucket, or GitLab."""
    url = _git(path, "remote", "get-url", "origin")
    m = re.match(r"(?:git@|https://)([^/:]+)[/:](.+?)(?:\.git)?$", url)
    if not m:
        return None
    host, repo = m.groups()
    if "github" in host:
        return f"https://{host}/{repo}/compare/{base}...{BRANCH}?expand=1"
    if "gitlab" in host:
        return f"https://{host}/{repo}/-/merge_requests/new?merge_request[source_branch]={BRANCH}"
    if "bitbucket" in host:
        return f"https://{host}/{repo}/pull-requests/new?source={BRANCH}"
    return None


def _default_branch(repo: Path) -> str:
    try:
        ref = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        return ref.removeprefix("origin/")
    except PublishError:
        head = _git(repo, "ls-remote", "--symref", "origin", "HEAD").split("\n")[0]
        m = re.match(r"ref: refs/heads/(\S+)", head)
        return m.group(1) if m else "main"


def _git(cwd: Path, *args: str) -> str:
    return _run(["git", "-C", str(cwd), *args], cwd)


def _run(cmd: list[str], cwd: Path) -> str:
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-2:]
        raise PublishError(f"{' '.join(cmd[:4])}: {' | '.join(tail)}")
    return done.stdout.strip()
