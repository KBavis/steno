"""Adding a repository checks that it and its default branch exist."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from steno.api.routes.repositories import _check_remote
from steno.api.schemas import RepositoryIn
from steno.db.models import ConnectorKind

CONNECTOR = SimpleNamespace(kind=ConnectorKind.GITHUB, credentials_ref=None)
SESSION = SimpleNamespace(get=lambda model, id: CONNECTOR)


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A local repository with one commit on `main`."""
    repo = tmp_path / "remote"
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return repo


def check(clone_url: str, branch: str = "main") -> None:
    body = RepositoryIn(connector_id=1, name="r", clone_url=clone_url, default_branch=branch)
    _check_remote(SESSION, body)  # type: ignore[arg-type]


def test_existing_repository_and_branch(remote: Path) -> None:
    check(str(remote))


def test_missing_branch(remote: Path) -> None:
    with pytest.raises(HTTPException, match="has no branch 'develop'") as exc:
        check(str(remote), "develop")
    assert exc.value.status_code == 422


def test_missing_repository(tmp_path: Path) -> None:
    with pytest.raises(HTTPException, match="can't reach the repository") as exc:
        check(str(tmp_path / "nowhere"))
    assert exc.value.status_code == 422
