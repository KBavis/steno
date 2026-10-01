"""Admin API: what the UI uses to see declarations and run ingestion."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.api.schemas import JobCreate, JobDetail, JobOut, RepositoryOut, SpaceOut, StageOut
from steno.db.models import IngestionJob, IngestionStage, Repository, Space
from steno.db.session import get_session
from steno.ingestion.queue import enqueue

router = APIRouter(tags=["admin"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/spaces", response_model=list[SpaceOut])
def list_spaces(session: SessionDep) -> list[Space]:
    return list(session.scalars(select(Space).order_by(Space.name)))


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(session: SessionDep) -> list[Repository]:
    return list(session.scalars(select(Repository).order_by(Repository.name)))


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(session: SessionDep, limit: int = 50) -> list[IngestionJob]:
    stmt = select(IngestionJob).order_by(IngestionJob.id.desc()).limit(limit)
    return list(session.scalars(stmt))


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, session: SessionDep) -> IngestionJob:
    if session.get(Repository, body.repository_id) is None:
        raise HTTPException(404, f"repository {body.repository_id} not found")
    return enqueue(session, body.repository_id, body.mode)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int, session: SessionDep) -> JobDetail:
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(404, f"job {job_id} not found")
    stages = session.scalars(
        select(IngestionStage).where(IngestionStage.job_id == job_id).order_by(IngestionStage.id)
    )
    return JobDetail(
        **JobOut.model_validate(job).model_dump(),
        stages=[StageOut.model_validate(s) for s in stages],
    )
