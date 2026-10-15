"""Repositories to ingest. Phase 1: an explicit include list (D30)."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.api.deps import SessionDep, get_or_404
from steno.api.schemas import RepositoryIn, RepositoryOut
from steno.connectors.git import CloneError, remote_head
from steno.db.models import Connector, Repository, Space

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=list[RepositoryOut])
def list_repositories(session: SessionDep) -> list[Repository]:
    return list(session.scalars(select(Repository).order_by(Repository.name)))


@router.post("", response_model=RepositoryOut, status_code=201)
def create_repository(body: RepositoryIn, session: SessionDep) -> Repository:
    _check_refs(session, body)
    _check_remote(session, body)
    repository = Repository(**body.model_dump())
    session.add(repository)
    session.flush()
    return repository


@router.put("/{repository_id}", response_model=RepositoryOut)
def update_repository(repository_id: int, body: RepositoryIn, session: SessionDep) -> Repository:
    repository = get_or_404(session, Repository, repository_id)
    _check_refs(session, body)
    # Renaming or moving a repository doesn't need the git host
    if (body.connector_id, body.clone_url, body.default_branch) != (
        repository.connector_id,
        repository.clone_url,
        repository.default_branch,
    ):
        _check_remote(session, body)
    for key, value in body.model_dump().items():
        setattr(repository, key, value)
    session.flush()
    return repository


@router.delete("/{repository_id}", status_code=204)
def delete_repository(repository_id: int, session: SessionDep) -> None:
    """Also deletes the repository's jobs."""
    session.delete(get_or_404(session, Repository, repository_id))


def _check_refs(session: Session, body: RepositoryIn) -> None:
    get_or_404(session, Connector, body.connector_id)
    if body.space_id is not None:
        get_or_404(session, Space, body.space_id)


def _check_remote(session: Session, body: RepositoryIn) -> None:
    """The repository and its default branch exist and the connector's credentials reach them."""
    connector = get_or_404(session, Connector, body.connector_id)
    try:
        head = remote_head(
            body.clone_url, body.default_branch, connector.kind, connector.credentials_ref
        )
    except CloneError as exc:
        # Hosts answer a missing repository and a private one the same way: they ask to log in
        reason = str(exc)
        if "could not read Username" in reason or "Authentication failed" in reason:
            reason = (
                "it doesn't exist, or it's private and the connector's credentials can't read it"
            )
        raise HTTPException(
            422, f"can't reach the repository at {body.clone_url}: {reason}"
        ) from exc
    if head is None:
        raise HTTPException(
            422, f"the repository at {body.clone_url} has no branch {body.default_branch!r}"
        )
