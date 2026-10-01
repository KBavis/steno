"""Admin-declared spaces (D27). Postgres is the source of truth; Neo4j gets a copy (D33)."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.api.deps import SessionDep, get_or_404, sync_graph
from steno.api.schemas import SpaceIn, SpaceOut
from steno.db.models import Organization, Space

router = APIRouter(prefix="/spaces", tags=["spaces"])


@router.get("", response_model=list[SpaceOut])
def list_spaces(session: SessionDep) -> list[Space]:
    return list(session.scalars(select(Space).order_by(Space.name)))


@router.post("", response_model=SpaceOut, status_code=201)
def create_space(body: SpaceIn, session: SessionDep) -> Space:
    if session.get(Organization, 1) is None:
        raise HTTPException(409, "create the organization first")
    if body.parent_id is not None:
        get_or_404(session, Space, body.parent_id)
    space = Space(**body.model_dump())
    session.add(space)
    sync_graph(session)
    return space


@router.put("/{space_id}", response_model=SpaceOut)
def update_space(space_id: int, body: SpaceIn, session: SessionDep) -> Space:
    space = get_or_404(session, Space, space_id)
    if body.parent_id is not None:
        get_or_404(session, Space, body.parent_id)
        if _is_self_or_descendant(session, body.parent_id, space_id):
            raise HTTPException(422, "a space can't be moved under itself or its own child")
    for key, value in body.model_dump().items():
        setattr(space, key, value)
    sync_graph(session)
    return space


@router.delete("/{space_id}", status_code=204)
def delete_space(space_id: int, session: SessionDep) -> None:
    """Deletes the space and its child spaces. Its repositories stay, unassigned."""
    session.delete(get_or_404(session, Space, space_id))
    sync_graph(session)


def _is_self_or_descendant(session: Session, candidate_id: int, space_id: int) -> bool:
    current: int | None = candidate_id
    while current is not None:
        if current == space_id:
            return True
        current = session.get_one(Space, current).parent_id
    return False
