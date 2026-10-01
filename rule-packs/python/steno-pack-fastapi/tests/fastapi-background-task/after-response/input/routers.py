from fastapi import APIRouter, BackgroundTasks
from uuid import UUID

from services import JobService

router = APIRouter(prefix="/jobs")
svc = JobService()


@router.post("/projects/{project_id}")
async def run_project_jobs(project_id: UUID, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        svc.run_project_jobs,
        project_id,
    )
    return {"project_id": str(project_id)}
