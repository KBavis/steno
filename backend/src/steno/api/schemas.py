from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from steno.db.models import JobMode, JobStatus, JobTrigger, StageName, StageStatus


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SpaceOut(_Orm):
    id: int
    parent_id: int | None
    name: str
    description: str | None


class RepositoryOut(_Orm):
    id: int
    connector_id: int
    space_id: int | None
    name: str
    clone_url: str
    default_branch: str
    last_ingested_sha: str | None


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
