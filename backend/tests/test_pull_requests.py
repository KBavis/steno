"""What became of a pull request, on GitHub and both kinds of Bitbucket."""

import json
from urllib.error import URLError

import pytest

from steno.connectors import pull_requests as pr


@pytest.mark.parametrize(
    ("url", "api"),
    [
        ("https://github.com/acme/rules/pull/4", "https://api.github.com/repos/acme/rules/pulls/4"),
        ("https://git.acme.com/acme/rules/pull/4", "https://git.acme.com/api/v3/repos/acme/rules/pulls/4"),
        ("https://bitbucket.org/acme/rules/pull-requests/4",
         "https://api.bitbucket.org/2.0/repositories/acme/rules/pullrequests/4"),
        ("https://bitbucket.acme.com/projects/ARCH/repos/rules/pull-requests/4/overview",
         "https://bitbucket.acme.com/rest/api/1.0/projects/ARCH/repos/rules/pull-requests/4"),
        ("https://acme.com/bitbucket/projects/ARCH/repos/rules/pull-requests/4",
         "https://acme.com/bitbucket/rest/api/1.0/projects/ARCH/repos/rules/pull-requests/4"),
    ],
)  # fmt: skip
def test_each_host_has_its_api(url, api):
    request = pr.api_request(url)
    assert request is not None and request[0] == api


def test_a_url_that_isnt_a_pull_request_is_unknown():
    assert pr.api_request("https://example.com/whatever") is None
    assert pr.state_of("https://example.com/whatever") == pr.UNKNOWN


@pytest.mark.parametrize(
    ("url", "body", "state"),
    [
        ("https://github.com/a/b/pull/1", {"state": "open"}, pr.OPEN),
        ("https://github.com/a/b/pull/1", {"state": "closed", "merged": True}, pr.MERGED),
        ("https://github.com/a/b/pull/1", {"state": "closed", "merged": False}, pr.DECLINED),
        ("https://bitbucket.org/a/b/pull-requests/1", {"state": "DECLINED"}, pr.DECLINED),
        ("https://bitbucket.org/a/b/pull-requests/1", {"state": "SUPERSEDED"}, pr.DECLINED),
        ("https://b.acme.com/projects/P/repos/r/pull-requests/1", {"state": "MERGED"}, pr.MERGED),
        ("https://b.acme.com/projects/P/repos/r/pull-requests/1", {"state": "OPEN"}, pr.OPEN),
    ],
)
def test_the_answer_becomes_a_state(monkeypatch, url, body, state):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(body).encode()

    monkeypatch.setattr(pr, "urlopen", lambda *a, **k: Response())
    assert pr.state_of(url) == state


def test_a_host_that_cant_answer_is_unknown(monkeypatch):
    def fail(*a, **k):
        raise URLError("down")

    monkeypatch.setattr(pr, "urlopen", fail)
    assert pr.state_of("https://github.com/a/b/pull/1") == pr.UNKNOWN


def test_the_note_gives_the_url():
    assert (
        pr.url_in("rule for x: https://github.com/a/b/pull/1.") == "https://github.com/a/b/pull/1"
    )
    assert pr.url_in("no link") is None
