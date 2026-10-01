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


def clone(
    clone_url: str, branch: str, dest: Path, kind: ConnectorKind, credentials_ref: str | None
) -> str:
    """Shallow-clone `branch` into `dest` and return the commit SHA."""
    url = clone_url
    token = resolve_secret(credentials_ref)
    if token and url.startswith("https://"):
        parts = urlsplit(url)
        auth = f"{TOKEN_USER.get(kind, 'oauth2')}:{quote(token, safe='')}@"
        url = urlunsplit(parts._replace(netloc=auth + parts.netloc.split("@")[-1]))

    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", "--branch", branch, "--single-branch", url, str(dest)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    )
    if proc.returncode != 0:
        message = (
            proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "git clone failed"
        )
        raise CloneError(message.replace(token, "***") if token else message)

    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    log.info("cloned %s@%s (%s)", clone_url, branch, sha[:12])
    return sha
