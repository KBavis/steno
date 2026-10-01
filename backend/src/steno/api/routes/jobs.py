from fastapi import APIRouter
from sqlalchemy import select

from steno.api.deps import SessionDep, get_or_404
from steno.api.schemas import JobCreate, JobDetail, JobOut, StageOut
from steno.db.models import IngestionJob, IngestionStage, Repository
from steno.ingestion.queue import enqueue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(session: SessionDep, limit: int = 50) -> list[IngestionJob]:
    stmt = select(IngestionJob).order_by(IngestionJob.id.desc()).limit(limit)
    return list(session.scalars(stmt))


@router.post("", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, session: SessionDep) -> IngestionJob:
    get_or_404(session, Repository, body.repository_id)
    return enqueue(session, body.repository_id, body.mode)


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: int, session: SessionDep) -> JobDetail:
    job = get_or_404(session, IngestionJob, job_id)
    stages = session.scalars(
        select(IngestionStage).where(IngestionStage.job_id == job_id).order_by(IngestionStage.id)
    )
    return JobDetail(
        **JobOut.model_validate(job).model_dump(),
        stages=[StageOut.model_validate(s) for s in stages],
    )
