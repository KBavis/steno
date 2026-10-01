from fastapi import APIRouter, FastAPI

job_router = APIRouter(prefix="/jobs")
app_router = APIRouter(prefix="/api")
app_router.include_router(job_router)

app = FastAPI()
app.include_router(app_router)
