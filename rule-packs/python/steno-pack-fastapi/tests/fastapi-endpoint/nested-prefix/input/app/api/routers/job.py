from fastapi import APIRouter, BackgroundTasks, status
from uuid import UUID

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post("/projects/{project_id}", status_code=status.HTTP_202_ACCEPTED, summary="Fan-out one Job per applicable source")
async def run_project_jobs(project_id: UUID, background_tasks: BackgroundTasks):
    return {"project_id": str(project_id)}


@router.get(
    "/{job_id}",
    summary="Get a job",
)
async def get_job(job_id: UUID):
    return {"job_id": str(job_id)}
