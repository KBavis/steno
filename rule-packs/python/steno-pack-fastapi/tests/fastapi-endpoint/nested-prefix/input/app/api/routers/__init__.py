from fastapi import APIRouter

from .job import router as job_router

app_router = APIRouter(prefix="/api")
app_router.include_router(job_router)
