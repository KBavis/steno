"""Load the POC declarations file into Postgres (docs/ingestion.md §8).

In the POC, connectors, spaces, and repositories start as a YAML file. Loading is an
upsert by natural key, so running it again after editing the file is safe.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.db.models import Connector, ConnectorKind, Repository, Space


class ConnectorDecl(BaseModel):
    name: str
    kind: ConnectorKind
    base_url: str
    scope: dict[str, Any] = Field(default_factory=dict)
    credentials_ref: str | None = None


class SpaceDecl(BaseModel):
    name: str
    description: str | None = None
    spaces: list["SpaceDecl"] = Field(default_factory=list)


class RepositoryDecl(BaseModel):
    name: str
    connector: str
    space: str = Field(description="Space path, e.g. 'Payroll' or 'Payroll/Adjustments'")
    clone_url: str
    default_branch: str = "main"


class PocConfig(BaseModel):
    organization: str
    connectors: list[ConnectorDecl] = Field(default_factory=list)
    spaces: list[SpaceDecl] = Field(default_factory=list)
    repositories: list[RepositoryDecl] = Field(default_factory=list)


def read_config(path: Path) -> PocConfig:
    return PocConfig.model_validate(yaml.safe_load(path.read_text()))


def load_config(session: Session, config: PocConfig) -> dict[str, int]:
    connectors = {c.name: _upsert_connector(session, c) for c in config.connectors}

    space_ids: dict[str, int] = {}

    def walk(decls: list[SpaceDecl], parent_id: int | None, prefix: str) -> None:
        for decl in decls:
            path = f"{prefix}/{decl.name}" if prefix else decl.name
            space = _upsert_space(session, decl, parent_id)
            space_ids[path] = space.id
            walk(decl.spaces, space.id, path)

    walk(config.spaces, None, "")

    for repo in config.repositories:
        if repo.connector not in connectors:
            raise ValueError(f"repository {repo.name!r}: unknown connector {repo.connector!r}")
        if repo.space not in space_ids:
            raise ValueError(f"repository {repo.name!r}: unknown space {repo.space!r}")
        _upsert_repository(session, repo, connectors[repo.connector].id, space_ids[repo.space])

    return {
        "connectors": len(connectors),
        "spaces": len(space_ids),
        "repositories": len(config.repositories),
    }


def _upsert_connector(session: Session, decl: ConnectorDecl) -> Connector:
    row = session.scalar(select(Connector).where(Connector.name == decl.name))
    if row is None:
        row = Connector(name=decl.name)
        session.add(row)
    row.kind = decl.kind
    row.base_url = decl.base_url
    row.scope = decl.scope
    row.credentials_ref = decl.credentials_ref
    session.flush()
    return row


def _upsert_space(session: Session, decl: SpaceDecl, parent_id: int | None) -> Space:
    parent_match = Space.parent_id.is_(None) if parent_id is None else Space.parent_id == parent_id
    row = session.scalar(select(Space).where(Space.name == decl.name, parent_match))
    if row is None:
        row = Space(name=decl.name, parent_id=parent_id)
        session.add(row)
    row.description = decl.description
    session.flush()
    return row


def _upsert_repository(
    session: Session, decl: RepositoryDecl, connector_id: int, space_id: int
) -> Repository:
    row = session.scalar(
        select(Repository).where(
            Repository.connector_id == connector_id, Repository.name == decl.name
        )
    )
    if row is None:
        row = Repository(connector_id=connector_id, name=decl.name)
        session.add(row)
    row.space_id = space_id
    row.clone_url = decl.clone_url
    row.default_branch = decl.default_branch
    session.flush()
    return row
