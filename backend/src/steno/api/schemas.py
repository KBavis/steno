from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from steno.db.models import ConnectorKind, JobMode, JobStatus, JobTrigger, StageName, StageStatus


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Organization -----------------------------------------------------------


class OrganizationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class OrganizationOut(_Orm):
    name: str
    description: str | None
    onboarded_at: datetime | None


# --- Spaces -----------------------------------------------------------------


class SpaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    parent_id: int | None = None


class SpaceOut(_Orm):
    id: int
    parent_id: int | None
    name: str
    description: str | None


# --- Connectors -------------------------------------------------------------


class ConnectorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: ConnectorKind
    base_url: str = Field(min_length=1)
    # Which part of the host this connector covers, e.g. {"org": "acme"} (D30)
    scope: dict[str, Any] = Field(default_factory=dict)
    # A reference to a secret (e.g. "env:STENO_GITHUB_TOKEN"), never the secret itself
    credentials_ref: str | None = None


class ConnectorOut(_Orm):
    id: int
    name: str
    kind: ConnectorKind
    base_url: str
    scope: dict[str, Any]
    credentials_ref: str | None


# --- Repositories -----------------------------------------------------------


class RepositoryIn(BaseModel):
    connector_id: int
    space_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    clone_url: str = Field(min_length=1)
    default_branch: str = "main"


class RepositoryOut(_Orm):
    id: int
    connector_id: int
    space_id: int | None
    name: str
    clone_url: str
    default_branch: str
    last_ingested_sha: str | None


# --- Jobs -------------------------------------------------------------------


class StageOut(_Orm):
    id: int
    stage: StageName
    status: StageStatus
    started_at: datetime | None
    finished_at: datetime | None
    metrics: dict[str, Any]
    llm_cost: Decimal
    jev_cost: Decimal


class JobOut(_Orm):
    id: int
    repository_id: int
    trigger: JobTrigger
    mode: JobMode
    status: JobStatus
    from_sha: str | None
    to_sha: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    stats: dict[str, Any]


class JobDetail(JobOut):
    stages: list[StageOut]


class JobCreate(BaseModel):
    repository_id: int
    mode: JobMode = JobMode.DRY_RUN
