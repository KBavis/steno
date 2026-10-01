from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from steno.db.session import get_session
from steno.graph.driver import database, get_driver
from steno.graph.projection import sync_declared

SessionDep = Annotated[Session, Depends(get_session)]


def get_or_404[T](session: Session, model: type[T], id: int) -> T:
    row = session.get(model, id)
    if row is None:
        raise HTTPException(404, f"{model.__tablename__} {id} not found")
    return row


def sync_graph(session: Session) -> None:
    """Project the declared org and spaces into Neo4j (D33).

    Flushes first so Postgres rejects bad data before Neo4j changes. If Neo4j fails, the
    request fails and Postgres rolls back with it, so the two stay in step.
    """
    session.flush()
    session.expire_all()
    sync_declared(session, get_driver(), database())
