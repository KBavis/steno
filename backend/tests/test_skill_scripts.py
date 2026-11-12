"""The rule-pack-author skill's helper scripts: where a pull request goes, on any host, and
scratch clones that get deleted."""

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude/skills/rule-pack-author/scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


open_pr = _load("open_pr")
scratch = _load("scratch_clone")


@pytest.mark.parametrize(
    ("url", "kind", "host", "owner", "repo"),
    [
        ("git@github.com:KBavis/steno.git", "github", "github.com", "KBavis", "steno"),
        ("https://github.com/acme/steno.git", "github", "github.com", "acme", "steno"),
        ("git@bitbucket.org:acme/rules.git", "bitbucket_cloud", "bitbucket.org", "acme", "rules"),
        ("https://b.ac.me/scm/arch/rules.git", "bitbucket_server", "b.ac.me", "arch", "rules"),
        ("ssh://git@g.ac.me:7999/arch/rules.git", "bitbucket_server", "g.ac.me", "arch", "rules"),
    ],
)  # fmt: skip
def test_the_remote_names_the_destination(url, kind, host, owner, repo):
    r = open_pr.parse_remote(url)
    assert (r.kind, r.host, r.owner, r.repo) == (kind, host, owner, repo)


def test_an_unknown_host_is_refused_not_guessed():
    with pytest.raises(SystemExit):
        open_pr.parse_remote("https://code.example.com/a/b.git")


def test_bitbucket_server_and_cloud_get_their_own_api():
    server = open_pr.parse_remote("https://b.acme.com/scm/arch/rules.git")
    api, body = open_pr.api_call(server, "t", "d", "rules/x", "main")
    assert api == "https://b.acme.com/rest/api/1.0/projects/arch/repos/rules/pull-requests"
    assert (
        body["fromRef"]["id"] == "refs/heads/rules/x" and body["toRef"]["id"] == "refs/heads/main"
    )
    cloud = open_pr.parse_remote("git@bitbucket.org:acme/rules.git")
    api, body = open_pr.api_call(cloud, "t", "d", "rules/x", "main")
    assert api == "https://api.bitbucket.org/2.0/repositories/acme/rules/pullrequests"
    assert body["destination"]["branch"]["name"] == "main"


def test_the_pull_requests_address_is_read_from_either_answer():
    assert open_pr.url_of({"links": {"html": {"href": "https://c"}}}) == "https://c"
    assert open_pr.url_of({"links": {"self": [{"href": "https://s"}]}}) == "https://s"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(scratch, "root", lambda: tmp_path / "scratch")
    source = tmp_path / "source"
    (source / "a").mkdir(parents=True)
    (source / "b").mkdir()
    (source / "a" / "x.txt").write_text("x")
    (source / "b" / "y.txt").write_text("y")
    git = ["git", "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    return source.as_uri()


def test_a_scratch_clone_holds_only_what_was_asked_and_is_deleted(repo):
    path = scratch.clone(repo, ["a"])
    assert (path / "a" / "x.txt").exists() and not (path / "b").exists()
    scratch.clean(str(path))
    assert not path.exists()


def test_clean_refuses_anything_outside_the_scratch_folder(repo, tmp_path):
    keep = tmp_path / "keep"
    keep.mkdir()
    with pytest.raises(SystemExit):
        scratch.clean(str(keep))
    assert keep.exists()


def test_a_clone_left_behind_is_removed_after_a_day(repo):
    old = scratch.clone(repo, [])
    day_ago = time.time() - 2 * scratch.MAX_AGE_S
    os.utime(old, (day_ago, day_ago))
    new = scratch.clone(repo, [])
    assert not old.exists() and new.exists()
