from fastapi import APIRouter

router = APIRouter(prefix="/jobs", tags=["Jobs"])
plain = APIRouter()
