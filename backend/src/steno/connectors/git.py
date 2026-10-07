"""Clone a repository into a temporary workspace (docs/ingestion.md §3, Cloning)."""

import logging
import os
import subprocess
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from steno.db.models import ConnectorKind

log = logging.getLogger(__name__)

# The username each host expects alongside a token in an HTTPS clone URL
TOKEN_USER = {
    ConnectorKind.GITHUB: "x-access-token",
    ConnectorKind.GITLAB: "oauth2",
    ConnectorKind.BITBUCKET: "x-token-auth",
}


class CloneError(RuntimeError):
    pass


def resolve_secret(credentials_ref: str | None) -> str | None:
    """Look up a credentials reference. Only `env:NAME` exists so far; secrets are never stored."""
    if not credentials_ref:
        return None
    scheme, _, name = credentials_ref.partition(":")
    if scheme != "env":
        raise CloneError(f"unsupported credentials reference {credentials_ref!r} (use env:NAME)")
    value = os.environ.get(name)
    if not value:
        raise CloneError(
            f"credentials reference {credentials_ref!r}: environment variable {name} isn't set"
        )
    return value


def _authed(
    clone_url: str, kind: ConnectorKind, credentials_ref: str | None
) -> tuple[str, str | None]:
    """The URL with the token in it for HTTPS hosts, and the token (to redact from errors)."""
    token = resolve_secret(credentials_ref)
    if token and clone_url.startswith("https://"):
        parts = urlsplit(clone_url)
        auth = f"{TOKEN_USER.get(kind, 'oauth2')}:{quote(token, safe='')}@"
        return urlunsplit(parts._replace(netloc=auth + parts.netloc.split("@")[-1])), token
    return clone_url, token


def _git(cmd: list[str], token: str | None, what: str) -> str:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    )
    if proc.returncode != 0:
        message = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"{what} failed"
        raise CloneError(message.replace(token, "***") if token else message)
    return proc.stdout


def remote_head(
    clone_url: str, branch: str, kind: ConnectorKind, credentials_ref: str | None
) -> str | None:
    """The commit a remote branch points at, without cloning (`git ls-remote`)."""
    url, token = _authed(clone_url, kind, credentials_ref)
    out = _git(["git", "ls-remote", url, f"refs/heads/{branch}"], token, "git ls-remote")
    return out.split()[0] if out.strip() else None


def clone(
    clone_url: str, branch: str, dest: Path, kind: ConnectorKind, credentials_ref: str | None
) -> str:
    """Shallow-clone `branch` into `dest` and return the commit SHA."""
    url, token = _authed(clone_url, kind, credentials_ref)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", "--branch", branch, "--single-branch", url, str(dest)]
    _git(cmd, token, "git clone")

    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    log.info("cloned %s@%s (%s)", clone_url, branch, sha[:12])
    return sha
