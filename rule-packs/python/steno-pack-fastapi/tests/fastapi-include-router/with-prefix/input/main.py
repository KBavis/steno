from fastapi import APIRouter, FastAPI

api_router = APIRouter()
app = FastAPI()
app.include_router(api_router, prefix="/api/v1")
