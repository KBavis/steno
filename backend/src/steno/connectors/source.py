"""Read a file at a commit from the git host (D17: no file contents are stored).

Code tools (`view_flow_code`) read the exact lines a flow's trace points to, at the commit
the flow was ingested from, straight from the host's API. A small in-process cache saves
repeated reads within a session; nothing is written to disk or a database.
"""

import logging
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from steno.connectors.git import resolve_secret
from steno.db.models import ConnectorKind

log = logging.getLogger(__name__)
TIMEOUT = 15


class SourceError(RuntimeError):
    pass


def read_file(
    clone_url: str, kind: ConnectorKind, credentials_ref: str | None, commit: str, path: str
) -> str:
    """The file's text at `commit`. Raises SourceError when the host can't be read."""
    token = resolve_secret(credentials_ref) if credentials_ref else None
    return _read(clone_url, kind.value, token, commit, path)


@lru_cache(maxsize=256)
def _read(clone_url: str, kind: str, token: str | None, commit: str, path: str) -> str:
    url, headers = _raw_url(clone_url, kind, token, commit, path)
    try:
        with urlopen(Request(url, headers=headers), timeout=TIMEOUT) as response:
            body: bytes = response.read()
    except HTTPError as exc:
        raise SourceError(f"{path}@{commit[:7]}: the git host answered {exc.code}") from exc
    except URLError as exc:
        raise SourceError(f"{path}@{commit[:7]}: can't reach the git host ({exc.reason})") from exc
    return body.decode("utf-8", errors="replace")


def _raw_url(
    clone_url: str, kind: str, token: str | None, commit: str, path: str
) -> tuple[str, dict[str, str]]:
    """The host's raw-file URL for a repository given by its HTTPS clone URL."""
    parts = urlsplit(clone_url)
    host = parts.netloc.rsplit("@", 1)[-1]
    repo_path = parts.path.removesuffix(".git").strip("/")
    file = quote(path)
    headers: dict[str, str] = {}
    if kind == "github":
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if host == "github.com":
            return f"https://raw.githubusercontent.com/{repo_path}/{commit}/{file}", headers
        # GitHub Enterprise: the contents API returns raw text with this media type
        headers["Accept"] = "application/vnd.github.raw"
        return f"https://{host}/api/v3/repos/{repo_path}/contents/{file}?ref={commit}", headers
    if kind == "gitlab":
        if token:
            headers["PRIVATE-TOKEN"] = token
        project = quote(repo_path, safe="")
        return (
            f"https://{host}/api/v4/projects/{project}/repository/files/{quote(path, safe='')}"
            f"/raw?ref={commit}",
            headers,
        )
    if kind == "bitbucket":
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if host.endswith("bitbucket.org"):  # Bitbucket Cloud
            return (
                f"https://api.bitbucket.org/2.0/repositories/{repo_path}/src/{commit}/{file}",
                headers,
            )
        # Bitbucket Server / Data Center: https://host/scm/PROJECT/repo.git
        segments = repo_path.split("/")
        project, repo = (segments[-2], segments[-1]) if len(segments) >= 2 else ("", repo_path)
        return (
            f"https://{host}/rest/api/1.0/projects/{project}/repos/{repo}/raw/{file}?at={commit}",
            headers,
        )
    raise SourceError(f"reading files from {kind} isn't supported")


def slice_lines(text: str, start: int, end: int) -> str:
    """Lines `start`..`end` (1-based, inclusive)."""
    lines = text.splitlines()
    return "\n".join(lines[max(0, start - 1) : end])
