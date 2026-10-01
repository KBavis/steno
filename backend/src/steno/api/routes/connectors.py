"""Connectors: a source system plus a scope (D30)."""

from fastapi import APIRouter
from sqlalchemy import select

from steno.api.deps import SessionDep, get_or_404
from steno.api.schemas import ConnectorIn, ConnectorOut
from steno.db.models import Connector

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("", response_model=list[ConnectorOut])
def list_connectors(session: SessionDep) -> list[Connector]:
    return list(session.scalars(select(Connector).order_by(Connector.name)))


@router.post("", response_model=ConnectorOut, status_code=201)
def create_connector(body: ConnectorIn, session: SessionDep) -> Connector:
    connector = Connector(**body.model_dump())
    session.add(connector)
    session.flush()
    return connector


@router.put("/{connector_id}", response_model=ConnectorOut)
def update_connector(connector_id: int, body: ConnectorIn, session: SessionDep) -> Connector:
    connector = get_or_404(session, Connector, connector_id)
    for key, value in body.model_dump().items():
        setattr(connector, key, value)
    session.flush()
    return connector


@router.delete("/{connector_id}", status_code=204)
def delete_connector(connector_id: int, session: SessionDep) -> None:
    """Also deletes the connector's repositories and their jobs."""
    session.delete(get_or_404(session, Connector, connector_id))
