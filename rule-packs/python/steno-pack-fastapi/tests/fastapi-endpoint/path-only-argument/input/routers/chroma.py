from fastapi import APIRouter
from uuid import UUID

router = APIRouter(prefix="/chroma")


@router.delete("/collection/{project_id}")
def delete_collection(project_id: UUID):
    return None
