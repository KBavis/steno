"""Whether a pull request is still open, from the git host that holds it.

The rule author marks a pattern `proposed` with its pull request's URL. Each coverage run asks
the host what became of it: GitHub, Bitbucket Cloud, or Bitbucket Server / Data Center. Anything
that can't be answered (a host that's down, no credentials) is `unknown`, and nothing changes.
"""

import json
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

OPEN, MERGED, DECLINED, UNKNOWN = "open", "merged", "declined", "unknown"
TIMEOUT = 5
URL = re.compile(r"https?://\S+")
GITHUB = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)")
BITBUCKET_CLOUD = re.compile(r"^/([^/]+)/([^/]+)/pull-requests/(\d+)")
# /projects/PROJ/repos/repo/pull-requests/12, with a context path before it on some servers
BITBUCKET_SERVER = re.compile(
    r"^(.*?)/(?:projects|users)/([^/]+)/repos/([^/]+)/pull-requests/(\d+)"
)


def url_in(note: str | None) -> str | None:
    """The pull request a pattern's note links to."""
    found = URL.search(note or "")
    return found.group(0).rstrip(".,)") if found else None


def api_request(url: str) -> tuple[str, Callable[[dict[str, Any]], str]] | None:
    """The host's API address for a pull request, and how to read its state from the answer.
    None for a URL that isn't a pull request on a known host."""
    parts = urlsplit(url)
    host, path = parts.netloc.rsplit("@", 1)[-1], parts.path
    if match := GITHUB.match(path):
        owner, repo, number = match.groups()
        base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        return f"{base}/repos/{owner}/{repo}/pulls/{number}", _github
    if host.endswith("bitbucket.org") and (match := BITBUCKET_CLOUD.match(path)):
        workspace, repo, number = match.groups()
        api = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests/{number}"
        return api, _bitbucket
    if match := BITBUCKET_SERVER.match(path):
        context, project, repo, number = match.groups()
        kind = "users" if "/users/" in path else "projects"
        api = (
            f"{parts.scheme}://{host}{context}/rest/api/1.0/{kind}/{project}"
            f"/repos/{repo}/pull-requests/{number}"
        )
        return api, _bitbucket
    return None


def _github(body: dict[str, Any]) -> str:
    if body.get("merged") or body.get("merged_at"):
        return MERGED
    return DECLINED if body.get("state") == "closed" else OPEN


def _bitbucket(body: dict[str, Any]) -> str:
    return {"OPEN": OPEN, "MERGED": MERGED, "DECLINED": DECLINED, "SUPERSEDED": DECLINED}.get(
        str(body.get("state")).upper(), UNKNOWN
    )


def state_of(url: str, token: str | None = None) -> str:
    """`open`, `merged`, `declined`, or `unknown`. Never raises: a coverage run doesn't depend
    on a git host's API being up."""
    request = api_request(url)
    if request is None:
        return UNKNOWN
    api, read = request
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(api, headers=headers), timeout=TIMEOUT) as response:
            return read(json.load(response))
    except (URLError, OSError, ValueError) as exc:
        log.info("pull request %s: state not read: %s", url, exc)
        return UNKNOWN


__all__ = ["DECLINED", "MERGED", "OPEN", "UNKNOWN", "api_request", "state_of", "url_in"]
